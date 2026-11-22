"""Warehouse Link queries for the three sales forms and the purchase receipt.

The native Warehouse Link knows company and group rules but not this application's physical /
virtual classification. These queries keep the native permission path (`frappe.get_list`) and
add the classification, including the `warehouse_type IS NULL` case that a simple `NOT IN`
would have excluded.

One read serves all four selectors. The Sales Invoice one offers physical warehouses and is
the only one that can be restricted to them; Quotation and Sales Order only plan, so their
selectors offer physical and Transit warehouses. Virtual stock is never an operational source,
so a Virtual warehouse is not offered there. Whether a selector carries the item's stock per
warehouse is each document's own switch. The Purchase Receipt selector offers every warehouse
of the company — the receipt names where goods arrived, including a virtual one — and its
second line names the warehouse's type instead of its stock.

A row is the warehouse's name and, when its switch is on, one description line: the item's own
stock there. The Frappe Link already draws the name as the value, so nothing repeats it on the
description line.

`hide_empty=1` in the Link filters drops the warehouses where the chosen item holds no stock.
It is a per-request filter, not a setting: the Sales Invoice form offers it as a per-user view
switch, and the flag only ever narrows the list.
"""

import json

import frappe
from frappe.utils import cint

from smartprocess_logistics.core import sales_visibility, selection_filters
from smartprocess_logistics.warehouse import (
	classification,
	company_scope,
	policy,
	visibility_settings,
)

QUERY_METHOD = "smartprocess_logistics.warehouse.physical_warehouse_query.physical_warehouses"
QUOTATION_QUERY_METHOD = "smartprocess_logistics.warehouse.physical_warehouse_query.quotation_warehouses"
SALES_ORDER_QUERY_METHOD = "smartprocess_logistics.warehouse.physical_warehouse_query.sales_order_warehouses"
PURCHASE_RECEIPT_QUERY_METHOD = (
	"smartprocess_logistics.warehouse.physical_warehouse_query.purchase_receipt_warehouses"
)
MAX_WAREHOUSE_ROWS = 2000


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


def _page(
	doctype, txt, start, page_len, filters, *, physical_only, exclude_virtual, show_stock, show_type=False
):
	"""One page of leaf, enabled warehouses of the company, with an optional stock or type line."""
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
		fields=["name", "warehouse_type"],
		limit_page_length=MAX_WAREHOUSE_ROWS + 1,
	)
	if len(rows) > MAX_WAREHOUSE_ROWS:
		frappe.throw(
			"تعداد انبارهای قابل انتخاب از سقف مجاز بیشتر است؛ جست‌وجو را محدود کنید.",
			frappe.ValidationError,
		)
	virtual_types = classification.virtual_types()
	kinds = {row.get("name"): policy.kind(row.get("warehouse_type"), virtual_types) for row in rows}
	if physical_only:
		rows = [row for row in rows if kinds.get(row.get("name")) == policy.PHYSICAL]
	elif exclude_virtual:
		rows = [row for row in rows if kinds.get(row.get("name")) != policy.VIRTUAL]

	item_code = _item_code(values)
	hide_empty = selection_filters.wants(values.get("hide_empty"))
	stock = None

	if hide_empty and item_code and rows:
		# Filter before paging, so the page is filled with rows that match. A caller who cannot
		# read `Bin` gets the unfiltered list: `None` is "not answered", not "empty".
		stock = _stock_by_warehouse(company, [row.get("name") for row in rows], item_code)
		if stock is not None:
			rows = [
				row for row in rows if selection_filters.keep_warehouse(stock.get(row.get("name"), 0), True)
			]

	page = rows[start : start + page_len]
	names = [row.get("name") for row in page]

	if show_type:
		# The name stays the value the Link stores; the type is only its second line. A type the
		# policy does not map falls back to the physical label, exactly as `policy.kind` does.
		return [
			(
				name,
				policy.CLASSIFICATION_LABELS.get(
					kinds.get(name), policy.CLASSIFICATION_LABELS[policy.PHYSICAL]
				),
			)
			for name in names
		]

	if not show_stock or not item_code or not page:
		return [(name,) for name in names]

	if stock is None:
		stock = _stock_by_warehouse(company, names, item_code)
	if stock is None:
		return [(name,) for name in names]

	return [
		(
			name,
			sales_visibility.warehouse_stock_text(stock.get(name, 0), kinds.get(name)),
		)
		for name in names
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
		exclude_virtual=False,
		show_stock=flags[visibility_settings.SHOW_SI_WAREHOUSE_STOCK],
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def quotation_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Quotation: physical and transit warehouses; virtual warehouses are excluded."""
	flags = visibility_settings.load()

	return _page(
		doctype,
		txt,
		start,
		page_len,
		filters,
		physical_only=False,
		exclude_virtual=True,
		show_stock=flags[visibility_settings.SHOW_QUOTATION_WAREHOUSE_STOCK],
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def sales_order_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Sales Order: physical and transit warehouses; virtual warehouses are excluded."""
	flags = visibility_settings.load()

	return _page(
		doctype,
		txt,
		start,
		page_len,
		filters,
		physical_only=False,
		exclude_virtual=True,
		show_stock=flags[visibility_settings.SHOW_SO_WAREHOUSE_STOCK],
	)


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def purchase_receipt_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Purchase Receipt: every warehouse of the company, with its type on a second line.

	The list, its search and its company rule are the native ones — leaf, enabled warehouses of
	the company the document names. Only the description line is this app's, and the setting
	turns that one line on or off; no warehouse is added, removed or renamed by either state.
	"""
	flags = visibility_settings.load()

	return _page(
		doctype,
		txt,
		start,
		page_len,
		filters,
		physical_only=False,
		exclude_virtual=False,
		show_stock=False,
		show_type=flags[visibility_settings.SHOW_PR_WAREHOUSE_TYPE],
	)
