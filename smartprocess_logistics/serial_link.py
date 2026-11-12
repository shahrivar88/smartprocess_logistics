"""Where the assignment rules meet the database, for every write path that exists.

`core/assignment.py` states the rules and records in `RUNTIME_ENFORCEMENT` exactly what these
events must do; this module is the only place those rules are applied to a real document. It
exists because the app's DocType controllers are held empty on purpose — `tests/test_doctype_
controllers.py` reads them with an AST and asserts a controller declares nothing — so validation
arrives through the `doc_events` that `hooks_composition.approved_doc_events()` registers, and
`hooks.py` imports that list.

No rule is decided here. Every value and every judgement comes from `core.assignment`,
`core.delivery`, `core.eligibility` and `core.quantities`; this module reads the documents a
decision needs and raises the refusal those modules describe. It never posts, never reverses
anything, never writes a stock or ledger row, and never calls `ignore_permissions`.

These events fire for every write path there is: the app's endpoints, the Desk form, the list
view's bulk edit, `frappe.client`, the REST API and a console `insert()`. That is the point. A
rule enforced only in the endpoints is a rule a request can walk around, and this is the layer a
request cannot reach around. The one hole left is the one `core.assignment` names: a direct
`INSERT` or `UPDATE` against the table, which needs database access.

A note on the shape: reading the stored row happens **before** anything is derived. The
immutability check compares the caller's document with what the database holds, and deriving the
server-owned fields first would overwrite the very values that comparison is about.
"""

import frappe

from smartprocess_logistics.api.gateway import other_delivery_links
from smartprocess_logistics.core import assignment
from smartprocess_logistics.core import delivery as delivery_rules
from smartprocess_logistics.core import eligibility, quantities

ASSIGNMENT_DOCTYPE = "Logistics Serial Assignment"
SERIAL_DOCTYPE = "Logistics Serial"
INVOICE_DOCTYPE = "Sales Invoice"
INVOICE_ITEM_DOCTYPE = "Sales Invoice Item"
DELIVERY_DOCTYPE = "Delivery Note"
DELIVERY_ITEM_DOCTYPE = "Delivery Note Item"
ITEM_DOCTYPE = "Item"

TITLE = "جریان لجستیک — ثبت سریال"

# An assignment is created or replaced, never edited into a different one. Editing any of these
# would move a stored registration onto another serial, invoice row or shipment without a trace.
IMMUTABLE_FIELDS = (
	"logistics_serial",
	"sales_invoice",
	"sales_invoice_item",
	"normalized_serial",
	"delivery_note",
	"delivery_note_item",
)


def _refuse(message):
	frappe.throw(message, title=TITLE)


def _skip_event():
	"""Leave installs, patches and migrations alone.

	Those write the app's own fixture data before any invoice exists to check it against, and a
	refusal there would break the lifecycle rather than protect a record.
	"""
	return bool(
		getattr(frappe.flags, "in_install", False)
		or getattr(frappe.flags, "in_patch", False)
		or getattr(frappe.flags, "in_migrate", False)
	)


def _is_restore():
	"""Whether this write is the app's own export coming back.

	`api.gateway.insert_exported` sets `in_import` for the length of its insert loop. Such a row
	was written by this app once already, and it may predate a rule below — a row over its
	invoice quantity written before the cap existed, or a row whose item has since had serials
	switched off. Refusing it would refuse the app's own history, and a restore preserves
	history; it does not register. The identity checks still run, because they only ever confirm
	what the row already says.
	"""
	return bool(getattr(frappe.flags, "in_import", False))


def _serial(doc):
	name = doc.get("logistics_serial")

	if not name:
		_refuse("سریال این ثبت خالی است.")

	serial = frappe.db.get_value(
		SERIAL_DOCTYPE, name, ["name", "item_code", "normalized_serial"], as_dict=True
	)

	if not serial:
		_refuse(f"سریال «{name}» پیدا نشد.")

	return serial


