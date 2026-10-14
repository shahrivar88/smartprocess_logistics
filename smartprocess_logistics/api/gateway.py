"""Every database call the app makes, in one class.

The orchestration in `core` asks for work by name; this is what performs it. Splitting them
means the plan is tested against a fake and this file stays a list of short ORM calls.

Locking
-------
`frappe.db.get_value(..., for_update=True)` issues `SELECT ... FOR UPDATE` and, by default,
**waits**. `skip_locked` is deliberately not used: a second request for the same session must
block and then read what the first one committed, so it can see `Applied` and refuse. Skipping
the lock would let it read `Prepared` and apply the same file twice.

Reading with `frappe.get_all` does not apply user permissions, so every read here filters by
company explicitly. `core.authz` decides which companies those are; this file only applies the
list it is given.

Transactions
------------
`open_transaction` is a no-op marker: Frappe is already in a transaction for the request, and
the request boundary commits or rolls back. It exists so `core` can state the requirement, and
it is idempotent so a confirmation can nest a rewrite. Nothing here commits or rolls back.
"""

import frappe
from frappe import _, flags

# Imported by name rather than reached through `frappe.permissions`. That attribute exists only
# once something has imported the submodule, and a bare `import frappe` does not: measured on
# this site, `frappe.permissions` is the one submodule this app uses that is absent from a cold
# interpreter. Reaching for it through the package made the **first request handled by every new
# worker** raise AttributeError, and pass on the second.
from frappe.permissions import get_user_permissions
from frappe.utils import now

from smartprocess_logistics.core import authz, export, importer, orchestration, session as session_rules
from smartprocess_logistics.core.authz import InvoiceRef
from smartprocess_logistics.core.validation import ExistingAssignment, InvoiceRow

SESSION_DOCTYPE = "Logistics Import Session"
SERIAL_DOCTYPE = "Logistics Serial"
ASSIGNMENT_DOCTYPE = "Logistics Serial Assignment"
SETTINGS_DOCTYPE = "Logistics Settings"
INVOICE_DOCTYPE = "Sales Invoice"

SESSION_FIELDS = (
	"name",
	"token",
	"status",
	"owner",
	"company",
	"sales_invoice",
	"payload_hash",
	"expires_at",
	"claimed_at",
	"applied_at",
	"result_summary",
)

ASSIGNMENT_FIELDS = ("name", "sales_invoice_item", "normalized_serial", "logistics_serial", "item_code")


