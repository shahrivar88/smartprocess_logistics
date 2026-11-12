"""Purchase Receipt handler for the quantity-based, warehouse-neutral purchase flow."""

import frappe

from smartprocess_logistics.purchase import rules
from smartprocess_logistics.warehouse import classification, policy

MANAGED_FIELD = "sp_serial_required"


def _skip(doc):
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_patch", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True
	return doc.docstatus != 0 and getattr(doc, "_action", None) != "submit"


def _item_flags(item_codes):
	"""Stock flags per Item.

	The managed flag is optional — the invoice-first and rejected-warehouse rules do not depend
	on it — so a site whose custom field is not synced yet must still get those rules. Reading
	the standard flags unconditionally is the point: an absent custom field must not switch the
	whole handler off.
	"""
	codes = tuple(sorted({code for code in item_codes if code}))
	if not codes:
		return {}

	fields = ["name", "is_stock_item", "has_serial_no"]
	if frappe.get_meta("Item").get_field(MANAGED_FIELD):
		fields.append(MANAGED_FIELD)

	return {
		row.get("name"): row
		for row in frappe.get_all("Item", filters={"name": ("in", codes)}, fields=fields)
	}


def validate_purchase_receipt(doc, method=None):
	if _skip(doc):
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
	rejected_names = tuple(
		sorted({row.get("rejected_warehouse") for row in doc.items if row.get("rejected_warehouse")})
	)
	rejected_types = {}
	if rejected_names:
		rejected_types = {
			row.get("name"): row.get("warehouse_type")
			for row in frappe.get_all(
				"Warehouse",
				filters={"name": ("in", rejected_names), "company": doc.company},
				fields=["name", "warehouse_type"],
				limit_page_length=0,
			)
		}
	virtual_types = classification.virtual_types()
	rows = []

	for row in doc.items:
		item = items.get(row.item_code) or {}
		rejected_warehouse = row.get("rejected_warehouse")
		rows.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"is_stock_item": item.get("is_stock_item"),
				"is_managed": bool(item.get(MANAGED_FIELD)),
				"has_serial_no": item.get("has_serial_no"),
				"qty": row.get("qty"),
				"rejected_qty": row.get("rejected_qty"),
				"rejected_warehouse": rejected_warehouse,
				"rejected_warehouse_kind": policy.kind(
					rejected_types.get(rejected_warehouse), virtual_types
				)
				if rejected_warehouse
				else None,
				"purchase_invoice": row.get("purchase_invoice"),
				"purchase_invoice_docstatus": invoice_status.get(row.get("purchase_invoice")),
			}
		)

	problems = rules.stock_receipt_problems(rows, invoice_first=not doc.is_return)
	if problems:
		frappe.throw(problems[0].message, title="جریان خرید — رسید خرید")
