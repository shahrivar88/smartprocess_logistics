"""Physical-warehouse Link query for stock-affecting Sales Invoice fields.

The native Warehouse Link knows company and group rules but not this application's physical /
virtual classification. This query keeps the native permission path (`frappe.get_list`) and
adds the classification, including the `warehouse_type IS NULL` case that a simple `NOT IN`
would have excluded.
"""

import json

import frappe
from frappe.utils import cint

from smartprocess_logistics.warehouse import classification, company_scope, policy

QUERY_METHOD = "smartprocess_logistics.warehouse.physical_warehouse_query.physical_warehouses"
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


@frappe.whitelist(methods=["GET"])
@frappe.validate_and_sanitize_search_inputs
def physical_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""Warehouses of the current company that are leaf, enabled and physical."""
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
	physical = [row for row in rows if not policy.is_virtual_type(row.get("warehouse_type"), virtual_types)]

	return [(row.get("name"), row.get("warehouse_name") or "") for row in physical[start : start + page_len]]
