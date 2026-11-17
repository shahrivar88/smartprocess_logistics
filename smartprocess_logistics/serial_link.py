"""Where the assignment rules meet the database, for every write path that exists.

`core/assignment.py` states the rules and records in `RUNTIME_ENFORCEMENT` exactly what these
events must do; this module is the only place those rules are applied to a real document. It
exists because the app's DocType controllers are held empty on purpose — `tests/test_doctype_
controllers.py` reads them with an AST and asserts a controller declares nothing — so validation
arrives through the `doc_events` that `hooks_composition.approved_doc_events()` registers, and
`hooks.py` imports that list.

No rule is decided here. Every value and every judgement comes from `core.assignment`,
`core.delivery`, `core.eligibility`, `core.normalization`, `core.quantities` and `core.serial`;
this module reads the documents a decision needs and raises the refusal those modules describe. It never posts, never reverses
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

from smartprocess_logistics.api import gateway
from smartprocess_logistics.core import assignment
from smartprocess_logistics.core import authz
from smartprocess_logistics.core import delivery as delivery_rules
from smartprocess_logistics.core import eligibility, quantities
from smartprocess_logistics.core import normalization
from smartprocess_logistics.core import serial as serial_rules
from smartprocess_logistics.core import transfer as transfer_rules

ASSIGNMENT_DOCTYPE = "Logistics Serial Assignment"
SERIAL_DOCTYPE = "Logistics Serial"
TRANSFER_DOCTYPE = "Logistics Serial Transfer"
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
		SERIAL_DOCTYPE,
		name,
		["name", "item_code", "normalized_serial"],
		as_dict=True,
		for_update=True,
	)

	if not serial:
		_refuse(f"سریال «{name}» پیدا نشد.")

	return serial


def _invoice(doc):
	"""The invoice this registration belongs to, locked for update, or `None` when the name matched nothing.

	`docstatus` is read with the company because it is the other fact a decision needs and the other fact a
	caller must not supply. The lock is taken here, before any count or comparison, so every later read sees
	the invoice as it is now rather than a transaction snapshot.
	"""
	name = doc.get("sales_invoice")

	if not name:
		_refuse("فاکتور فروش این ثبت خالی است.")

	invoice = frappe.db.get_value(
		INVOICE_DOCTYPE,
		name,
		["name", "company", "docstatus"],
		as_dict=True,
		for_update=True,
	)

	return invoice


def _authorize_invoice(invoice):
	"""Refuse an invoice this caller may not read, and a company they may not reach.

	The CRUD path names no endpoint, so `require_invoice_access` cannot be called; the same checks are
	inlined here. A name that matched nothing and a name in an unreachable company are the same refusal,
	exactly as `authz.INVOICE_REFUSAL` states, so a caller cannot learn which invoice names are real.

	Skipped under `_is_restore()`: the restore authorizes itself at the endpoint — role, company reachability
	and the invoice locks — and `frappe.has_permission` would add a check it was deliberately built without.
	"""
	principal = gateway.principal_for(frappe.session.user)

	if not invoice:
		_refuse(authz.INVOICE_REFUSAL)

	try:
		authz.require_company(principal, invoice.get("company"))
	except authz.AuthorizationError:
		_refuse(authz.INVOICE_REFUSAL)

	if not frappe.has_permission(INVOICE_DOCTYPE, doc=invoice.get("name"), ptype="read"):
		_refuse(authz.INVOICE_REFUSAL)

	return invoice


def _refuse_cancelled(invoice):
	"""Refuse a new registration on an invoice the site has cancelled.

	Only new rows are refused; a cancelled invoice's own registrations must still be readable and removable.
	A restore is the one path that may place them back, so it is skipped, as `core.export` documents.
	"""
	if _is_restore():
		return

	if int(invoice.get("docstatus") or 0) == authz.DOCSTATUS_CANCELLED:
		_refuse(authz.REFUSAL_INVOICE_CANCELLED)


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
			for_update=True,
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
	held = len(
		frappe.db.get_values(
			ASSIGNMENT_DOCTYPE,
			filters={
				"sales_invoice": doc.get("sales_invoice"),
				"sales_invoice_item": row.get("name"),
			},
			fieldname=["name"],
			as_dict=True,
			for_update=True,
		)
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
	others = gateway.other_delivery_links((identity,), for_update=True).get(identity, ())

	if delivery_rules.conflicting_delivery(delivery_note, others):
		_refuse("این سریال روی رسید تحویل فعال دیگری ثبت شده است و هم‌زمان در دو تحویل باز نمی‌شود.")


def validate_assignment(doc, method=None):
	"""Hold one assignment to the rules its core states, on every write path.

	Reads are ordered by the lock plan `core.orchestration` declares — invoice, then serial, then row, then
	assignment — and every decision is a current read under that lock. The stored row is read before anything
	is derived, for the reason the module docstring states.
	"""
	if _skip_event():
		return

	invoice = _invoice(doc)

	if not _is_restore():
		_authorize_invoice(invoice)

	if doc.is_new():
		_refuse_cancelled(invoice)

	serial = _serial(doc)
	row = _invoice_row(doc, invoice)

	stored = None

	if not doc.is_new():
		stored = (
			frappe.db.get_value(
				ASSIGNMENT_DOCTYPE, doc.name, list(IMMUTABLE_FIELDS), as_dict=True, for_update=True
			)
			or {}
		)

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


def validate_serial(doc, method=None):
	"""Hold `Logistics Serial` to the rules `core/serial.py` states, on every write path.

	The identity is derived on the server from `serial_no`, never read from the request, and it is
	derived only at creation: re-deriving an existing serial would silently re-normalize it under
	the current rule set and fight the migration that is the only place a rule change is applied.
	So an update reasserts the stored identity and refuses a change to the fields that name it. A
	restore (`flags.in_import`) skips the derivation — the imported row already carries the identity
	it was first written with, and a restore preserves history rather than re-normalizing it.
	"""
	if _skip_event():
		return

	if doc.is_new():
		if _is_restore():
			return

		try:
			identity = serial_rules.derive_serial_identity(doc.get("serial_no"))
		except normalization.SerialTextError as error:
			_refuse(error.message)

		doc.normalized_serial = identity.normalized
		doc.normalization_version = identity.version
		return

	stored = (
		frappe.db.get_value(
			SERIAL_DOCTYPE,
			doc.name,
			["serial_no", "item_code", "normalized_serial", "normalization_version"],
			as_dict=True,
		)
		or {}
	)

	changed = serial_rules.changed_identity_fields(stored, doc)

	if changed:
		_refuse(
			f"سریال پس از ایجاد قابل ویرایش نیست («{changed[0]}» تغییر کرده است)؛"
			" برای اصلاح، سریال را حذف و دوباره ثبت کنید."
		)

	doc.normalized_serial = stored.get("normalized_serial")
	doc.normalization_version = stored.get("normalization_version")


def _transfer_record(doc):
	"""The plain record a `Logistics Serial Transfer` document is, in the shape `record_of` writes.

	`core.transfer.record_issues` consumes the dictionary `record_of` returns, so the live validator
	rebuilds that same dictionary from the document. The names here are the document's, which are
	also the record's: the DocType fields and the child rows were chosen to *be* the record, and the
	restore marker is the one field the document carries that `record_of` does not set.
	"""
	items = []
	for item in doc.get("items") or ():
		items.append(
			{
				"logistics_serial": item.get("logistics_serial"),
				"serial_no": item.get("serial_no"),
				"item_code": item.get("item_code"),
				"normalized_serial": item.get("normalized_serial"),
				"source_assignment": item.get("source_assignment"),
				"source_row": item.get("source_row"),
				"source_index": item.get("source_index"),
				"acknowledged": item.get("acknowledged"),
				"result": item.get("result"),
				"reason": item.get("reason"),
				"reason_message": item.get("reason_message"),
				"target_row": item.get("target_row"),
				"target_index": item.get("target_index"),
			}
		)

	return {
		"source_invoice": doc.get("source_invoice"),
		"target_invoice": doc.get("target_invoice"),
		"company": doc.get("company"),
		"confirmed_token": doc.get("confirmed_token"),
		"source_total": doc.get("source_total"),
		"moved_count": doc.get("moved_count"),
		"refused_count": doc.get("refused_count"),
		"unselected_count": doc.get("unselected_count"),
		"acknowledged_count": doc.get("acknowledged_count"),
		"restored_from": doc.get("restored_from"),
		"restored_on": doc.get("restored_on"),
		"restored_by": doc.get("restored_by"),
		"items": items,
	}


def _transfer_child_issues(doc):
	"""Every child row whose copies disagree with the serial master it links to.

	The pure checks say whether a row is complete; only the database says whether it is *right*.
	Each row copies `serial_no`, `item_code` and `normalized_serial` from the master when the record
	is written, so a row edited to point at a different serial while keeping the old copies — or to
	keep the link while rewriting the copies — is a record that names one serial and describes
	another. The master is read under lock so the comparison is not made against a value another
	write is about to change.

	This must not run during a restore: the archive writes children whose `logistics_serial` may
	name a serial that no longer exists on this site, while the copies it carries are the true
	history of what moved.
	"""
	issues = []
	seen = set()

	for index, item in enumerate(doc.get("items") or ()):
		link = item.get("logistics_serial")
		if not link or link in seen:
			continue
		seen.add(link)

		master = (
			frappe.db.get_value(
				SERIAL_DOCTYPE,
				link,
				["serial_no", "item_code", "normalized_serial"],
				as_dict=True,
				for_update=True,
			)
			or {}
		)

		for field in ("serial_no", "item_code", "normalized_serial"):
			if item.get(field) != master.get(field):
				issues.append(
					transfer_rules.RecordIssue(
						transfer_rules.CODE_CHILD_MASTER_MISMATCH,
						f"ردیف {index + 1} «{field}» اش با سریال پیوندشده نمی‌خواند.",
					)
				)
				break

	return tuple(issues)


def validate_transfer(doc, method=None):
	"""Hold `Logistics Serial Transfer` to the record rules `core/transfer.py` states.

	The transfer document is evidence, and evidence is only worth what it agrees with itself about:
	its header must name its rows, its rows must match their outcomes, and its provenance marker
	must be there in full or not at all. A record that fails one of these was not written by
	`record_of`, and this hook refuses it on every write path. The pure checks run even on a
	restore, because a restored archive is still the archive it was; the child-master agreement
	needs the database, so it is skipped during a restore, where a child may name a serial that no
	longer exists here.
	"""
	if _skip_event():
		return

	issues = list(transfer_rules.record_issues(_transfer_record(doc)))

	if not _is_restore():
		issues.extend(_transfer_child_issues(doc))

	if issues:
		_refuse(issues[0].message)
