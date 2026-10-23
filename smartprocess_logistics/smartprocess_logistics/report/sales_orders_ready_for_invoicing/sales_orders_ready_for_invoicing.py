"""Sales Orders Ready for Invoicing.

Physical readiness and incoming (virtual) quantity stay separate. A zero-quantity value-only
credit note is shown for review but never creates a new invoice recommendation.
"""

import frappe
from frappe import _

from smartprocess_logistics.core import procurement
from smartprocess_logistics.warehouse import report_data

STATUS_LABELS = {
	procurement.READY: "آماده",
	procurement.INCOMING: "در راه",
	procurement.WAITING: "در انتظار تأمین",
}

REVIEW_LABELS = {
	procurement.CREDIT_NOTE_VALUE_ONLY: "اصلاح ارزشی بدون اثر مقداری — بررسی شود",
	procurement.CREDIT_NOTE_POSITIVE_RETURN: "برگشت با مقدار مثبت — بررسی شود",
	procurement.CREDIT_NOTE_NEGATIVE_WITHOUT_RETURN: "مقدار منفی بدون علامت برگشت — بررسی شود",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = report_data.require_company(filters)
	rows = report_data.ready_invoicing_rows(company, filters)

	for row in rows:
		row["status"] = STATUS_LABELS.get(row.get("readiness"), row.get("readiness"))
		row["review"] = "; ".join(
			REVIEW_LABELS.get(part, part) for part in str(row.get("review") or "").split(", ") if part
		)

	return columns(), rows


def columns():
	return [
		{
			"label": _("سفارش فروش"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 150,
		},
		{
			"label": _("مشتری"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 140,
		},
		{"label": _("تاریخ سفارش"), "fieldname": "order_date", "fieldtype": "Date", "width": 100},
		{"label": _("سن سفارش (روز)"), "fieldname": "age_days", "fieldtype": "Int", "width": 100},
		{"label": _("کالا"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("سفارش‌شده"), "fieldname": "ordered_qty", "fieldtype": "Float", "width": 90},
		{"label": _("فاکتورشده"), "fieldname": "invoiced_qty", "fieldtype": "Float", "width": 90},
		{
			"label": _("باقی‌ماندهٔ فاکتور"),
			"fieldname": "remaining_uninvoiced_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{"label": _("تحویل‌شده"), "fieldname": "delivered_qty", "fieldtype": "Float", "width": 90},
		{
			"label": _("تخصیص فیزیکی"),
			"fieldname": "allocated_physical_qty",
			"fieldtype": "Float",
			"width": 110,
		},
		{"label": _("تخصیص مجازی"), "fieldname": "allocated_virtual_qty", "fieldtype": "Float", "width": 110},
		{
			"label": _("پیشنهاد فاکتور آماده"),
			"fieldname": "suggested_invoice_qty",
			"fieldtype": "Float",
			"width": 130,
		},
		{"label": _("مقدار در راه"), "fieldname": "incoming_qty", "fieldtype": "Float", "width": 100},
		{"label": _("وضعیت"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("اطلاعات فاکتور"), "fieldname": "billing_note", "fieldtype": "Data", "width": 220},
		{"label": _("بررسی"), "fieldname": "review", "fieldtype": "Data", "width": 220},
	]
