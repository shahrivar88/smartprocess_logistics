"""Warehouse Link queries for the three sales forms.

The native Warehouse Link knows company and group rules but not this application's physical /
virtual classification. These queries keep the native permission path (`frappe.get_list`) and
add the classification, including the `warehouse_type IS NULL` case that a simple `NOT IN`
would have excluded.

One read serves all three selectors. The Sales Invoice one offers physical warehouses and is
the only one that can be restricted to them; Quotation and Sales Order only plan, so their
selectors offer both kinds. Whether a selector carries the item's stock per warehouse is each
document's own switch.

A virtual warehouse that a selector offers is labelled as one. It is a real warehouse with real
stock, but this application refuses it as a sales source when the Sales Invoice or Delivery Note
built from the row is saved, and a Stock Reservation Entry for it is refused at submit. The
label is the only place an operator is told that before they choose it; the refusal itself
stays in `warehouse/sales.py`.
"""

import json

import frappe
from frappe.utils import cint

from smartprocess_logistics.core import sales_visibility
from smartprocess_logistics.warehouse import (
	classification,
	company_scope,
	policy,
	visibility_settings,
)

QUERY_METHOD = "smartprocess_logistics.warehouse.physical_warehouse_query.physical_warehouses"
QUOTATION_QUERY_METHOD = (
	"smartprocess_logistics.warehouse.physical_warehouse_query.quotation_warehouses"
)
SALES_ORDER_QUERY_METHOD = (
	"smartprocess_logistics.warehouse.physical_warehouse_query.sales_order_warehouses"
)
MAX_WAREHOUSE_ROWS = 2000
VIRTUAL_ROW_LABEL = "انبار مجازی"


def _filters_dict(filters):
	if not filters:
		return {}
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			return {}
	if isinstance(filters, dict):
		return filters
	return {}


def _item_code(values):
	value = values.get("item_code")
	return value.strip() if isinstance(value, str) else ""


def _stock_by_warehouse(company, warehouses, item_code):
	"""One Bin read for the visible page; `None` means the caller cannot see it."""
	if not frappe.has_permission("Item", "read") or not frappe.has_permission("Bin", "read"):
		return None
	rows = frappe.get_list(
		"Bin",
		filters={
			"company": company,
			"warehouse": ("in", tuple(warehouses)),
			"item_code": item_code,
		},
		fields=["warehouse", "actual_qty"],
		limit_page_length=len(warehouses),
	)
	return {row.get("warehouse"): row.get("actual_qty") for row in rows}


def _warehouse_label(row, virtual_types):
	"""The name line; a virtual warehouse says so, because it cannot supply a sale."""
	label = row.get("warehouse_name") or ""
	if not policy.is_virtual_type(row.get("warehouse_type"), virtual_types):
		return label
	return f"{label} — {VIRTUAL_ROW_LABEL}" if label else VIRTUAL_ROW_LABEL


def _page(doctype, txt, start, page_len, filters, *, physical_only, show_stock):
	"""One page of leaf, enabled warehouses of the company, with an optional stock line."""
	if doctype != "Warehouse":
		return []

	values = _filters_dict(filters)
	company = company_scope.require_company(values.get("company"))
	if not company:
		return []
	if not frappe.has_permission("Warehouse", "read"):
		return []

	page_len = max(1, min(cint(page_len) or 20, 500))
	start = max(0, cint(start) or 0)
	or_filters = None
	if txt:
		pattern = f"%{txt}%"
		or_filters = [
			["Warehouse", "name", "like", pattern],
			["Warehouse", "warehouse_name", "like", pattern],
		]

	rows = frappe.get_list(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0},
		or_filters=or_filters,
		fields=["name", "warehouse_name", "warehouse_type"],
		limit_page_length=MAX_WAREHOUSE_ROWS + 1,
	)
	if len(rows) > MAX_WAREHOUSE_ROWS:
		frappe.throw(
			"تعداد انبارهای قابل انتخاب از سقف مجاز بیشتر است؛ جست‌وجو را محدود کنید.",
			frappe.ValidationError,
		)
	virtual_types = classification.virtual_types()
	if physical_only:
		rows = [row for row in rows if not policy.is_virtual_type(row.get("warehouse_type"), virtual_types)]

	page = rows[start : start + page_len]
	if physical_only:
		base = [(row.get("name"), row.get("warehouse_name") or "") for row in page]
	else:
		base = [(row.get("name"), _warehouse_label(row, virtual_types)) for row in page]

	item_code = _item_code(values)
	if not show_stock or not item_code or not page:
		return base

	stock = _stock_by_warehouse(company, [row.get("name") for row in page], item_code)
	if stock is None:
		return base

	return [
		(name, warehouse_name, sales_visibility.warehouse_stock_text(stock.get(name, 0)))
		for name, warehouse_name in base
	]


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def physical_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Sales Invoice: physical warehouses, unless the operator restores the standard list."""
	flags = visibility_settings.load()

	return _page(
		doctype,
		txt,
		start,
		page_len,
		filters,
		physical_only=flags[visibility_settings.SI_PHYSICAL_WAREHOUSES_ONLY],
		show_stock=flags[visibility_settings.SHOW_SI_WAREHOUSE_STOCK],
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def quotation_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Quotation: every warehouse of the company, because the document only plans."""
	flags = visibility_settings.load()

	return _page(
		doctype,
		txt,
		start,
		page_len,
		filters,
		physical_only=False,
		show_stock=flags[visibility_settings.SHOW_QUOTATION_WAREHOUSE_STOCK],
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def sales_order_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Sales Order: every warehouse of the company, because the document only plans."""
	flags = visibility_settings.load()

	return _page(
		doctype,
		txt,
		start,
		page_len,
		filters,
		physical_only=False,
		show_stock=flags[visibility_settings.SHOW_SO_WAREHOUSE_STOCK],
	)
