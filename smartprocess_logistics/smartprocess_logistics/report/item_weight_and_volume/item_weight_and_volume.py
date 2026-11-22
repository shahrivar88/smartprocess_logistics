"""Item Weight and Volume for purchase documents and warehouse inventory.

A document's net weight comes from its own native transaction snapshot. Gross weight and volume
are only shown when the transaction row actually carries them; otherwise the source column says
"unavailable" rather than presenting the current Item specification as a historical fact.
"""

import frappe
from frappe import _

from smartprocess_logistics.warehouse import report_data

SOURCE_LABELS = {
	"native_snapshot": "عکس لحظه‌ای سند",
	"native_net_snapshot": "وزن خالص سند",
	"native_item_weight": "وزن کالای سند",
	"historical_snapshot": "عکس لحظه‌ای تاریخی",
	"historical": "عکس لحظه‌ای تاریخی",
	"current": "مشخصات فعلی کالا",
	"current_item": "مشخصات فعلی کالا",
	"document_total": "جمع سند",
	"document_partial": "جمع ناقص سند",
	"unavailable": "ناموجود",
}

DOCUMENT_LABELS = {
	"Purchase Invoice": "فاکتور خرید",
	"Purchase Order": "سفارش خرید",
	"Purchase Receipt": "رسید خرید",
	"Warehouse Inventory": "موجودی انبار",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = report_data.require_company(filters)
	rows = report_data.weight_volume_rows(company, filters)

	for row in rows:
		row["document_type"] = DOCUMENT_LABELS.get(row.get("document_type"), row.get("document_type"))
		for field in ("measurement_source", "net_source", "gross_source", "volume_source"):
			row[field] = SOURCE_LABELS.get(row.get(field), row.get(field) or "ناموجود")

	return columns(), rows


def columns():
	return [
		{"label": _("نوع ردیف"), "fieldname": "row_type", "fieldtype": "Data", "width": 90},
		{"label": _("نوع سند"), "fieldname": "document_type", "fieldtype": "Data", "width": 110},
		{"label": _("سند"), "fieldname": "document", "fieldtype": "Data", "width": 150},
		{"label": _("تاریخ"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("تأمین‌کننده"), "fieldname": "party", "fieldtype": "Data", "width": 120},
		{"label": _("شناسه بار"), "fieldname": "cargo_ref", "fieldtype": "Data", "width": 130},
		{"label": _("کالا"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 110},
		{"label": _("نام کالا"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{
			"sp_quantity": 1,
			"label": _("مقدار (Stock UOM)"),
			"fieldname": "quantity",
			"fieldtype": "Float",
			"width": 120,
		},
		{"label": _("وزن خالص"), "fieldname": "net_weight", "fieldtype": "Float", "width": 100},
		{"label": _("وزن ناخالص"), "fieldname": "gross_weight", "fieldtype": "Float", "width": 100},
		{"label": _("حجم (cm³)"), "fieldname": "volume_cm3", "fieldtype": "Float", "width": 100},
		{"label": _("حجم (m³)"), "fieldname": "volume_m3", "fieldtype": "Float", "width": 100},
		{
			"label": _("واحد وزن"),
			"fieldname": "weight_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 90,
		},
		{
			"sp_quantity": 1,
			"label": _("مقدار ردشده (Stock UOM)"),
			"fieldname": "rejected_qty",
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"label": _("وزن خالص ردشده"),
			"fieldname": "rejected_net_weight",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("وزن ناخالص ردشده"),
			"fieldname": "rejected_gross_weight",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("حجم ردشده (cm³)"),
			"fieldname": "rejected_volume_cm3",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("حجم ردشده (m³)"),
			"fieldname": "rejected_volume_m3",
			"fieldtype": "Float",
			"width": 120,
		},
		{"label": _("تعداد ردیف سند"), "fieldname": "rows_count", "fieldtype": "Int", "width": 100},
		{
			"label": _("پوشش اندازه‌گیری"),
			"fieldname": "coverage",
			"fieldtype": "Data",
			"width": 220,
		},
		{"label": _("هشدار"), "fieldname": "warning", "fieldtype": "Data", "width": 280},
		{"label": _("منبع وزن خالص"), "fieldname": "net_source", "fieldtype": "Data", "width": 130},
		{"label": _("منبع وزن ناخالص"), "fieldname": "gross_source", "fieldtype": "Data", "width": 130},
		{"label": _("منبع حجم"), "fieldname": "volume_source", "fieldtype": "Data", "width": 110},
	]
