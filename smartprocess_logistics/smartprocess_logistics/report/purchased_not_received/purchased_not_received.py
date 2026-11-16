"""Purchased Not Received report: one row per submitted Purchase Invoice Item.

The status, the outstanding quantity and the physical arrival are decided in
`core/receipts.py`; this module only reads through the permission-aware loader and puts the
Persian words on the answers.
"""

import frappe
from frappe import _

from smartprocess_logistics.core import receipts
from smartprocess_logistics.warehouse import report_data


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = report_data.require_company(filters)
	rows = report_data.purchased_not_received(company, filters)
	for row in rows:
		row["receipt_status"] = receipts.STATUS_LABELS.get(row.get("receipt_status"), "")
		row["notes"] = "؛ ".join(
			receipts.NOTE_LABELS[code] for code in row.pop("note_codes", ()) if code in receipts.NOTE_LABELS
		)
	return columns(), rows


def columns():
	return [
		{
			"label": _("فاکتور خرید"),
			"fieldname": "purchase_invoice",
			"fieldtype": "Link",
			"options": "Purchase Invoice",
			"width": 150,
		},
		{"label": _("تاریخ فاکتور"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("تأمین‌کننده"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 140,
		},
		{"label": _("کد کالا"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 110},
		{"label": _("نام کالا"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{"label": _("مقدار خرید"), "fieldname": "purchased_qty", "fieldtype": "Float", "width": 100},
		{"label": _("مقدار دریافت‌شده"), "fieldname": "received_qty", "fieldtype": "Float", "width": 110},
		{"label": _("مقدار باقی‌مانده"), "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 110},
		{"label": _("واحد"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 80},
		{"label": _("انبار مقصد"), "fieldname": "warehouse", "fieldtype": "Data", "width": 180},
		{
			"label": _("ارز"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 80,
		},
		{"label": _("وضعیت دریافت"), "fieldname": "receipt_status", "fieldtype": "Data", "width": 110},
		{"label": _("ورود فیزیکی"), "fieldname": "physical_qty", "fieldtype": "Float", "width": 110},
		{"label": _("یادداشت"), "fieldname": "notes", "fieldtype": "Data", "width": 260},
	]
