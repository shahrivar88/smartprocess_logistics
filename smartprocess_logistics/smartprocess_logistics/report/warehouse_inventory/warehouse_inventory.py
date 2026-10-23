"""Warehouse Inventory report: one row per Item and Warehouse."""

import frappe
from frappe import _

from smartprocess_logistics.warehouse import report_data


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = report_data.require_company(filters)
	rows = report_data.warehouse_inventory(company, filters)
	for row in rows:
		row["classification"] = "مجازی" if row.get("classification") == "virtual" else "فیزیکی"
	return columns(), rows


def columns():
	return [
		{"label": _("شرکت"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 120},
		{"label": _("کالا"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 110},
		{"label": _("نام کالا"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{
			"label": _("گروه کالا"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 120,
		},
		{
			"label": _("انبار"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 150,
		},
		{"label": _("نوع انبار"), "fieldname": "warehouse_type", "fieldtype": "Data", "width": 100},
		{"label": _("طبقه‌بندی"), "fieldname": "classification", "fieldtype": "Data", "width": 90},
		{"label": _("موجودی واقعی"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 100},
		{"label": _("واحد"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 80},
		{"label": _("ارزش موجودی"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 120},
		{"label": _("نرخ ارزش‌گذاری"), "fieldname": "valuation_rate", "fieldtype": "Currency", "width": 120},
	]
