"""Validation-only `doc_events` for the invoice-first logistics workflow.

The decisions are in `core/integrity.py`; this module only reads the documents and hands the
values to those functions. It never posts, reverses or adjusts anything, and it never calls
`ignore_permissions`: the handler runs as the user saving the document, and a refusal is a
refusal for that user.

Most rules are scoped to a document with at least one Item whose `sp_serial_required` flag is
on; that flag already exists on every site this app runs on, and no custom field or migration is
required. The Purchase Invoice rule is broader: it applies to every domestic and foreign stock
purchase and reads the Item's `is_stock_item`/`is_fixed_asset` flags. A document outside both
scopes is left to ERPNext entirely.
"""

import frappe

from smartprocess_logistics.core import integrity

MANAGED_FIELD = "sp_serial_required"


def _skip(doc):
	"""Leave installs, patches and historical documents alone.

	Drafts (`docstatus = 0`) are inspected on every save, and a **submit** is inspected too:
	Frappe sets `docstatus = 1` before it calls `validate`, so the action, not the status, is
	what identifies a draft that is becoming a submitted document. Skipping that moment would
	let an API caller add an unlinked Delivery Note row after the draft was saved and submit it
	unchecked. A document already submitted and edited through `update_after_submit` is
	historical and is not inspected.
	"""
	if getattr(frappe.flags, "in_install", False):
		return True
	if getattr(frappe.flags, "in_patch", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True

	action = getattr(doc, "_action", "save")

	return doc.docstatus != 0 and action != "submit"


def _rule_docstatus(doc):
	"""The status the pure rule should see: a submit is still a draft being decided."""
	return 0 if getattr(doc, "_action", None) == "submit" else doc.docstatus


def _marker_exists():
	return bool(frappe.get_meta("Item").get_field(MANAGED_FIELD))


def _item_flags(item_codes):
	"""Read the marker for every item once, in one query."""
	if not _marker_exists():
		return {}

	codes = tuple(sorted({code for code in item_codes if code}))
	if not codes:
		return {}

	rows = frappe.get_all(
		"Item",
		filters={"name": ("in", codes)},
		fields=["name", MANAGED_FIELD],
	)

	return {row.get("name"): bool(row.get(MANAGED_FIELD)) for row in rows}


def _item_stock_flags(item_codes):
	"""Whether each item is a stock item, for the Delivery Note's warehouse rule."""
	codes = tuple(sorted({code for code in item_codes if code}))
	if not codes:
		return {}

	return {
		row.get("name"): bool(row.get("is_stock_item"))
		for row in frappe.get_all(
			"Item",
			filters={"name": ("in", codes)},
			fields=["name", "is_stock_item"],
		)
	}


def _item_purchase_flags(item_codes):
	"""Stock and fixed-asset flags for a Purchase Invoice's rows."""
	codes = tuple(sorted({code for code in item_codes if code}))
	if not codes:
		return {}

	return {
		row.get("name"): row
		for row in frappe.get_all(
			"Item",
			filters={"name": ("in", codes)},
			fields=["name", "is_stock_item", "is_fixed_asset"],
		)
	}


def _rows_managed(rows):
	flags = _item_flags([row.get("item_code") for row in rows])
	return flags, integrity.is_managed(flags.values())


def _warehouse_company(warehouse, cache):
	if not warehouse:
		return None
	if warehouse not in cache:
		cache[warehouse] = frappe.db.get_value("Warehouse", warehouse, "company")
	return cache[warehouse]


def _raise(problems, title):
	if not problems:
		return
	problem = problems[0]
	frappe.throw(problem.message, title=title)


def validate_sales_invoice(doc, method=None):
	"""A managed Sales Invoice is a request to deliver; the Delivery Note moves the stock."""
	if _skip(doc):
		return

	_flags, managed = _rows_managed(doc.items)
	problems = integrity.sales_invoice_problems(doc.update_stock, _rule_docstatus(doc), managed)
	_raise(problems, "جریان لجستیک — فاکتور فروش")


def validate_purchase_invoice(doc, method=None):
	"""An inventory Purchase Invoice is a bill; the Purchase Receipt receives the goods."""
	if _skip(doc):
		return

	flags = _item_purchase_flags(row.get("item_code") for row in doc.items)
	rows = []
	for row in doc.items:
		item = flags.get(row.get("item_code")) or {}
		rows.append(
			{
				"idx": row.idx,
				"item_code": row.get("item_code"),
				"is_stock_item": bool(item.get("is_stock_item")),
				"is_fixed_asset": bool(row.get("is_fixed_asset") or item.get("is_fixed_asset")),
				"qty": row.get("qty"),
			}
		)

	problems = integrity.purchase_invoice_problems(
		doc.update_stock,
		_rule_docstatus(doc),
		rows,
		is_return=bool(doc.is_return),
		is_opening=doc.is_opening == "Yes",
		is_internal_transfer=bool(doc.is_internal_transfer()),
	)
	_raise(problems, "جریان لجستیک — فاکتور خرید")


def _delivery_invoice_context(doc, commitment_lookup=None):
	"""The invoice headers and managed flags every Delivery Note check needs.

	`commitment_lookup` is the development extension's commitment reader; the approved
	validator passes None and reads no commitment. The per-invoice order is the original one:
	header, item flags, then the lookup.
	"""
	si_names = sorted({row.against_sales_invoice for row in doc.items if row.get("against_sales_invoice")})
	si_headers = {}
	si_managed = {}
	commitment = {} if commitment_lookup else None

	for name in si_names:
		si_headers[name] = (
			frappe.db.get_value(
				"Sales Invoice",
				name,
				["company", "docstatus", "update_stock", "creation"],
				as_dict=True,
				for_update=True,
			)
			or {}
		)
		items = frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": name},
			fields=["item_code"],
		)
		_flags, managed = _rows_managed(items)
		si_managed[name] = managed
		if commitment_lookup:
			commitment[name] = commitment_lookup(
				name, doc.company, (si_headers.get(name) or {}).get("creation")
			)

	return si_headers, si_managed, commitment


