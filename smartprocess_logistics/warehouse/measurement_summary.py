"""Frappe-facing measurement rows, document summaries and Persian coverage text."""

from decimal import Decimal

import frappe
from erpnext.stock.doctype.item.item import get_uom_conv_factor

from smartprocess_logistics.core import measurement_totals, measurements
from smartprocess_logistics.core.inventory import as_decimal, positive_factor

ITEM = "Item"


def make_converter():
	"""A cached `(from_uom, to_uom) -> Decimal | None` conversion callable."""
	cache = {}

	def convert(from_uom, to_uom):
		if not from_uom or not to_uom:
			return None
		if from_uom == to_uom:
			return Decimal("1")
		key = (from_uom, to_uom)
		if key not in cache:
			value = get_uom_conv_factor(from_uom, to_uom)
			factor = as_decimal(value, "conversion_factor") if value else None
			cache[key] = factor if factor and factor > 0 else None
		return cache[key]

	return convert


def _item_values(codes):
	codes = tuple(sorted({code for code in codes if code}))
	if not codes or not frappe.has_permission(ITEM, "read"):
		return {}

	meta = frappe.get_meta(ITEM)
	fields = ["name", "item_name", "weight_per_unit", "weight_uom"]
	for field in (*measurements.CUSTOM_MEASUREMENT_FIELDS, *measurements.PRESENCE_FIELDS):
		if meta.get_field(field):
			fields.append(field)

	return {
		row.get("name"): row
		for row in frappe.get_list(
			ITEM,
			filters={"name": ("in", codes)},
			fields=fields,
			limit_page_length=0,
		)
	}


def _measurement(row, source, item=None):
	item = item or {}
	gross = row.get("sp_gross_weight")
	length = row.get("sp_length_cm")
	width = row.get("sp_width_cm")
	height = row.get("sp_height_cm")
	if source == measurements.CURRENT:
		gross = gross if gross not in (None, "") else item.get("sp_gross_weight")
		length = length if length not in (None, "") else item.get("sp_length_cm")
		width = width if width not in (None, "") else item.get("sp_width_cm")
		height = height if height not in (None, "") else item.get("sp_height_cm")

	return measurements.Measurement(
		weight_per_unit=row.get("weight_per_unit"),
		weight_uom=row.get("weight_uom") or item.get("weight_uom"),
		gross_weight=gross,
		length_cm=length,
		width_cm=width,
		height_cm=height,
		source=source,
	)


def _presence(row, item, snapshotted):
	if snapshotted:
		return measurements.parse_presence(row.get(measurements.PRESENCE_FIELD))
	if item is not None:
		return measurements.snapshot_presence(item)
	return ()


def _accepted_stock_qty(row):
	if row.get("stock_qty") not in (None, ""):
		return as_decimal(row.get("stock_qty"), "stock_qty")
	return as_decimal(row.get("qty"), "qty") * positive_factor(row.get("conversion_factor") or 1)


def _rejected_stock_qty(row):
	return as_decimal(row.get("rejected_qty"), "rejected_qty") * positive_factor(
		row.get("conversion_factor") or 1
	)


def purchase_receipt_lines(doc):
	"""One `MeasurementLine` per receipt row, with draft fallback to current Item values.

	A submitted row keeps its own snapshot. A draft row has no snapshot yet, so gross weight and
	dimensions come from the current Item and are labelled as current, not historical.
	"""
	items = _item_values(row.get("item_code") for row in doc.items) if doc.docstatus == 0 else {}
	lines = []
	for row in doc.items:
		snapshotted = bool(row.get("sp_measurement_snapshot"))
		item = items.get(row.get("item_code")) or None
		source = measurements.HISTORICAL
		if doc.docstatus == 0 and not snapshotted:
			source = measurements.CURRENT
		measurement = _measurement(row, source, item)
		presence = _presence(row, item, snapshotted)
		gross_present = "sp_gross_weight" in presence and measurement.gross_weight not in (None, "")
		volume_present = all(
			field in presence for field in ("sp_length_cm", "sp_width_cm", "sp_height_cm")
		) and all(
			value not in (None, "")
			for value in (measurement.length_cm, measurement.width_cm, measurement.height_cm)
		)
		lines.append(
			measurement_totals.MeasurementLine(
				document=doc.name,
				item_code=row.get("item_code") or "",
				stock_qty=_accepted_stock_qty(row),
				rejected_stock_qty=_rejected_stock_qty(row),
				measurement=measurement,
				net_present=measurement_totals.weight_is_present(measurement),
				gross_present=gross_present,
				volume_present=volume_present,
				idx=row.get("idx") or 0,
			)
		)
	return lines


