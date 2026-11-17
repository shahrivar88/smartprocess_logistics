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

from smartprocess_logistics.core import (
	authz,
	delivery as delivery_rules,
	export,
	importer,
)
from smartprocess_logistics.core import (
	session as session_rules,
)
from smartprocess_logistics.core import (
	transfer as transfer_rules,
)
from smartprocess_logistics.core.authz import InvoiceRef
from smartprocess_logistics.core.quantities import required_units
from smartprocess_logistics.core.transfer import SourceSerial, TargetRow
from smartprocess_logistics.core.validation import ExistingAssignment, InvoiceRow

SESSION_DOCTYPE = "Logistics Import Session"
SERIAL_DOCTYPE = "Logistics Serial"
ASSIGNMENT_DOCTYPE = "Logistics Serial Assignment"
SETTINGS_DOCTYPE = "Logistics Settings"
INVOICE_DOCTYPE = "Sales Invoice"
DELIVERY_DOCTYPE = "Delivery Note"
DELIVERY_ITEM_DOCTYPE = "Delivery Note Item"
TRANSFER_DOCTYPE = "Logistics Serial Transfer"
TRANSFER_ITEM_DOCTYPE = "Logistics Serial Transfer Item"

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

ASSIGNMENT_FIELDS = (
	"name",
	"sales_invoice_item",
	"normalized_serial",
	"logistics_serial",
	"item_code",
	"delivery_note",
	"delivery_note_item",
)

# The format's field list per DocType, for `insert_exported`. A doctype that is not here cannot be
# placed, and saying so beats the old two-way conditional, which treated anything that was not a
# serial as an assignment and inserted whichever fields that happened to match.
EXPORTED_FIELDS = {
	export.SERIAL_DOCTYPE: export.SERIAL_FIELDS,
	export.ASSIGNMENT_DOCTYPE: export.ASSIGNMENT_FIELDS,
	export.TRANSFER_DOCTYPE: export.TRANSFER_FIELDS,
	export.TRANSFER_ITEM_DOCTYPE: export.TRANSFER_ITEM_FIELDS,
}


def principal_for(user):
	"""The acting user, as `core.authz` wants them.

	Module-level rather than a method because the framework hands a permission condition
	(`permissions.py`) a bare user name and expects the same principal this app authorizes with.
	Two builders would be two chances to disagree about what a company restriction means, and the
	day they disagreed the Desk list and the export endpoint would give different answers for the
	same person.

	The company list is the framework's own: `User Permission` rows on `Company`, and
	`apply_strict_user_permissions` for what their absence means. Neither is this app's policy to
	invent.
	"""
	if not user or user == "Guest":
		return authz.Principal(user="", roles=frozenset())

	user_permissions = get_user_permissions(user) or {}
	allowed = user_permissions.get("Company")
	companies = frozenset(entry.get("doc") for entry in allowed if entry.get("doc")) if allowed else None

	return authz.Principal(
		user=user,
		roles=frozenset(frappe.get_roles(user)),
		companies=companies,
		strict_companies=bool(frappe.db.get_single_value("System Settings", "apply_strict_user_permissions")),
	)


