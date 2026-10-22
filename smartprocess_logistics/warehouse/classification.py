"""Read the standard Warehouse Type as the virtual/physical classification.

ERPNext v16's `Warehouse Type` DocType is a label: it has a name and a description, and no
controller reads it for availability, permissions or ledger behaviour. It is still the right
selector because it is a standard field on every Warehouse, it is company-neutral and it needs
no schema change. These helpers read it once per warehouse and apply the pure rules in
`policy.py`; the enforcement is in the server-side handlers.
"""

import json

import frappe

from smartprocess_logistics.warehouse import policy

CONFIG_OVERRIDE = "sp_virtual_warehouse_types"


def _configured_types():
	value = frappe.local.conf.get(CONFIG_OVERRIDE)
	if not value:
		return None

	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			value = [part.strip() for part in value.split(",") if part.strip()]

	if isinstance(value, str):
		value = [value]

	if not isinstance(value, (list, tuple, set)):
		frappe.throw(
			"تنظیم sp_virtual_warehouse_types باید فهرستی از نام‌های Warehouse Type باشد.",
			title="جریان انبار — طبقه‌بندی انبار",
		)

	types = tuple(str(item).strip() for item in value if str(item).strip())
	if not types:
		frappe.throw(
			"تنظیم sp_virtual_warehouse_types خالی است؛ برای جلوگیری از طبقه‌بندی ناامن متوقف شد.",
			title="جریان انبار — طبقه‌بندی انبار",
		)
	return types


def virtual_types():
	"""The configured type names, or the safe default."""
	return _configured_types() or policy.DEFAULT_VIRTUAL_TYPES


def is_virtual(warehouse):
	"""Whether this warehouse is classified as virtual, via its standard Warehouse Type."""
	if not warehouse:
		return False
	warehouse_type = frappe.get_cached_value("Warehouse", warehouse, "warehouse_type")
	return policy.is_virtual_type(warehouse_type, virtual_types())


def physical_warehouses(company):
	"""Leaf warehouses of a company whose type is not virtual."""
	types = virtual_types()
	rows = frappe.get_list(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		fields=["name", "warehouse_type"],
		limit_page_length=0,
	)
	return tuple(
		row.get("name") for row in rows if not policy.is_virtual_type(row.get("warehouse_type"), types)
	)