def _delivery_rows(doc, si_headers, si_managed, item_flags, stock_flags, commitment=None):
	warehouse_companies = {}
	rows = []

	for row in doc.items:
		si_item = None
		if row.get("si_detail"):
			si_item = frappe.db.get_value(
				"Sales Invoice Item",
				row.si_detail,
				["parent", "item_code"],
				as_dict=True,
			)
		header = si_headers.get(row.get("against_sales_invoice")) or {}
		record = {
			"idx": row.idx,
			"item_code": row.item_code,
			"si_detail": row.get("si_detail"),
			"against_sales_invoice": row.get("against_sales_invoice"),
			"si_item_parent": (si_item or {}).get("parent"),
			"si_item_code": (si_item or {}).get("item_code"),
			"si_docstatus": header.get("docstatus"),
			"si_company": header.get("company"),
			"si_update_stock": header.get("update_stock"),
			"si_managed": bool(si_managed.get(row.get("against_sales_invoice"))),
			"is_managed": bool(item_flags.get(row.item_code)),
			"is_stock_item": bool(stock_flags.get(row.item_code)),
			"warehouse": row.get("warehouse"),
			"warehouse_company": _warehouse_company(row.get("warehouse"), warehouse_companies),
			"target_warehouse_company": _warehouse_company(row.get("target_warehouse"), warehouse_companies),
		}
		if commitment is not None:
			state = commitment.get(row.get("against_sales_invoice")) or {}
			record["commitment_state"] = state.get("state", "none")
			record["committed_warehouse"] = state.get("warehouse")
		rows.append(record)

	return rows


def validate_delivery_note(doc, method=None):
	"""The approved managed-Delivery-Note rule (I30), with no commitment read."""
	if _skip(doc):
		return
	if doc.is_return:
		return

	item_flags, dn_managed = _rows_managed(doc.items)
	si_headers, si_managed, _commitment = _delivery_invoice_context(doc)
	managed = dn_managed or any(si_managed.values())
	if not managed:
		return

	stock_flags = _item_stock_flags(row.item_code for row in doc.items)
	rows = _delivery_rows(doc, si_headers, si_managed, item_flags, stock_flags)
	problems = integrity.delivery_note_problems(doc.company, bool(doc.is_return), rows, managed)
	_raise(problems, "جریان لجستیک — یادداشت تحویل")


