"""The Item Code suggestions for a source warehouse, for a standard Stock Entry Link field.

ERPNext's Stock Entry points `item_code` at `erpnext.controllers.queries.item_query`, which
filters Items by themselves — stock item, enabled, searchable by code and name — and knows
nothing about stock in a warehouse. Its warehouse helper is the opposite direction: it filters
warehouses by the chosen item. So the filter the operator asked for does not exist in v16 and
is added here as a custom Link query, the mechanism the form already supports.

This module is the permission gate. `core.stock_search` writes the statement; before it runs,
the caller must be able to read the warehouse, the warehouse must belong to the document's
company, and the user must be able to read Items at all. Item-level restrictions (role
permissions, Item user permissions, `if_owner`, the site's permission-query hooks) come from
`get_match_cond`, the same fragment ERPNext's own `item_query` uses on raw SQL.

Nothing here writes. A refusal returns an empty list rather than an error, so a caller who
guesses a warehouse name learns nothing from the answer.
"""

import json

import frappe
from frappe.desk.reportview import get_match_cond
from frappe.utils import cint, today

from smartprocess_logistics.core import stock_search

QUERY_METHOD = "smartprocess_logistics.warehouse.item_search.items_in_stock"


def _filters_dict(filters):
	"""The Link sends a dict, a JSON string of one, or a list of filter rows."""
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


def _warehouse_readable(warehouse, company):
	"""Whether this caller may see stock in this warehouse for this company."""
	if not warehouse:
		return False

	details = frappe.db.get_value("Warehouse", warehouse, ["company", "is_group"], as_dict=True)
	if not details or details.is_group:
		return False
	if company and details.company != company:
		return False
	if not frappe.has_permission("Warehouse", "read", doc=warehouse):
		return False

	return True


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def items_in_stock(doctype, txt, searchfield, start, page_len, filters):
	"""Items with positive stock in one warehouse, for the Stock Entry Item Code Link."""
	if doctype != "Item":
		return []

	values = stock_search.filter_values(_filters_dict(filters))
	if values is None:
		return []

	warehouse = values["warehouse"]
	company = values["company"]
	item_code = values["item_code"]

	if not _warehouse_readable(warehouse, company):
		return []
	if not frappe.has_permission("Item", "read"):
		return []

	query, params = stock_search.build_query(
		warehouse=warehouse,
		txt=(txt or "").strip(),
		start=cint(start),
		page_len=cint(page_len),
		today=today(),
		match_condition=get_match_cond("Item"),
		item_code=item_code or None,
	)

	rows = frappe.db.sql(query, params)
	return [stock_search.suggestion(row[0], row[1], row[2], row[3]) for row in rows]