def purchase_receipt_summary(doc, target_uom=None):
	lines = purchase_receipt_lines(doc)
	totals = measurement_totals.document_totals(
		doc.name,
		lines,
		target_uom=target_uom,
		convert=make_converter(),
	)
	return summary_payload(totals)


def _value(value):
	return None if value is None else str(value)


def coverage_text(totals):
	if not totals.rows:
		return "ردیفی وجود ندارد"
	return (
		f"وزن خالص {totals.net_covered}/{totals.rows} · "
		f"وزن ناخالص {totals.gross_covered}/{totals.rows} · "
		f"حجم {totals.volume_covered}/{totals.rows}"
	)


def warnings(totals, extra=()):
	result = list(extra or ())
	if not totals.rows:
		result.append("ردیفی برای محاسبه وزن و حجم وجود ندارد.")

	if totals.net_missing:
		result.append(f"وزن خالص {totals.net_missing} ردیف نامشخص است؛ جمع ناقص است.")
	if totals.gross_missing:
		result.append(f"وزن ناخالص {totals.gross_missing} ردیف نامشخص است؛ جمع ناقص است.")
	if totals.volume_missing:
		result.append(f"حجم {totals.volume_missing} ردیف نامشخص است؛ جمع ناقص است.")

	if totals.unresolved_uoms:
		names = ", ".join(uom or "بدون واحد" for uom in totals.unresolved_uoms)
		result.append(f"واحد وزن قابل تبدیل نیست: {names}؛ جمع وزن محاسبه نشد.")
	elif totals.net_total is None and totals.net_by_uom:
		names = ", ".join(uom or "بدون واحد" for uom, _ in totals.net_by_uom)
		result.append(f"واحدهای وزن ناهمگون‌اند: {names}؛ جمع وزن محاسبه نشد.")

	if totals.rejected_rows:
		result.append(
			f"{totals.rejected_rows} ردیف مقدار ردشده دارد؛ وزن و حجم ردشده جدا گزارش شده است."
		)

	return result


def summary_payload(totals, extra_warnings=()):
	issues = warnings(totals, extra_warnings)
	return {
		"document": totals.document,
		"rows": totals.rows,
		"net_total": _value(totals.net_total),
		"gross_total": _value(totals.gross_total),
		"weight_uom": totals.weight_uom or "",
		"volume_cm3": _value(totals.volume_cm3),
		"volume_m3": _value(totals.volume_m3),
		"net_by_uom": [{"uom": uom, "value": _value(value)} for uom, value in totals.net_by_uom],
		"gross_by_uom": [{"uom": uom, "value": _value(value)} for uom, value in totals.gross_by_uom],
		"rejected_net_total": _value(totals.rejected_net_total),
		"rejected_gross_total": _value(totals.rejected_gross_total),
		"rejected_volume_cm3": _value(totals.rejected_volume_cm3),
		"rejected_volume_m3": _value(totals.rejected_volume_m3),
		"rejected_rows": totals.rejected_rows,
		"coverage": coverage_text(totals),
		"warnings": issues,
		"warning": "؛ ".join(issues),
		"complete": totals.complete,
	}
