"""Purchase Receipt handler for the quantity-based, warehouse-neutral purchase flow."""

import frappe

from smartprocess_logistics.purchase import rules

MANAGED_FIELD = "sp_serial_required"


def _skip(doc):
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_patch", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True
	return doc.docstatus != 0 and getattr(doc, "_action", None) != "submit"


def _item_flags(item_codes):
	codes = tuple(sorted({code for code in item_codes if code}))
	if not codes or not frappe.get_meta("Item").get_field(MANAGED_FIELD):
		return {}

	return {
		row.get("name"): row
		for row in frappe.get_all(
			"Item",
			filters={"name": ("in", codes)},
			fields=["name", "is_stock_item", "has_serial_no", MANAGED_FIELD],
		)
	}


def validate_purchase_receipt(doc, method=None):
	if _skip(doc) or doc.is_return:
		return

	items = _item_flags(row.get("item_code") for row in doc.items)
	invoice_names = tuple(
		sorted({row.get("purchase_invoice") for row in doc.items if row.get("purchase_invoice")})
	)
	invoice_status = {}
	if invoice_names:
		invoice_status = {
			row.get("name"): row.get("docstatus")
			for row in frappe.get_all(
				"Purchase Invoice",
				filters={"name": ("in", invoice_names)},
				fields=["name", "docstatus"],
			)
		}
	rows = []

	for row in doc.items:
		item = items.get(row.item_code) or {}
		rows.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"is_stock_item": item.get("is_stock_item"),
				"is_managed": bool(item.get(MANAGED_FIELD)),
				"has_serial_no": item.get("has_serial_no"),
				"qty": row.get("qty"),
				"purchase_invoice": row.get("purchase_invoice"),
				"purchase_invoice_docstatus": invoice_status.get(row.get("purchase_invoice")),
			}
		)

	problems = rules.stock_receipt_problems(rows)
	if problems:
		frappe.throw(problems[0].message, title="جریان خرید — رسید خرید")