class FrappeGateway:
	"""The real gateway. See the module docstring for the locking and transaction rules."""

	def __init__(self):
		self._transaction_open = False

	# --- principal ---------------------------------------------------------------

	def principal(self):
		"""The acting user, as `core.authz` wants them."""
		return principal_for(frappe.session.user)

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
		"""The invoice as stored, or `None`. The company and the lifecycle are what every check uses.

		`docstatus` is read with the company because it is the other fact a decision needs and the
		other fact a caller must not supply: whether the site has cancelled this invoice decides
		whether a registration may be added to it at all (`core.authz.require_live_invoice`).
		"""
		if not name or not frappe.db.exists(INVOICE_DOCTYPE, name):
			return None

		stored = frappe.db.get_value(INVOICE_DOCTYPE, name, ["name", "company", "docstatus"], as_dict=True)

		if not stored:
			return None

		return InvoiceRef(name=stored.name, company=stored.company, docstatus=int(stored.docstatus or 0))

	def item_facts(self, item_codes):
		"""What the station needs about these items, in one read per chunk.

		Two answers, from the same read so a column's title costs no further query:

		- **Whether the item requires serials.** Read as the flag stands now, which is what the
		  shortage report reads too, so the two cannot disagree about which columns are required.
		  An item absent from the answer is one this site does not mark.
		- **The model to show at the head of the column.** `Item.item_name`, which is where this
		  site keeps it — measured rather than assumed: its invoice items carry values such as
		  `DS-2CD2166G2-ISU (2.8mm)` there, while `crm_product_code` merely repeats the item code,
		  no brand is set, and no item variant attribute exists. The caller falls back to the item
		  code when this is empty, so a site that keeps its model somewhere else still gets a
		  usable title rather than a blank one.
		"""
		names = tuple(sorted({code for code in item_codes if code}))

		if not names:
			return {}

		facts = {}

		for row in self._by_names("Item", names, ["name", "item_name", "sp_serial_required"]):
			facts[row.get("name")] = {
				"model": row.get("item_name") or "",
				"serial_required": bool(row.get("sp_serial_required")),
			}

		return facts

	def read_amendment(self, source_name, target_name):
		"""Whether the target invoice is the amendment of the source, from the stored document.

		One field, read on its own, because it is the fact that makes the pair a pair: two
		unrelated invoices can carry the same item in the same company, and a transfer between
		them would be a claim about a transaction that never happened. The rule it feeds lives in
		`core.transfer.plan_transfer`, where a test can reach it.
		"""
		if not source_name or not target_name:
			return False

		return frappe.db.get_value(INVOICE_DOCTYPE, target_name, "amended_from") == source_name

	def read_source_serials(self, invoice_name):
		"""The registrations the cancelled invoice holds, in the order they were created.

		`name asc` and not `creation asc`: assignment names come from the expression
		`LSA-.YYYY.-.#####`, so name order is creation order, and it is the order
		`export_serials` reads the same rows in. Two paths that order one table two ways are two
		paths that pair a serial with a different row when the table is only half filled.

		The identity comes from the **serial master**, not from the registration's copy of it:
		that is the value `core.assignment.derive_assignment_values` will write on the new row,
		and matching on the value that will be written is what keeps the plan and the write
		describing the same serial. For every row this app ever wrote the two are equal — the
		copy is derived and the identity is immutable — so the distinction only appears in data
		something else wrote.

		A registration whose serial master is missing is skipped rather than refused. Nothing in
		this app can produce one (a serial is never deleted, and the assignment refuses to lose its
		link), and a serial that is gone cannot be moved: there would be no identity to derive and
		nothing to point the new row at.
		"""
		rows = tuple(
			frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters={"sales_invoice": invoice_name},
				fields=[
					"name",
					"sales_invoice_item",
					"item_code",
					"logistics_serial",
				],
				order_by="name asc",
				limit_page_length=0,
			)
		)

		if not rows:
			return ()

		masters = {
			row.get("name"): row
			for row in self._by_names(
				SERIAL_DOCTYPE,
				tuple({row.get("logistics_serial") for row in rows if row.get("logistics_serial")}),
				["name", "serial_no", "normalized_serial", "normalization_version"],
			)
		}
		index_by_row = {row.get("name"): row.get("idx") for row in self._invoice_rows((invoice_name,))}
		serials = []

		for row in rows:
			master = masters.get(row.get("logistics_serial"))

			if not master:
				continue

			serials.append(
				SourceSerial(
					assignment=row.get("name"),
					serial=master.get("serial_no") or "",
					normalized=master.get("normalized_serial") or "",
					item_code=row.get("item_code") or "",
					row_key=row.get("sales_invoice_item") or "",
					row_index=index_by_row.get(row.get("sales_invoice_item")),
					serial_ref=master.get("name") or "",
					version=int(master.get("normalization_version") or 0),
				)
			)

		return tuple(serials)

	def read_target_rows(self, invoice_name):
		"""The amended invoice's item rows, each with what it can still take.

		`read_rows` is the whole invoice in the invoice's own order, eligible or not, and
		`read_existing` is what it already holds; the capacity is the row's own invoiced quantity,
		by the same rule every write path enforces. Three facts from three reads this gateway
		already makes for other endpoints, so the transfer cannot disagree with them about which
		rows may receive a registration or how many fit.

		The count is taken over `ExistingAssignment` objects rather than through `_count_by_row`,
		which reads the row *dicts* the import path has in hand: those carry the invoice they
		belong to, these do not, because they were read for one invoice and one invoice only. The
		dictionary is keyed by the row alone, and a page of a fifty-row invoice is still one read.
		"""
		rows = self.read_rows(invoice_name)
		held = {}

		for existing in self.read_existing(invoice_name):
			held[existing.row_key] = held.get(existing.row_key, 0) + 1

		return tuple(
			TargetRow(
				row_key=row.row_key,
				item_code=row.item_code,
				capacity=transfer_rules.capacity_of(row.invoiced_qty),
				serial_required=row.serial_required,
				held=held.get(row.row_key, 0),
				index=index,
			)
			for index, row in enumerate(rows, start=1)
		)

	def read_live_elsewhere(self, principal, normalized_serials, target_invoice):
		"""Which of these identities are registered on another invoice that is not cancelled.

		Not a conflict and never a refusal on its own: this app has had no site-wide uniqueness
		rule over serial text across invoices since it began, and the three identities that are
		already on two invoices on the operator's own site are exactly that. The answer becomes a
		warning the operator has to acknowledge, and the acknowledgment is what the record keeps.

		Restricted to the caller's companies, in the read itself. An unrestricted answer would
		tell a restricted operator which serials another company's invoices carry, and the
		acknowledgment prompt would be asking about rows they cannot see.

		The invoices are read for their lifecycle rather than trusted to be live, because a serial
		sitting on a *cancelled* invoice is not somewhere it is still in use — that is the whole
		reason the source invoice's own serials are transferable.
		"""
		wanted = tuple(sorted({value for value in normalized_serials if value}))

		if not wanted:
			return frozenset()

		companies = authz.visible_companies(principal)
		company_filter = self._company_filter(companies)
		pairs = []

		for chunk in _chunks(wanted):
			pairs.extend(
				frappe.get_all(
					ASSIGNMENT_DOCTYPE,
					filters={"normalized_serial": ["in", chunk], **company_filter},
					fields=["normalized_serial", "sales_invoice"],
					limit_page_length=0,
				)
			)

		others = {row.get("sales_invoice") for row in pairs} - {target_invoice}

		if not others:
			return frozenset()

		cancelled = {
			row.get("name")
			for row in self._by_names(INVOICE_DOCTYPE, tuple(others), ["name", "docstatus"])
			if int(row.get("docstatus") or 0) == authz.DOCSTATUS_CANCELLED
		}

		return frozenset(
			row.get("normalized_serial")
			for row in pairs
			if row.get("sales_invoice") != target_invoice and row.get("sales_invoice") not in cancelled
		)

	def read_rows(self, invoice_name):
		"""The invoice's item rows, in invoice order, each with its item's eligibility.

		**Every row, eligible or not, and in the invoice's own order.** Which rows may be worked on
		is a decision for `core.eligibility`, and a reader that dropped the rest would leave the
		caller unable to tell "this invoice has three rows and one of them cannot take serials"
		from "this invoice has two rows" — the numbers an operator reads off a printed invoice are
		the positions in *this* list, so the list has to be the whole invoice. It is also what lets
		`core.validation` answer a request that names an ineligible row instead of refusing it as
		one that does not exist.

		The flag comes from the item, in one extra read per distinct item — the same read the
		station's own status call makes, through the same method, so the window and the write path
		cannot end up disagreeing about which rows are serial rows.
		"""
		rows = frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": invoice_name, "parenttype": INVOICE_DOCTYPE},
			fields=["name", "item_code", "qty"],
			order_by="idx asc",
			limit_page_length=0,
		)

		facts = self.item_facts(row.item_code for row in rows)

		return tuple(
			InvoiceRow(
				row_key=row.name,
				item_code=row.item_code,
				invoiced_qty=row.qty,
				serial_required=bool((facts.get(row.item_code) or {}).get("serial_required")),
			)
			for row in rows
		)

	def read_existing(self, invoice_name, for_update=False):
		"""Every assignment already stored on the invoice, as a current read when asked.

		`for_update` is not decoration. This read is the count the capacity rule decides on, and a
		plain read under `REPEATABLE-READ` answers with the view the transaction had when it first
		looked, not with what the last writer committed. Measured in the lab, with the reading and
		the writing deliberately separated: a request that read a row while it held two of its
		three places, waited, and then wrote was **let through the cap** on that older count, and
		was stopped only afterwards by an unrelated deadlock on the framework's own naming series.
		A row that stays within its quantity because a deadlock happened to occur is not a rule.

		A locking read returns the latest committed rows whatever the transaction's view was, so
		the count is the count that exists when the decision is made. It is called from under the
		invoice lock, and in the lock order the plan declares — the invoice, then the serials, then
		the assignments — so it adds no cycle.
		"""
		if for_update:
			rows = frappe.db.get_values(
				ASSIGNMENT_DOCTYPE,
				filters={"sales_invoice": invoice_name},
				fieldname=["sales_invoice_item", "normalized_serial"],
				as_dict=True,
				for_update=True,
			)
		else:
			rows = frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters={"sales_invoice": invoice_name},
				fields=["sales_invoice_item", "normalized_serial"],
				limit_page_length=0,
			)

		return tuple(
			ExistingAssignment(
				row_key=row.get("sales_invoice_item"),
				normalized_serial=row.get("normalized_serial"),
			)
			for row in rows
		)

	def delivery_candidates(self, invoice_name, row_keys):
		"""Every Delivery Note row that carries, or reverses, one of this invoice's rows.

		Returns one entry per invoice row, **empty tuple included**, because the validator
		refuses a mapping that skipped a row: a row nobody looked up must not be read as a row
		with no delivery. A row with no delivery is written provisionally; a row whose delivery
		exists and was not read would be written provisionally too, and would then sit outside
		the confirmed-delivery status for no reason an operator could see.

		Three facts about the documents decide the two queries below:

		- A forward row carries `si_detail`, the invoice row it fulfils.
		- A **return** row does not. `make_return_doc` sets its `dn_detail` to the forward row it
		  reverses and copies `against_sales_invoice`, which names the invoice but not the row.
		  So returns are found by walking `dn_detail` back to the forward rows read above, and a
		  return whose `dn_detail` is empty — one typed in by hand against an invoice — cannot be
		  attributed to a row at all. It is deliberately not guessed at: the row's delivered
		  quantity then stays higher than reality, which overstates a shortage in the report
		  instead of understating one.
		- `Delivery Note.per_returned` is maintained by the standard controller and is the
		  cheapest way to tell a closed delivery from one that can still be returned.

		Cancelled deliveries are read rather than filtered out, so a stored link to one can be
		reported as cancelled instead of silently disappearing. Drafts are read too: packing
		happens before submission, and a serial may be attached to a shipment that has not been
		submitted yet.

		`invoice_name` is not used in the queries — the row names are exact — and is accepted so
		the call reads as what it is, a question about one invoice.
		"""

		keys = tuple(dict.fromkeys(key for key in row_keys if key))

		if not keys:
			return {}

		forward = []

		for chunk in _chunks(keys):
			forward.extend(
				frappe.get_all(
					DELIVERY_ITEM_DOCTYPE,
					filters={"parenttype": DELIVERY_DOCTYPE, "si_detail": ["in", chunk]},
					fields=["name", "parent", "si_detail", "qty", "warehouse", "idx"],
					order_by="parent asc, idx asc, name asc",
					limit_page_length=0,
				)
			)

		forward_names = tuple(sorted({row.get("name") for row in forward if row.get("name")}))
		returns = []

		for chunk in _chunks(forward_names):
			returns.extend(
				frappe.get_all(
					DELIVERY_ITEM_DOCTYPE,
					filters={"parenttype": DELIVERY_DOCTYPE, "dn_detail": ["in", chunk]},
					fields=["name", "parent", "dn_detail", "qty", "warehouse", "idx"],
					order_by="parent asc, idx asc, name asc",
					limit_page_length=0,
				)
			)

		notes = {
			note.get("name"): note
			for note in self._by_names(
				DELIVERY_DOCTYPE,
				{row.get("parent") for row in forward + returns if row.get("parent")},
				["name", "docstatus", "is_return", "per_returned", "posting_date"],
			)
		}

		row_of = {row.get("name"): row.get("si_detail") for row in forward}
		grouped = {key: [] for key in keys}

		for row in forward:
			_in = row_of.get(row.get("name"))
			grouped.setdefault(_in, []).append(
				_candidate(row, notes.get(row.get("parent")), invoice_item=_in)
			)

		for row in returns:
			invoice_item = row_of.get(row.get("dn_detail"))
			grouped.setdefault(invoice_item, []).append(
				_candidate(row, notes.get(row.get("parent")), invoice_item=invoice_item)
			)

		return {key: tuple(grouped.get(key, ())) for key in keys}

	def delivery_links(self, identities):
		"""The delivery links the other registrations of these identities hold.

		See `other_delivery_links`, which is a module-level function because a document event
		needs the same answer and has no gateway: the event is registered in `hooks_composition`
		and runs inside the framework, not through this class.
		"""
		return other_delivery_links(identities)

	def read_assignment(self, name):
		"""One assignment's invoice and company, or `None`.

		The company is read from the stored row, never from the request: it is what
		`authz.require_company` decides on, so it must not be a value the caller supplied.
		"""
		return frappe.db.get_value(ASSIGNMENT_DOCTYPE, name, ["sales_invoice", "company"], as_dict=True)

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
				# `name` breaks a tie between two rows written in the same second: which serials are
				# the excess ones depends on this order, and it has to be the same order every time.
				order_by="sales_invoice_item asc, creation asc, name asc",
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

	# --- session lifecycle -------------------------------------------------------

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
			["name", "company", "docstatus"],
			as_dict=True,
			for_update=True,
		)

		if not stored:
			return None

		return InvoiceRef(name=stored.name, company=stored.company, docstatus=int(stored.docstatus or 0))

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
		"""Insert the assignments the plan built. The identity comes from the serial, not here.

		The delivery pair is written when the plan resolved one and left empty when it did not.
		`delivery_note_item` is passed for symmetry with `delivery_note` and is re-derived from
		the note by the `Logistics Serial Assignment` document events, which is the only place
		that value is trusted from.
		"""
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
					"delivery_note": value.delivery_note,
					"delivery_note_item": value.delivery_note_item,
				}
			)
			document.insert()
			names.append(document.name)

		return tuple(names)

	def insert_transfer(self, record):
		"""Write the audit record of a transfer, submitted, in this same transaction.

		Inserted with `docstatus` already set rather than inserted and then submitted, and that is
		not a shortcut: `Document.save` checks the **`write`** permission before it checks
		anything else, and no role has `write` on this DocType — the record is not editable, which
		is the whole point of it. Inserting at `docstatus = 1` asks for `create` and `submit` and
		nothing else, and `set_docstatus` reads the transition as a submit, so `on_submit` still
		runs if this app ever adds one.

		One document, one call. The assignments it describes are written by the caller in the same
		transaction, so a transfer that fails halfway leaves neither: a record of a move that did
		not happen is worse than no record, and a move with no record is what this feature exists
		to stop.

		Afterwards the framework will not let go of it: a submitted document cannot be changed
		(`_validate_update_after_submit` compares every field against the stored copy) and cannot
		be deleted by anyone, the Administrator included
		(`delete_doc.check_permission_and_not_submitted`). History that can be removed is not
		history.
		"""
		document = frappe.get_doc({"doctype": TRANSFER_DOCTYPE, "docstatus": 1, **record})
		document.insert()

		return document.name

	def insert_transfers(self, records, items):
		"""Write restored audit records with their own rows, one document per record.

		Not `insert_exported`, and the difference is what a child row is. Those rows are inserted
		as documents of their own, which is right for a serial or an assignment and wrong for a row
		of a table: a child needs `parenttype` and `parentfield`, which the format does not carry
		and the framework sets from the parent when the parent is saved. Inserting them separately
		would write orphans, or fail.

		So each record is one document, with its rows in the child table, and the record's own
		`docstatus` comes from the file — which is what makes a restored record submitted and
		therefore as immutable as the one it came from (I24). The rows are stamped with the restore
		by the caller's plan, and the provenance write-back is the same as everywhere else: when and
		by whom the row was *originally* written, not when this site received it.
		"""
		by_parent = {}

		for item in items:
			by_parent.setdefault(item.get("parent"), []).append(item)

		choices = _select_options(TRANSFER_DOCTYPE)
		previous = flags.in_import
		flags.in_import = True
		names = []

		try:
			for record in records:
				rows = by_parent.get(record.get("name"), [])
				values = {field: record.get(field) for field in export.TRANSFER_FIELDS}
				children = [
					{
						field: row.get(field)
						for field in export.TRANSFER_ITEM_FIELDS
						if field not in ("parent", "parenttype", "parentfield")
					}
					for row in rows
				]
				misplaced = export.misplaced_choices(choices, values)

				if misplaced:
					raise export.ExportError(
						export.CODE_VALUE_NOT_ALLOWED,
						f"مقدار «{misplaced[0][1]}» برای «{misplaced[0][0]}» در این سایت مجاز نیست.",
					)

				document = frappe.get_doc({"doctype": TRANSFER_DOCTYPE, **values, "items": children})
				document.insert()

				provenance = export.row_timestamps(record)

				if provenance:
					frappe.db.set_value(TRANSFER_DOCTYPE, document.name, provenance, update_modified=False)

				names.append(document.name)
		finally:
			flags.in_import = previous

		return tuple(names)

	# --- exporting and importing this app's records -------------------------------

	def export_rows(self, principal):
		"""The serials and assignments this caller may export, with every field the format defines.

		Scoped to the caller's companies, in the reads themselves. An export is the one endpoint
		whose entire answer is stored data, so a restriction applied to the finished document
		would be a filter over rows that had already been read — and the rows it dropped would
		still have been loaded, copied into a response and written to an operator's disk.

		A principal with no company restriction — every `System Manager` on any site, and everyone
		on a single-company site that has granted no `Company` user permissions — reads exactly
		what this method read before the restriction existed: the whole site, in the same order,
		row for row.

		`limit_page_length=0` is Frappe's own "no limit", measured on this stack rather than
		assumed: against a table of 78 rows it returned 78, where a page size would have returned
		the page. An export that silently stopped at the first page would be the worst possible
		bug in this file.
		"""
		companies = authz.visible_companies(principal)

		assignments = tuple(
			frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters=self._company_filter(companies),
				fields=list(export.ASSIGNMENT_FIELDS),
				order_by="name asc",
				limit_page_length=0,
			)
		)

		if companies is None:
			serials = tuple(
				frappe.get_all(
					SERIAL_DOCTYPE,
					fields=list(export.SERIAL_FIELDS),
					order_by="name asc",
					limit_page_length=0,
				)
			)
		else:
			serials = self._reachable_serials(assignments, export.SERIAL_FIELDS)

		# The transfer records of the same companies, and their rows. Scoped the same way and for
		# the same reason: a record names two invoices and a company, so a restricted caller must
		# not be handed another company's. A transfer's own rows are read as one query filtered by
		# the parents that survived, rather than read whole and filtered here — the second is a
		# filter over rows that were already loaded.
		transfers = tuple(
			frappe.get_all(
				TRANSFER_DOCTYPE,
				filters=self._company_filter(companies),
				fields=list(export.TRANSFER_FIELDS),
				order_by="name asc",
				limit_page_length=0,
			)
		)

		return {
			"serials": serials,
			"assignments": assignments,
			"transfers": transfers,
			"transfer_items": self._transfer_items(tuple(row.get("name") for row in transfers)),
		}

	def _transfer_item_names(self, transfers):
		"""The names of the rows of these records, for the collision check."""
		return [row.get("name") for row in self._transfer_items(tuple(row.get("name") for row in transfers))]

	def _transfer_items(self, parents):
		"""The rows of these transfers, in parent and then row-name order."""
		rows = []

		for chunk in _chunks(tuple(name for name in parents if name)):
			rows.extend(
				frappe.get_all(
					TRANSFER_ITEM_DOCTYPE,
					filters={"parent": ["in", chunk]},
					fields=list(export.TRANSFER_ITEM_FIELDS),
					order_by="parent asc, name asc",
					limit_page_length=0,
				)
			)

		return tuple(rows)

	def _reachable_serials(self, assignment_rows, fields):
		"""The serial rows those assignments point at, in name order.

		`Logistics Serial` carries no company of its own; a serial belongs to a company only
		through the assignments that name it. So this is the only way to ask which serials a
		restricted caller may see — and it asks it by name, against the primary key, rather than
		reading the whole serial table and dropping rows afterwards, which would have loaded the
		other companies' rows first and is the very thing being avoided.
		"""
		referenced = tuple(
			sorted({row.get("logistics_serial") for row in assignment_rows if row.get("logistics_serial")})
		)

		return tuple(
			sorted(
				self._by_names(SERIAL_DOCTYPE, referenced, fields),
				key=lambda row: row.get("name") or "",
			)
		)

	def transfer_world(
		self,
		principal,
		*,
		item_codes=(),
		company_names=(),
		invoice_names=(),
		serial_names=(),
		delivery_names=(),
	):
		"""The site as `core.export.World` sees it — as this caller is allowed to see it.

		The names are the ones **the document mentions**, and they are given rather than derived:
		the plan asks whether these items, companies, invoices and delivery notes exist here, so a
		world built only from what is already stored answers about the wrong set. Measured in the lab: a
		five-row export imported into a freshly reinstalled site — whose tables are empty by
		definition — reported every row as `ITEM_MISSING`, with the item sitting on the site the
		whole time. `core.export.world_names` reads them off the document.

		That leaves the permission question, and this is where it is answered: every read below is
		restricted to the caller's companies. The plan's three answers — create, skip, refuse — are
		all derived from what the world holds, so a world that holds another company's rows
		answers "this invoice already carries this serial" about an invoice the caller may not
		see. That is both a disclosure and a decision made on data that is not theirs.

		The write was never the hole. `apply_serial_restore` authorizes the live invoice's company
		per row, from `world.invoices`, before it writes anything, and refuses the whole request
		otherwise — but a preview that is only refused at the apply step is still a preview that
		told the caller something.

		`Item` and `Company` are asked for their existence only, and a restricted caller's world
		holds only their own companies: a file naming somebody else's company reports
		`COMPANY_MISSING` rather than confirming which companies this site has.
		"""
		companies = authz.visible_companies(principal)
		company_filter = self._company_filter(companies)

		# The assignments already on the invoices this document names: that is the set the
		# duplicate and link-conflict checks are about. With no invoice named, every stored
		# assignment of the caller's companies is read, because guessing that there are none would
		# be guessing.
		assignment_filters = {"sales_invoice": ["in", tuple(invoice_names)]} if invoice_names else {}

		assignments = tuple(
			frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters={**assignment_filters, **company_filter},
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

		if companies is None:
			serials = tuple(
				frappe.get_all(
					SERIAL_DOCTYPE,
					fields=["name", "item_code", "normalized_serial"],
					limit_page_length=0,
				)
			)
		else:
			# Reachability is about the caller's companies, not about the invoices this document
			# happens to name: a serial of theirs that sits on some other invoice is still a
			# serial this site already has, and calling it new would make the import collide with
			# its own history over a row it may not have been told about.
			mine = tuple(
				frappe.get_all(
					ASSIGNMENT_DOCTYPE,
					filters=company_filter,
					fields=["logistics_serial"],
					limit_page_length=0,
				)
			)
			serials = self._reachable_serials(mine, ["name", "item_code", "normalized_serial"])

		# One read, two answers. `docstatus` is read here and never taken from the file being
		# restored: a cancelled invoice is refused by the planner the way every other write path
		# refuses it, and only the stored document can say which invoices those are.
		reachable = [
			row
			for row in self._by_names(INVOICE_DOCTYPE, tuple(invoice_names), ["name", "company", "docstatus"])
			if companies is None or row.get("company") in companies
		]

		invoices = {row.get("name"): row.get("company") for row in reachable}
		cancelled_invoices = frozenset(row.get("name") for row in reachable if row.get("docstatus") == 2)

		item_rows = self._invoice_rows(tuple(invoices))

		# The delivery notes the file names, read for the same two facts the invoices above are read
		# for: is it here, and is it cancelled. Both are questions only the stored document can
		# answer, and both are refused by `serial_link` on every other write path. Company-scoped
		# like everything else here, so another company's note is reported as missing rather than
		# confirming which notes this site has.
		delivery_headers = [
			row
			for row in self._by_names(
				DELIVERY_DOCTYPE, tuple(delivery_names), ["name", "company", "docstatus"]
			)
			if companies is None or row.get("company") in companies
		]

		deliveries = frozenset(row.get("name") for row in delivery_headers)
		cancelled_deliveries = frozenset(
			row.get("name") for row in delivery_headers if row.get("docstatus") == 2
		)

		# The record names this site already uses, restricted to the caller's companies for the
		# headers — a record of another company is not a name a caller may be told about. The child
		# names are asked for by the parents that survived, so the same rule reaches them.
		transfers = tuple(
			frappe.get_all(
				TRANSFER_DOCTYPE,
				filters=company_filter,
				fields=["name"],
				limit_page_length=0,
			)
		)

		# The serial masters the document names, looked up by name and nothing else. This read is
		# deliberately *not* company-scoped, and it is the one exception to the paragraph above:
		# `Logistics Serial` has no company column, so a name says nothing about which company
		# holds it, and a name is unique over the site. Asked because the identity above cannot
		# answer it — a serial reachable only through another company's invoice is invisible
		# there, and a plan that called it new would insert a master whose name is taken and lose
		# the whole file to a duplicate-key error.
		named = frozenset(
			row.get("name")
			for row in self._by_names(SERIAL_DOCTYPE, tuple(serial_names), ["name"])
			if row.get("name")
		)

		return export.World(
			transfers=frozenset(row.get("name") for row in transfers),
			transfer_items=frozenset(self._transfer_item_names(transfers)),
			serial_identities=frozenset(export.serial_identity(row) for row in serials),
			serial_names=named,
			serials_by_identity={export.serial_identity(row): row.get("name") for row in serials},
			items=frozenset(row.get("name") for row in self._by_names("Item", tuple(item_codes), ["name"])),
			companies=frozenset(
				row.get("name")
				for row in self._by_names("Company", tuple(company_names), ["name"])
				if companies is None or row.get("name") in companies
			),
			invoices=invoices,
			cancelled_invoices=cancelled_invoices,
			invoice_rows=frozenset((row.get("parent"), row.get("name")) for row in item_rows),
			deliveries=deliveries,
			cancelled_deliveries=cancelled_deliveries,
			# The row of each named note that carries the invoice row — the value the write derives
			# and stores, computed over the same ordering `serial_link._delivery_row` uses, so the
			# plan's output and the stored row cannot disagree about which shipment row a
			# registration belongs to.
			delivery_rows=self._delivery_rows(tuple(deliveries)),
			# The cap an operator meets while scanning, given to the planner as the row's own
			# invoiced quantity. Counted from the assignments already read above, so an import
			# measures a row against what it actually holds rather than against what a file says.
			row_capacity={
				(row.get("parent"), row.get("name")): required_units(row.get("qty")) for row in item_rows
			},
			row_registered=_count_by_row(assignments),
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
		fields = EXPORTED_FIELDS.get(doctype)

		if fields is None:
			raise export.ExportError(
				export.CODE_BAD_SHAPE,
				f"این برنامه نمی‌داند چگونه ردیف‌های «{doctype}» را بنویسد.",
			)

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
		return _rows_by_names(doctype, names, fields)

	def _invoice_rows(self, invoice_names):
		"""Every item row of these invoices, with the quantity it is allowed to carry.

		`qty` is read here rather than in a second query because the capacity of a row *is* its
		invoiced quantity, and the two are asked for together or not at all: a caller that had the
		rows without their quantities would have to guess, and guessing is how a cap becomes a
		number nobody enforces.
		"""
		rows = []

		for chunk in _chunks(tuple(invoice_names)):
			if not chunk:
				continue

			rows.extend(
				frappe.get_all(
					"Sales Invoice Item",
					filters={"parent": ["in", chunk]},
					fields=["parent", "name", "qty", "idx"],
					limit_page_length=0,
				)
			)

		return tuple(rows)

	def _delivery_rows(self, delivery_names):
		"""`(delivery note, invoice row name) -> delivery row name`, first row wins.

		The same answer `serial_link._delivery_row` derives on the write path, over the same
		ordering, so the plan can state the value the insert will store instead of carrying a name
		from another site's numbering. A **return** delivery row carries `dn_detail` and no
		`si_detail` — the framework does not copy it forward — so a returned shipment has no entry
		here, which is how a row naming one is refused as incomplete rather than silently linked to
		the forward row it happens to sit on.
		"""
		rows = []

		for chunk in _chunks(tuple(delivery_names)):
			if not chunk:
				continue

			rows.extend(
				frappe.get_all(
					DELIVERY_ITEM_DOCTYPE,
					filters={"parent": ["in", chunk], "parenttype": DELIVERY_DOCTYPE},
					fields=["parent", "name", "si_detail"],
					order_by="parent asc, idx asc, name asc",
					limit_page_length=0,
				)
			)

		answer = {}

		for row in rows:
			if row.get("si_detail"):
				answer.setdefault((row.get("parent"), row.get("si_detail")), row.get("name"))

		return answer

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


def other_delivery_links(identities, for_update=False):
	"""Delivery links of the **other** registrations of the same serial identities.

	`identities` are `(item_code, normalized_serial)` pairs. The answer maps each identity to
	the `(delivery_note, docstatus, per_returned)` triples of the registrations that name the
	same serial on any invoice, so `core.delivery.conflicting_delivery` can decide whether one of
	them is still holding the unit in a different shipment.

	Read by normalized text rather than through `Logistics Serial`, because an identity with no
	serial record yet cannot be referenced, and the question is about the identity. The item code
	is matched in Python: neither read below can express a tuple of pairs, and a normalized serial
	is specific enough that the candidate rows are few.

	Registrations with no delivery are left out. They are the pre-existing acknowledgement case —
	a serial named on two invoices — and calling that a delivery conflict would be a new rule.

	`for_update` asks for a locking read, so a conflict decision made under the invoice lock sees
	the deliveries committed now rather than the transaction's first view. `_derive_delivery`
	passes `True`; the planner's read stays a plain one.
	"""
	wanted = tuple(sorted({identity for identity in identities if identity and identity[1]}))

	if not wanted:
		return {}

	normalized = tuple(dict.fromkeys(identity[1] for identity in wanted))
	rows = []

	for chunk in _chunks(normalized):
		if for_update:
			rows.extend(
				frappe.db.get_values(
					ASSIGNMENT_DOCTYPE,
					filters={"normalized_serial": ["in", chunk], "delivery_note": ["is", "set"]},
					fieldname=["item_code", "normalized_serial", "delivery_note"],
					as_dict=True,
					for_update=True,
				)
			)
		else:
			rows.extend(
				frappe.get_all(
					ASSIGNMENT_DOCTYPE,
					filters={"normalized_serial": ["in", chunk], "delivery_note": ["is", "set"]},
					fields=["item_code", "normalized_serial", "delivery_note"],
					limit_page_length=0,
				)
			)

	notes = {
		note.get("name"): note
		for note in _rows_by_names(
			DELIVERY_DOCTYPE,
			{row.get("delivery_note") for row in rows if row.get("delivery_note")},
			["name", "docstatus", "per_returned"],
		)
	}

	grouped = {identity: [] for identity in wanted}

	for row in rows:
		identity = (row.get("item_code"), row.get("normalized_serial"))

		if identity not in grouped:
			continue

		note = notes.get(row.get("delivery_note")) or {}

		grouped[identity].append(
			(
				row.get("delivery_note"),
				int(note.get("docstatus") or 0),
				float(note.get("per_returned") or 0),
			)
		)

	return grouped


def _rows_by_names(doctype, names, fields):
	"""Rows of one DocType whose name is in `names`, asked in chunks."""
	rows = []

	for chunk in _chunks(tuple(name for name in names if name)):
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


def _candidate(row, note, invoice_item):
	"""One `DeliveryCandidate` from a Delivery Note item row and its parent's facts.

	`is_return` comes from the **parent document**, not from the sign of the quantity. A return
	Delivery Note is the document that says "this moved back"; a negative quantity on a forward
	delivery is a data-entry mistake in a document that has not been submitted and cannot be
	submitted, and reading it as a return would net it against a shipment that never happened.
	"""
	note = note or {}
	docstatus = int(note.get("docstatus") or 0)

	return delivery_rules.DeliveryCandidate(
		delivery_note=row.get("parent"),
		delivery_note_item=row.get("name"),
		invoice_item=invoice_item,
		delivery_date=note.get("posting_date"),
		qty=float(row.get("qty") or 0),
		docstatus=docstatus,
		is_return=bool(note.get("is_return")),
		per_returned=float(note.get("per_returned") or 0),
		warehouse=row.get("warehouse"),
		against_detail=row.get("dn_detail"),
	)


def _count_by_row(assignments):
	"""How many assignments each invoice row holds, from a read that has already happened.

	Counted here rather than asked of the database, because the import already reads exactly these
	rows to answer "is this serial already on this invoice" — and a second query could answer
	about a different moment than the first.
	"""
	counts = {}

	for row in assignments:
		key = (row.get("sales_invoice"), row.get("sales_invoice_item"))
		counts[key] = counts.get(key, 0) + 1

	return counts


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