def _invoice(doc):
	name = doc.get("sales_invoice")

	if not name:
		_refuse("فاکتور فروش این ثبت خالی است.")

	invoice = frappe.db.get_value(INVOICE_DOCTYPE, name, ["name", "company"], as_dict=True)

	if not invoice:
		_refuse(f"فاکتور فروش «{name}» پیدا نشد.")

	return invoice


def _invoice_row(doc, invoice):
	"""The invoice item row this registration belongs to, and nobody else's row.

	Comparing the row's own parent with the named invoice is what stops a caller from pairing one
	invoice with another invoice's row: the row key comes from the request.
	"""
	name = doc.get("sales_invoice_item")
	row = None

	if name:
		row = frappe.db.get_value(
			INVOICE_ITEM_DOCTYPE,
			name,
			["name", "parent", "item_code", "qty"],
			as_dict=True,
		)

	if not row or row.get("parent") != invoice.get("name"):
		_refuse("ردیف فاکتور این ثبت با فاکتور نام‌برده هم‌خوان نیست.")

	return row


def _refuse_edit(doc, stored):
	changed = [
		field
		for field in IMMUTABLE_FIELDS
		if (doc.get(field) or None) != (stored.get(field) or None)
	]

	if changed:
		_refuse(
			f"ثبت سریال پس از ایجاد قابل ویرایش نیست («{changed[0]}» تغییر کرده است)؛"
			" برای اصلاح، ثبت را حذف و دوباره ثبت کنید."
		)


def _check_identity(doc, serial, stored):
	"""Refuse a copy that disagrees with the serial, wherever the copy came from.

	The copy is compared against `stored` rather than only against `doc`, because a stored value
	that drifted — written by a path that skipped the derivation — must be caught on its next
	write instead of being quietly overwritten into agreement.
	"""
	copied = (stored or {}).get("normalized_serial") or doc.get("normalized_serial")

	if copied and assignment.identity_mismatch(copied, serial.get("normalized_serial")):
		_refuse("شناسهٔ ذخیره‌شدهٔ این ثبت با شناسهٔ سریال مرجع یکی نیست.")

	doc.normalized_serial = serial.get("normalized_serial")


def _refuse_foreign_item(doc, serial, row):
	"""Both item checks, because they answer two different questions.

	`doc.item_code` is the stored copy of the serial's item, so a wrong value there is a caller
	deciding what the serial is. The invoice row's item is a caller naming a row of another item,
	which would put this serial under a row it was never sold on.
	"""
	if doc.get("item_code") and doc.get("item_code") != serial.get("item_code"):
		_refuse("کالای این ثبت با کالای سریال یکی نیست.")

	if row.get("item_code") != serial.get("item_code"):
		_refuse("این سریال به کالای ردیف فاکتور نام‌برده تعلق ندارد.")


def _refuse_ineligible(row):
	if _is_restore():
		return

	flagged = frappe.db.get_value(ITEM_DOCTYPE, row.get("item_code"), "sp_serial_required")

	if not eligibility.is_eligible(flagged):
		_refuse("برای کالای این ردیف ثبت سریال فعال نیست.")


def _refuse_excess(doc, row):
	"""The invoiced-quantity cap, applied to the row as it will be after this write.

	Unconditional, like `core.validation._excess_issues`: the cap is the rule, not a site setting.
	The count is taken here rather than from the request, so a caller that never asked the
	planner a question cannot exceed the row by writing rows one at a time.
	"""
	if _is_restore():
		return

	required = quantities.required_units(row.get("qty"))
	held = frappe.db.count(
		ASSIGNMENT_DOCTYPE,
		{"sales_invoice": doc.get("sales_invoice"), "sales_invoice_item": row.get("name")},
	)

	if held + 1 > required:
		_refuse("تعداد سریال این ردیف بیش از تعداد فاکتور است.")


