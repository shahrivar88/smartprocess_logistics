"""Inventory and Procurement Dashboard: one row per Item."""

import frappe
from frappe import _

from smartprocess_logistics.warehouse import report_data


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = report_data.require_company(filters)
	rows = report_data.inventory_procurement_rows(company, filters)
	return columns(), rows


def columns():
	return [
		{"label": _("شرکت"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 120},
		{"label": _("کالا"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("موجودی فیزیکی"), "fieldname": "physical_stock", "fieldtype": "Float", "width": 110},
		{"label": _("موجودی ترانزیت"), "fieldname": "transit_stock", "fieldtype": "Float", "width": 110},
		{"label": _("موجودی مجازی"), "fieldname": "virtual_stock", "fieldtype": "Float", "width": 110},
		{"label": _("کل موجودی"), "fieldname": "total_stock", "fieldtype": "Float", "width": 100},
		{"label": _("تقاضای سفارش فروش"), "fieldname": "open_demand", "fieldtype": "Float", "width": 120},
		{
			"label": _("قابل تحویل فیزیکی"),
			"fieldname": "physically_deliverable",
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"label": _("آزاد برای فروش جدید"),
			"fieldname": "available_for_new_sales",
			"fieldtype": "Float",
			"width": 140,
		},
		{"label": _("کسری فوری"), "fieldname": "immediate_shortage", "fieldtype": "Float", "width": 100},
		{"label": _("کسری تأمین"), "fieldname": "procurement_shortage", "fieldtype": "Float", "width": 100},
		{
			"label": _("سفارش خرید باز"),
			"fieldname": "open_purchase_supply",
			"fieldtype": "Float",
			"width": 120,
		},
		{"label": _("پیشنهاد خرید"), "fieldname": "recommended_purchase", "fieldtype": "Float", "width": 110},
	]
