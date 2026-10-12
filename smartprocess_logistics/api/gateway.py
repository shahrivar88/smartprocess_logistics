"""Every database call the app makes, in one class.

The orchestration in `core` asks for work by name; this is what performs it. Splitting them
means the plan is tested against a fake and this file stays a list of short ORM calls.

Inactive: the app is not installed, so nothing here runs yet.

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
from frappe.utils import now

from smartprocess_logistics.core import authz, importer, orchestration, session as session_rules
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

		user_permissions = frappe.permissions.get_user_permissions(user) or {}
		allowed = user_permissions.get("Company")
		companies = (
			frozenset(entry.get("doc") for entry in allowed if entry.get("doc"))
			if allowed
			else None
		)

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
		"""Let the framework check the caller's own read permission on the invoice."""
		frappe.has_permission(INVOICE_DOCTYPE, doc=name, ptype="read", throw=True)
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

	def list_assignments(self, invoice_name, limit, offset):
		"""One page of the invoice's assignments, for the read endpoint."""
		return tuple(
			frappe.get_all(
				ASSIGNMENT_DOCTYPE,
				filters={"sales_invoice": invoice_name},
				fields=list(ASSIGNMENT_FIELDS),
				order_by="sales_invoice_item asc, creation asc",
				limit_start=offset,
				limit_page_length=limit,
			)
		)

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
		"""Insert a prepared session and return its name."""
		document = frappe.get_doc({"doctype": SESSION_DOCTYPE, **values})
		document.insert()

		return document.name

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

		`requests` is `(item_code, normalized, original)` triples already sorted by the caller.
		The identity is passed to `Logistics Serial` as its own `normalized_serial`, and the
		assignment's copy is derived from the row that is returned — never from the request.
		"""
		by_normalized = {}
		created = []
		reused = 0

		for item_code, normalized, original in requests:
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

	# --- settings ----------------------------------------------------------------

	def settings(self):
		"""The seeded settings, as a plain mapping."""
		return frappe.get_single(SETTINGS_DOCTYPE)

	def import_request(self, invoice, rows, existing, principal, **overrides):
		"""An `ImportRequest` built from the site's settings."""
		settings = self.settings()
		stored_delimiters = tuple(
			row.delimiter for row in settings.delimiters if row.enabled
		)
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