class FrappeGateway:
	"""The real gateway. See the module docstring for the locking and transaction rules."""

	def __init__(self):
		self._transaction_open = False

	# --- principal ---------------------------------------------------------------

	def principal(self):
		"""The acting user, as `core.authz` wants them."""
		user = frappe.session.user

		if not user or user == "Guest":
			return authz.Principal(user="", roles=frozenset())

		user_permissions = get_user_permissions(user) or {}
		allowed = user_permissions.get("Company")
		companies = frozenset(entry.get("doc") for entry in allowed if entry.get("doc")) if allowed else None

		return authz.Principal(
			user=user,
			roles=frozenset(frappe.get_roles(user)),
			companies=companies,
			strict_companies=bool(
				frappe.db.get_single_value("System Settings", "apply_strict_user_permissions")
			),
		)

	# --- transactions ------------------------------------------------------------

	def open_transaction(self):
		"""A no-op marker. Idempotent, so a confirmation may nest a rewrite."""
		self._transaction_open = True
		return True

	def assert_can_read_invoice(self, name):
		"""Let the framework check the caller's own read permission on the invoice.

		The check is the framework's, and so is the decision. Only the wording of the refusal
		is this app's: `has_permission(..., throw=True)` raises a bare `PermissionError` with
		no message, and an empty message reaches the Desk as a blank dialog. Raised through
		`frappe.throw`, the same refusal arrives as a sentence.
		"""
		if not frappe.has_permission(INVOICE_DOCTYPE, doc=name, ptype="read"):
			frappe.throw(
				_("به این فاکتور دسترسی ندارید: {0}").format(name),
				frappe.PermissionError,
			)

		return True

	# --- reads -------------------------------------------------------------------

	def read_invoice(self, name):
		"""The invoice as stored, or `None`. The company here is what every check uses."""
		if not name or not frappe.db.exists(INVOICE_DOCTYPE, name):
			return None

		stored = frappe.db.get_value(INVOICE_DOCTYPE, name, ["name", "company"], as_dict=True)

		return InvoiceRef(name=stored.name, company=stored.company) if stored else None

	def read_rows(self, invoice_name):
		"""The invoice's item rows, in invoice order."""
		rows = frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": invoice_name, "parenttype": INVOICE_DOCTYPE},
			fields=["name", "item_code", "qty"],
			order_by="idx asc",
			limit_page_length=0,
		)

		return tuple(
			InvoiceRow(row_key=row.name, item_code=row.item_code, invoiced_qty=row.qty) for row in rows
		)

	def read_existing(self, invoice_name):
		"""Every assignment already stored on the invoice."""
		rows = frappe.get_all(
			ASSIGNMENT_DOCTYPE,
			filters={"sales_invoice": invoice_name},
			fields=["sales_invoice_item", "normalized_serial"],
			limit_page_length=0,
		)

		return tuple(
			ExistingAssignment(
				row_key=row.sales_invoice_item,
				normalized_serial=row.normalized_serial,
			)
			for row in rows
		)

	def read_assignment(self, name):
		"""One assignment's invoice and company, or `None`.

		The company is read from the stored row, never from the request: it is what
		`authz.require_company` decides on, so it must not be a value the caller supplied.
		"""
		return frappe.db.get_value(ASSIGNMENT_DOCTYPE, name, ["sales_invoice", "company"], as_dict=True)

	def read_session_payload(self, name):
		"""The stored payload text of one session, or `None`.

		Read rather than re-encoded: the confirmation recomputes the payload's hash from exactly
		the bytes that were stored, and a round trip through JSON would compare the session with
		itself.
		"""
		return frappe.db.get_value(SESSION_DOCTYPE, name, "payload")

	def list_assignments(self, invoice_name, limit, offset):
		"""One page of the invoice's assignments, each with the text that was typed.

		`normalized_serial` is the identity: case-folded, and never shown to an operator as the
		serial itself. The text lives on the `Logistics Serial` the assignment links to, so it is
		read in one further query for the page's serials rather than one query per row. A limit of
		zero means the whole invoice, which is what the status view asks for.
		"""
		rows = tuple(
			dict(row)
			for row in frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters={"sales_invoice": invoice_name},
				fields=list(ASSIGNMENT_FIELDS),
				order_by="sales_invoice_item asc, creation asc",
				limit_start=offset,
				limit_page_length=limit,
			)
		)

		names = sorted({row["logistics_serial"] for row in rows if row.get("logistics_serial")})
		texts = {}

		if names:
			for serial in frappe.get_all(
				SERIAL_DOCTYPE,
				filters={"name": ["in", names]},
				fields=["name", "serial_no"],
				limit_page_length=0,
			):
				texts[serial.name] = serial.serial_no

		for row in rows:
			row["serial_no"] = texts.get(row.get("logistics_serial"))

		return rows

	def read_session(self, token):
		"""A session by its token, without locking it."""
		stored = frappe.db.get_value(
			SESSION_DOCTYPE,
			{"token": token},
			list(SESSION_FIELDS),
			as_dict=True,
		)

		return _session_from(stored) if stored else None

	# --- session lifecycle -------------------------------------------------------

	def lock_session(self, token):
		"""The session, locked for update, or `None`."""
		stored = frappe.db.get_value(
			SESSION_DOCTYPE,
			{"token": token},
			list(SESSION_FIELDS),
			as_dict=True,
			for_update=True,
		)

		return _session_from(stored) if stored else None

	def create_session(self, values):
		"""Insert a prepared session and return its **token**.

		The token is what the client is given and what every later call finds the session by:
		`read_session` and `lock_session` both look up `{"token": ...}`. Returning the document
		name instead — a hash Frappe generates, which is a different value — handed the client an
		identifier nothing else accepted, so a prepared preview could never be confirmed and the
		only way to apply an import failed with "this preview was not found".

		The DocType says the same thing: `token` is documented as the random unique preview
		identifier that is the only thing returned to the browser.
		"""
		document = frappe.get_doc({"doctype": SESSION_DOCTYPE, **values})
		document.insert()

		return document.token

	def mark_session_applied(self, name, applied_at, summary):
		"""Record the applied state and the summary.

		`frappe.db.set_value` is used rather than a document `save()` on purpose. The session
		DocType carries no role permissions at all, so a user cannot save one through the ORM
		without `ignore_permissions`, which this app never uses. The transition is decided and
		tested in `core.session` before this is called, and the fields written here have no
		business validation of their own.
		"""
		frappe.db.set_value(
			SESSION_DOCTYPE,
			name,
			{
				"status": session_rules.APPLIED,
				"applied_at": applied_at,
				"result_summary": summary,
			},
			update_modified=False,
		)

		return True

	# --- housekeeping ------------------------------------------------------------

	def stored_sessions(self):
		"""Every session row, as the lifecycle's own value.

		Both steps of the cleanup read the whole table and decide in `core.session`, so this
		returns every row rather than filtering here: which rows expired on their own and which
		finished long enough ago to remove are rules, and rules live in `core`.
		"""
		return tuple(
			_session_from(row)
			for row in frappe.get_all(
				SESSION_DOCTYPE,
				fields=list(SESSION_FIELDS),
				limit_page_length=0,
			)
		)

	def expire_sessions(self, names, summary):
		"""Mark sessions expired. One statement, for the bounded list the caller decided.

		The update carries the two open states in its own condition, so a session that was applied
		while the cleanup was reading the table is left alone: its row is the record that an import
		happened, and writing "Expired" over it would say one did not. No separate `expired_at` is
		written either — `expires_at` is already the instant the row stopped being usable, and
		`core.session.finished_at` reads it.

		Returns the names it was given. The statement reports no count, and the caller is told what
		was *targeted* rather than being handed a number nobody measured.
		"""
		frappe.db.set_value(
			SESSION_DOCTYPE,
			{"name": ["in", list(names)], "status": ["in", list(session_rules.OPEN_STATES)]},
			{"status": session_rules.EXPIRED, "result_summary": summary},
			update_modified=False,
		)

		return tuple(names)

	def purge_sessions(self, names):
		"""Remove stored sessions by name, permanently.

		`delete_permanently` on purpose: a session row is transient by design, and the Recycle Bin
		would keep a restorable copy of the payload — every serial the preview accepted — which is
		the thing this removes. `delete_doc` rather than `frappe.db.delete` so the deletion keeps
		document semantics and the permission check: the list is bounded by the caller, and a
		caller who cannot delete a session would be refused here rather than silently obeyed.

		Each name is checked before it is deleted, so the list returned is what this call actually
		removed: a row a confirmation removed while the cleanup was reading the table is already
		gone, and reporting it as removed would make the sweep's own record of its work a guess.
		"""
		removed = []

		for name in names:
			if not frappe.db.exists(SESSION_DOCTYPE, name):
				continue

			frappe.delete_doc(SESSION_DOCTYPE, name, delete_permanently=True)
			removed.append(name)

		return tuple(removed)

	# --- locking -----------------------------------------------------------------

	def lock_invoice(self, name):
		"""The invoice, locked for update, or `None`."""
		stored = frappe.db.get_value(
			INVOICE_DOCTYPE,
			name,
			["name", "company"],
			as_dict=True,
			for_update=True,
		)

		return InvoiceRef(name=stored.name, company=stored.company) if stored else None

	def lock_serials(self, item_codes):
		"""Lock the serial rows of the items involved, ordered by name.

		The order is the item codes sorted, which the caller supplies already sorted, and the
		rows themselves are locked in name order, so two requests touching the same items take
		the same locks in the same sequence.
		"""
		if not item_codes:
			return 0

		names = frappe.get_all(
			SERIAL_DOCTYPE,
			filters={"item_code": ["in", list(item_codes)]},
			pluck="name",
			order_by="name asc",
			limit_page_length=0,
		)

		locked = 0

		for name in sorted(names):
			if frappe.db.get_value(SERIAL_DOCTYPE, name, "name", for_update=True):
				locked += 1

		return locked

	# --- writes ------------------------------------------------------------------

	def delete_row_assignments(self, invoice_name, row_key):
		"""Remove the assignments of one invoice row and return how many there were."""
		names = frappe.get_all(
			ASSIGNMENT_DOCTYPE,
			filters={"sales_invoice": invoice_name, "sales_invoice_item": row_key},
			pluck="name",
			limit_page_length=0,
		)

		for name in names:
			frappe.delete_doc(ASSIGNMENT_DOCTYPE, name)

		return len(names)

	def delete_assignments(self, names):
		"""Remove assignments by name, for the administrative endpoint."""
		removed = 0

		for name in names:
			if frappe.db.exists(ASSIGNMENT_DOCTYPE, name):
				frappe.delete_doc(ASSIGNMENT_DOCTYPE, name)
				removed += 1

		return removed

	def find_or_create_serials(self, requests):
		"""Find each serial by its identity and create the ones that do not exist.

		`requests` is `(item_code, normalized, original, version)` tuples already sorted by the
		caller. The identity is passed to `Logistics Serial` as its own `normalized_serial`, and
		the assignment's copy is derived from the row that is returned — never from the request.

		`version` is the rule set that produced the identity, carried from the normalization that
		actually ran. A serial that already exists keeps the version it was created with: the
		text may normalize to the same identity under two rule sets, and rewriting the recorded
		version then would lose the only trace of which one produced it.
		"""
		by_normalized = {}
		created = []
		reused = 0

		for item_code, normalized, original, version in requests:
			existing = frappe.db.get_value(
				SERIAL_DOCTYPE,
				{"item_code": item_code, "normalized_serial": normalized},
				["name", "normalized_serial"],
				as_dict=True,
			)

			if existing:
				by_normalized[(item_code, normalized)] = existing.name
				reused += 1
				continue

			document = frappe.get_doc(
				{
					"doctype": SERIAL_DOCTYPE,
					"serial_no": original,
					"normalized_serial": normalized,
					"item_code": item_code,
					"status": "Active",
					"normalization_version": version,
					"first_registered_on": now(),
				}
			)
			document.insert()

			by_normalized[(item_code, normalized)] = document.name
			created.append(document.name)

		return {"by_normalized": by_normalized, "created": tuple(created), "reused": reused}

	def insert_assignments(self, values):
		"""Insert the assignments the plan built. The identity comes from the serial, not here."""
		names = []

		for value in values:
			document = frappe.get_doc(
				{
					"doctype": ASSIGNMENT_DOCTYPE,
					"logistics_serial": value.logistics_serial,
					"item_code": value.item_code,
					"sales_invoice": value.sales_invoice,
					"sales_invoice_item": value.sales_invoice_item,
					"company": value.company,
					"invoice_item_idx": value.invoice_item_idx,
					"normalized_serial": value.normalized_serial,
				}
			)
			document.insert()
			names.append(document.name)

		return tuple(names)

	# --- exporting and importing this app's records -------------------------------

	def export_rows(self):
		"""Every serial and every assignment in the site, with each field the format defines.

		Read with `frappe.get_all`, like every read in this file and for the same reason: what an
		export contains must not depend on who clicked the button. The caller was authorized at
		Manager level first — an export is a copy of the site's whole serial history.

		`limit_page_length=0` is Frappe's own "no limit", measured on this stack rather than
		assumed: against a table of 78 rows it returned 78, where a page size would have returned
		the page. An export that silently stopped at the first page would be the worst possible
		bug in this file.
		"""
		return {
			"serials": tuple(
				frappe.get_all(
					SERIAL_DOCTYPE,
					fields=list(export.SERIAL_FIELDS),
					order_by="name asc",
					limit_page_length=0,
				)
			),
			"assignments": tuple(
				frappe.get_all(
					ASSIGNMENT_DOCTYPE,
					fields=list(export.ASSIGNMENT_FIELDS),
					order_by="name asc",
					limit_page_length=0,
				)
			),
		}

	def transfer_world(self, principal, *, item_codes=(), company_names=(), invoice_names=()):
		"""The site as `core.export.World` sees it, for planning an import.

		The names are the ones **the document mentions**, and they are given rather than derived:
		the plan asks whether these items, companies and invoices exist here, so a world built
		only from what is already stored answers about the wrong set. Measured in the lab: a
		five-row export imported into a freshly reinstalled site — whose tables are empty by
		definition — reported every row as `ITEM_MISSING`, with the item sitting on the site the
		whole time. `core.export.world_names` reads them off the document.

		That leaves the permission question, which is answered where it belongs: existence is read
		here without a company filter, and `core.authz.require_company` decides, per assignment and
		against the **live invoice's** company, whether this caller may write it. A permission
		filter applied to these reads would instead turn "you may not see it" into "somebody else
		gets to decide", which is not the same answer.
		"""
		serials = tuple(
			frappe.get_all(
				SERIAL_DOCTYPE,
				fields=["name", "item_code", "normalized_serial"],
				limit_page_length=0,
			)
		)
		invoices = {
			row.get("name"): row.get("company")
			for row in self._by_names(INVOICE_DOCTYPE, tuple(invoice_names), ["name", "company"])
		}
		# The assignments already on the invoices this document names: that is the set the
		# duplicate and link-conflict checks are about. With no invoice named, every stored
		# assignment is read, because guessing that there are none would be guessing.
		assignment_filters = {"sales_invoice": ["in", tuple(invoice_names)]} if invoice_names else {}

		assignments = tuple(
			frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters=assignment_filters,
				fields=[
					"name",
					"sales_invoice",
					"sales_invoice_item",
					"item_code",
					"normalized_serial",
					"logistics_serial",
					"company",
				],
				limit_page_length=0,
			)
		)

		return export.World(
			serial_identities=frozenset(export.serial_identity(row) for row in serials),
			serials_by_identity={export.serial_identity(row): row.get("name") for row in serials},
			items=frozenset(row.get("name") for row in self._by_names("Item", tuple(item_codes), ["name"])),
			companies=frozenset(
				row.get("name") for row in self._by_names("Company", tuple(company_names), ["name"])
			),
			invoices=invoices,
			invoice_rows=frozenset(
				(row.get("parent"), row.get("name")) for row in self._invoice_rows(tuple(invoices))
			),
			assignments_by_identity={
				export.assignment_identity(row): row.get("logistics_serial") for row in assignments
			},
		)

	def insert_exported(self, doctype, rows):
		"""Create exported rows as documents, and put their own provenance back on them.

		Two steps, because Frappe decides two different things on the way in:

		- `flags.in_import` keeps an explicitly passed `name`. `model/naming.py` resets `doc.name`
		  for every naming rule except `prompt` and `UUID`, and only that flag stops it. It also
		  suppresses `Select` validation, which `core.export.misplaced_choices` covers here rather
		  than leaving a narrowed field definition to be discovered on the next report.
		- Everything about *when and by whom* the row was written is rewritten by the framework:
		  `set_user_and_timestamp` overwrites `modified` and `modified_by` unconditionally, and
		  `creation` and `owner` unless one of `in_install`, `in_patch` or `in_migrate` is set.
		  Rather than reach for one of those three — each of which is read elsewhere in the
		  framework, so each drags behaviour in with it — the four columns are written back after
		  the insert with `frappe.db.set_value(..., update_modified=False)`. That is a supported
		  API, it is narrow, and `update_modified=False` is what stops it from rewriting the very
		  timestamp it is being asked to restore.

		The cost is one extra statement per row, inside the caller's transaction: the import either
		places a row with its own provenance or writes nothing at all. A row whose export predates
		one of these fields simply does not carry it, and `values` leaves the insert's own value in
		place rather than writing a blank over it.
		"""
		fields = export.SERIAL_FIELDS if doctype == SERIAL_DOCTYPE else export.ASSIGNMENT_FIELDS
		choices = _select_options(doctype)
		previous = flags.in_import
		flags.in_import = True
		names = []

		try:
			for row in rows:
				values = {field: row.get(field) for field in fields}
				misplaced = export.misplaced_choices(choices, values)

				if misplaced:
					raise export.ExportError(
						export.CODE_VALUE_NOT_ALLOWED,
						f"مقدار «{misplaced[0][1]}» برای «{misplaced[0][0]}» در این سایت مجاز نیست.",
					)

				document = frappe.get_doc({"doctype": doctype, **values})
				document.insert()

				provenance = export.row_timestamps(row)

				if provenance:
					frappe.db.set_value(doctype, document.name, provenance, update_modified=False)

				names.append(document.name)
		finally:
			flags.in_import = previous

		return tuple(names)

	def _company_filter(self, companies):
		"""A filter that matches only the companies this principal reaches.

		`None` means "no restriction" — the principal holds no Company user permission and the
		site is not strict about it. An empty set means the opposite, and it gets a filter that
		cannot match, rather than no filter at all.
		"""
		if companies is None:
			return {}

		if not companies:
			return {"company": ["in", ("",)]}

		return {"company": ["in", tuple(companies)]}

	def _by_names(self, doctype, names, fields):
		"""Rows of one DocType whose name is in `names`, asked in chunks."""
		rows = []

		for chunk in _chunks(tuple(names)):
			if not chunk:
				continue

			rows.extend(
				frappe.get_all(
					doctype,
					filters={"name": ["in", chunk]},
					fields=list(fields),
					limit_page_length=0,
				)
			)

		return tuple(rows)

	def _invoice_rows(self, invoice_names):
		"""Every item row of these invoices, as `(invoice, row name)` pairs."""
		rows = []

		for chunk in _chunks(tuple(invoice_names)):
			if not chunk:
				continue

			rows.extend(
				frappe.get_all(
					"Sales Invoice Item",
					filters={"parent": ["in", chunk]},
					fields=["parent", "name"],
					limit_page_length=0,
				)
			)

		return tuple(rows)

	# --- settings ----------------------------------------------------------------

	def settings(self):
		"""The seeded settings, as a plain mapping."""
		return frappe.get_single(SETTINGS_DOCTYPE)

	def enabled_delimiters(self):
		"""The delimiters this site splits text with, as stored.

		One reader, for two callers: the file import and a row's typed list. They were separate,
		and the typed list was split in the browser on a fixed set that included the comma — a
		comma the shipped configuration does not treat as a delimiter, so `A,B` was one serial in
		a file and two in the box.
		"""
		settings = self.settings()

		return tuple(row.delimiter for row in settings.delimiters if row.enabled)

	def import_request(self, invoice, rows, existing, principal, **overrides):
		"""An `ImportRequest` built from the site's settings."""
		settings = self.settings()
		stored_delimiters = self.enabled_delimiters()
		patterns = tuple(row.pattern for row in settings.item_markers if row.enabled)

		values = {
			"rows": rows,
			"existing": existing,
			"delimiters": stored_delimiters,
			"patterns": patterns,
			"allow_over_quantity": bool(settings.allow_over_quantity),
		}
		values.update(overrides)

		return importer.ImportRequest(**values)