def _delivery_row(delivery_note, invoice_item):
	rows = frappe.get_all(
		DELIVERY_ITEM_DOCTYPE,
		filters={
			"parent": delivery_note,
			"parenttype": DELIVERY_DOCTYPE,
			"si_detail": invoice_item,
		},
		fields=["name"],
		order_by="idx asc, name asc",
		limit=1,
	)

	return rows[0].get("name") if rows else None


def _derive_delivery(doc, serial, row):
	"""Set the delivery row from the named delivery, or refuse a note that does not carry it.

	`delivery_note_item` is always the server's answer, never the caller's: a row name supplied
	alongside the note would be a claim nobody can check. Requiring the note to carry this
	invoice row is what keeps a link meaningful — a serial may not be attached to a shipment of
	something else.
	"""
	delivery_note = doc.get("delivery_note")

	if not delivery_note:
		doc.delivery_note_item = None
		return

	note = frappe.db.get_value(DELIVERY_DOCTYPE, delivery_note, ["name", "docstatus"], as_dict=True)

	if not note:
		_refuse(f"رسید تحویل «{delivery_note}» پیدا نشد.")

	if int(note.get("docstatus") or 0) == delivery_rules.DOCSTATUS_CANCELLED:
		_refuse("رسید تحویل نام‌برده لغو شده است و سریال روی آن ثبت نمی‌شود.")

	delivery_note_item = _delivery_row(delivery_note, row.get("name"))

	if not delivery_note_item:
		_refuse("رسید تحویل نام‌برده ردیفی برای این ردیف فاکتور ندارد.")

	doc.delivery_note_item = delivery_note_item

	identity = (serial.get("item_code"), serial.get("normalized_serial"))
	others = other_delivery_links((identity,)).get(identity, ())

	if delivery_rules.conflicting_delivery(delivery_note, others):
		_refuse("این سریال روی رسید تحویل فعال دیگری ثبت شده است و هم‌زمان در دو تحویل باز نمی‌شود.")


def validate_assignment(doc, method=None):
	"""Hold one assignment to the rules its core states, on every write path."""
	if _skip_event():
		return

	stored = None

	if not doc.is_new():
		stored = (
			frappe.db.get_value(
				ASSIGNMENT_DOCTYPE, doc.name, list(IMMUTABLE_FIELDS), as_dict=True
			)
			or {}
		)

	serial = _serial(doc)
	invoice = _invoice(doc)
	row = _invoice_row(doc, invoice)

	if stored is not None:
		_refuse_edit(doc, stored)

	_check_identity(doc, serial, stored)
	_refuse_foreign_item(doc, serial, row)

	doc.item_code = serial.get("item_code")

	if doc.get("company") and doc.get("company") != invoice.get("company"):
		_refuse("شرکت این ثبت با شرکت فاکتور نام‌برده یکی نیست.")

	doc.company = invoice.get("company")

	if doc.is_new():
		_refuse_ineligible(row)
		_refuse_excess(doc, row)

	_derive_delivery(doc, serial, row)


def audit_deletion(doc, method=None):
	"""Leave a trace of a removed registration on the invoice it belonged to.

	Nothing is written to `Logistics Serial` — the serial itself is an identity other invoices may
	still reference. The entry goes on the Sales Invoice because that is the document an operator
	would open to ask where a serial went.

	`Document.add_comment` writes its `Comment` through the framework's own privileged path,
	which is why this succeeds for a user allowed to remove the registration but not to write the
	invoice. That path is framework code; this module claims no privilege of its own.
	"""
	if _skip_event():
		return

	invoice = doc.get("sales_invoice")

	if not invoice or not frappe.db.exists(INVOICE_DOCTYPE, invoice):
		return

	note = doc.get("delivery_note") or "ندارد"
	serial = doc.get("normalized_serial") or doc.get("logistics_serial")
	row = doc.get("invoice_item_idx") or "—"

	frappe.get_doc(INVOICE_DOCTYPE, invoice).add_comment(
		"Comment",
		f"سریال «{serial}» ({doc.get('item_code')}) از ردیف {row} برداشته شد؛ رسید تحویل: {note}.",
	)