def _fill_purchase_return_links(doc):
	"""Fill the `purchase_invoice_item` ERPNext's return mapper leaves empty.

	The value is a link the original receipt already holds. The plan is computed by a pure
	function; this function only applies it to the in-memory rows before ERPNext validates and
	posts the return.
	"""
	if not doc.is_return or not doc.return_against:
		return

	original_items = frappe.get_all(
		"Purchase Receipt Item",
		filters={"parent": doc.return_against},
		fields=["name", "idx", "item_code", "purchase_invoice", "purchase_invoice_item"],
	)
	if not original_items:
		return

	pi_names = {
		value
		for value in [row.get("purchase_invoice") for row in doc.items]
		+ [item.get("purchase_invoice") for item in original_items]
		if value
	}
	pi_items = {
		name: frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": name},
			fields=["name", "item_code"],
		)
		for name in pi_names
	}

	return_rows = [
		{
			"idx": row.idx,
			"item_code": row.item_code,
			"purchase_invoice": row.get("purchase_invoice"),
			"purchase_invoice_item": row.get("purchase_invoice_item"),
			"purchase_receipt_item": row.get("purchase_receipt_item"),
		}
		for row in doc.items
	]

	plans, problems = integrity.purchase_return_link_plan(return_rows, original_items, pi_items)
	_raise(problems, "جریان لجستیک — برگشت خرید")

	by_idx = {row.idx: row for row in doc.items}
	for plan in plans:
		row = by_idx.get(plan.get("idx"))
		if row is None:
			continue
		if not row.get("purchase_invoice") and plan.get("purchase_invoice"):
			row.purchase_invoice = plan["purchase_invoice"]
		if not row.get("purchase_invoice_item") and plan.get("purchase_invoice_item"):
			row.purchase_invoice_item = plan["purchase_invoice_item"]


def validate_purchase_receipt(doc, method=None):
	"""A linked stock receipt keeps the invoice's identity, rate and item link."""
	if _skip(doc):
		return

	_fill_purchase_return_links(doc)

	receipt_rows = [row for row in doc.items if row.get("purchase_invoice")]
	if not receipt_rows:
		return

	pi_names = sorted({row.get("purchase_invoice") for row in receipt_rows})
	pi_headers = {}
	pi_items = {}

	for name in pi_names:
		header = frappe.db.get_value(
			"Purchase Invoice",
			name,
			["company", "currency", "supplier", "conversion_rate", "update_stock", "docstatus"],
			as_dict=True,
		)
		pi_headers[name] = header or {}
		pi_items[name] = frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": name},
			fields=["name", "item_code", "rate", "conversion_factor", "uom"],
		)

	problems = integrity.purchase_receipt_rate_set_problems(
		[
			{"currency": header.get("currency"), "conversion_rate": header.get("conversion_rate")}
			for header in pi_headers.values()
			if header
		]
	)
	_raise(problems, "جریان لجستیک — رسید خرید")

	header = {
		"company": doc.company,
		"currency": doc.currency,
		"supplier": doc.supplier,
		"conversion_rate": doc.conversion_rate,
	}
	stock_flags = _item_stock_flags(row.get("item_code") for row in receipt_rows)

	rows = []
	for row in receipt_rows:
		pi_header = pi_headers.get(row.purchase_invoice) or {}
		pi_item = next(
			(
				item
				for item in pi_items.get(row.get("purchase_invoice"), [])
				if item.get("name") == row.get("purchase_invoice_item")
			),
			None,
		)
		rows.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"is_stock_item": bool(stock_flags.get(row.item_code)),
				"purchase_invoice": row.purchase_invoice,
				"purchase_invoice_item": row.get("purchase_invoice_item"),
				"rate": row.rate,
				"conversion_factor": row.conversion_factor,
				"uom": row.get("uom"),
				"pi_item_parent": (pi_item or {}).get("parent")
				or (row.purchase_invoice if pi_item else None),
				"pi_item_code": (pi_item or {}).get("item_code"),
				"pi_item_rate": (pi_item or {}).get("rate"),
				"pi_item_conversion_factor": (pi_item or {}).get("conversion_factor"),
				"pi_item_uom": (pi_item or {}).get("uom"),
				"pi_company": pi_header.get("company"),
				"pi_currency": pi_header.get("currency"),
				"pi_supplier": pi_header.get("supplier"),
				"pi_conversion_rate": pi_header.get("conversion_rate"),
				"pi_update_stock": pi_header.get("update_stock"),
				"pi_docstatus": pi_header.get("docstatus"),
			}
		)

	problems = integrity.purchase_receipt_problems(header, rows, is_return=bool(doc.is_return))
	_raise(problems, "جریان لجستیک — رسید خرید")
