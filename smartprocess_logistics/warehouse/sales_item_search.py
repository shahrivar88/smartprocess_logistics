"""Item Link queries for Sales Invoice, Quotation and Sales Order.

ERPNext's own `item_query` remains the search, customer-specific-item, permission and
pagination mechanism. This module calls it and then enriches the returned page with physical
and virtual stock from warehouses the caller may read. It never replaces the standard item
lookup with a second search implementation.

Each document's stock line is its own switch. With the switch off the page is the plain search
result, which is what the form shows without this app.
"""

import json

import frappe
from frappe.utils import cint

from smartprocess_logistics.core import inventory, sales_visibility
from smartprocess_logistics.warehouse import classification, company_scope, visibility_settings

SALES_INVOICE_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_invoice"
QUOTATION_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_quotation"
SALES_ORDER_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_order"

# Stock is enrichment for a search page, never an unbounded table read.
MAX_STOCK_ROWS = 10000
MAX_WAREHOUSE_ROWS = 1000


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
	if isinstance(filters, (list, tuple)):
		result = {}
		for row in filters:
			if isinstance(row, (list, tuple)) and len(row) >= 4:
				result[row[1]] = row[3]
		return result
	return {}


def _item_query(txt, searchfield, start, page_len, values):
	from erpnext.controllers.queries import item_query

	query_filters = {"is_sales_item": 1}
	if values.get("customer"):
		query_filters["customer"] = values.get("customer")

	return item_query(
		"Item",
		(txt or "").strip(),
		searchfield or "name",
		start,
		page_len,
		query_filters,
	)


def _permitted_warehouses(company):
	if not frappe.has_permission("Warehouse", "read"):
		return []
	rows = frappe.get_list(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0},
		fields=["name", "warehouse_type"],
		limit_page_length=MAX_WAREHOUSE_ROWS + 1,
	)
	return rows if len(rows) <= MAX_WAREHOUSE_ROWS else []


def _stock_by_item(company, item_codes):
	warehouses = _permitted_warehouses(company)
	if not warehouses or not item_codes or not frappe.has_permission("Bin", "read"):
		return {}

	types = {row.get("name"): row.get("warehouse_type") for row in warehouses}
	names = tuple(sorted(types))
	bins = frappe.get_list(
		"Bin",
		filters={"company": company, "warehouse": ("in", names), "item_code": ("in", tuple(item_codes))},
		fields=["item_code", "warehouse", "actual_qty", "reserved_qty"],
		limit_page_length=MAX_STOCK_ROWS + 1,
	)
	if len(bins) > MAX_STOCK_ROWS:
		return {}
	rows = [
		inventory.WarehouseStock(
			company=company,
			item_code=row.get("item_code"),
			warehouse=row.get("warehouse"),
			warehouse_type=types.get(row.get("warehouse")),
			actual_qty=row.get("actual_qty"),
			reserved_qty=row.get("reserved_qty"),
		)
		for row in bins
	]
	return inventory.stock_by_item(
		rows,
		company,
		permitted_warehouses=set(names),
		virtual_types=classification.virtual_types(),
	)


def _item_details(item_codes):
	if not item_codes or not frappe.has_permission("Item", "read"):
		return {}
	return {
		row.get("name"): row
		for row in frappe.get_list(
			"Item",
			filters={"name": ("in", tuple(item_codes))},
			fields=["name", "item_name", "stock_uom"],
			limit_page_length=0,
		)
	}


def _items(txt, searchfield, start, page_len, filters, *, include_virtual, setting):
	values = _filters_dict(filters)
	company = company_scope.require_company(values.get("company"))
	if not company:
		return []
	if not frappe.has_permission("Item", "read"):
		return []

	page_len = max(1, min(cint(page_len) or 10, 500))
	start = max(0, cint(start) or 0)
	raw = _item_query(txt, searchfield, start, page_len, values)
	if not raw:
		return []

	items = []
	for row in raw:
		if isinstance(row, dict):
			code = row.get("name") or row.get("value")
			name = row.get("item_name") or row.get("label")
		else:
			code = row[0] if len(row) else None
			name = row[1] if len(row) > 1 else None
		if code:
			items.append((str(code), name or ""))

	if not items:
		return []
	if not visibility_settings.load()[setting]:
		return items

	codes = [code for code, _name in items]
	stock = _stock_by_item(company, codes)
	details = _item_details(codes)
	result = []

	for code, name in items:
		item_stock = stock.get(code)
		physical = item_stock.physical if item_stock else 0
		virtual = item_stock.virtual if item_stock else 0
		detail = details.get(code) or {}
		result.append(
			sales_visibility.item_suggestion(
				code,
				name or detail.get("item_name") or "",
				physical,
				virtual,
				detail.get("stock_uom") or "",
				include_virtual=include_virtual,
			)
		)

	return result


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def items_for_sales_invoice(doctype, txt, searchfield, start, page_len, filters):
	"""Standard Item search plus authorized physical stock for a Sales Invoice."""
	if doctype != "Item":
		return []
	return _items(
		txt,
		searchfield,
		start,
		page_len,
		filters,
		include_virtual=False,
		setting=visibility_settings.SHOW_SI_ITEM_STOCK,
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def items_for_quotation(doctype, txt, searchfield, start, page_len, filters):
	"""Standard Item search plus separate physical and virtual stock for a Quotation."""
	if doctype != "Item":
		return []
	return _items(
		txt,
		searchfield,
		start,
		page_len,
		filters,
		include_virtual=True,
		setting=visibility_settings.SHOW_QUOTATION_ITEM_STOCK,
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def items_for_sales_order(doctype, txt, searchfield, start, page_len, filters):
	"""Standard Item search plus separate physical and virtual stock for a Sales Order."""
	if doctype != "Item":
		return []
	return _items(
		txt,
		searchfield,
		start,
		page_len,
		filters,
		include_virtual=True,
		setting=visibility_settings.SHOW_SO_ITEM_STOCK,
	)