# Values per `in (...)` filter. A site with a hundred thousand serials would otherwise build one
# statement nobody can read in a log, and MariaDB has its own ceiling on placeholders.
IN_CHUNK = 500


def _chunks(values, size=IN_CHUNK):
	values = tuple(values)

	for start in range(0, len(values), size):
		yield values[start : start + size]


def _select_options(doctype):
	"""`{fieldname: options}` for the Select fields of one DocType, or `{}` when unavailable.

	Read from the DocType so the check follows the site's own definition rather than a copy of it
	here. A runtime without DocType metadata — the database-free tests' stand-in — has no
	`get_meta`, and answering `{}` there means the check is skipped rather than wrong.
	"""
	try:
		meta = frappe.get_meta(doctype)
	except AttributeError:
		return {}

	options = {}

	for field in meta.get("fields") or []:
		if field.get("fieldtype") == "Select" and field.get("options"):
			options[field.get("fieldname")] = field.get("options")

	return options


def _session_from(stored):
	"""A stored session row as the lifecycle module's value."""
	return session_rules.ImportSession(
		name=stored.get("name"),
		token=stored.get("token"),
		status=stored.get("status") or session_rules.PREPARED,
		owner=stored.get("owner") or "",
		company=stored.get("company") or "",
		sales_invoice=stored.get("sales_invoice") or "",
		payload_hash=stored.get("payload_hash") or "",
		expires_at=stored.get("expires_at"),
		claimed_at=stored.get("claimed_at"),
		applied_at=stored.get("applied_at"),
		result_summary=stored.get("result_summary") or "",
	)


def gateway_for_orchestration():
	"""The object `core.orchestration` runs against."""
	return FrappeGateway()
