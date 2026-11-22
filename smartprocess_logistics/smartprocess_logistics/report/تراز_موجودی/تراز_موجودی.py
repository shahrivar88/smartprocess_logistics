"""Operational inventory and order shortage: sellable stock, goods on the way, open demand.

Every quantity is in the Item's Stock UOM. Virtual stock is not in this report at all. Open
Purchase Orders are shown as their own supply column and net the purchase suggestion; they are
not part of the shortage, by product rule. The arithmetic is in `core.fulfilment`, the reads are
in `warehouse.report_data`.
"""

import frappe
from frappe import _

from smartprocess_logistics.core import inventory
from smartprocess_logistics.warehouse import report_data

REPORT_NAME = "تراز موجودی"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = report_data.require_company(filters)
	rows = report_data.operational_fulfilment_rows(company, filters)

	return columns(), rows, None, None, summary(rows)


def columns():
	return [
		{
			"label": _("کالا"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{"label": _("نام کالا"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{
			"label": _("گروه کالا"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 130,
		},
		{"label": _("واحد"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{
			"sp_quantity": 1,
			"label": _("موجودی قابل فروش"),
			"fieldname": "physical_stock",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"sp_quantity": 1,
			"label": _("موجودی قابل سفارش"),
			"fieldname": "transit_stock",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"sp_quantity": 1,
			"label": _("پیش‌فاکتورهای باز"),
			"fieldname": "quotation_demand",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"sp_quantity": 1,
			"label": _("سفارشات فروش باز"),
			"fieldname": "sales_order_demand",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"sp_quantity": 1,
			"label": _("کل تقاضا"),
			"fieldname": "total_demand",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"sp_quantity": 1,
			"label": _("کسری سفارش"),
			"fieldname": "shortage",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"sp_quantity": 1,
			"label": _("سفارش خرید باز"),
			"fieldname": "open_purchase_supply",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"sp_quantity": 1,
			"label": _("پیشنهاد خرید"),
			"fieldname": "suggested_purchase",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("پیش‌فاکتورهای مرتبط"),
			"fieldname": "related_quotations",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("سفارشات فروش مرتبط"),
			"fieldname": "related_sales_orders",
			"fieldtype": "Data",
			"width": 200,
		},
	]


def summary(rows):
	"""Shortage count and demand totals, shown above the table."""
	short = [row for row in rows if row.get("shortage", 0) > 0]
	demand = inventory.quantity(sum((row.get("total_demand", 0) for row in rows), inventory.ZERO))
	shortage = inventory.quantity(sum((row.get("shortage", 0) for row in short), inventory.ZERO))

	return [
		{
			"label": _("اقلام دارای کسری"),
			"value": len(short),
			"datatype": "Int",
			"indicator": "Red" if short else "Green",
		},
		{"label": _("مجموع تقاضا"), "value": demand, "datatype": "Float"},
		{
			"label": _("مجموع کسری"),
			"value": shortage,
			"datatype": "Float",
			"indicator": "Red" if shortage > 0 else "Green",
		},
	]
