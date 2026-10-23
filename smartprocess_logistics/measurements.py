"""Snapshot gross weight and dimensions on newly submitted purchase documents.

Native ERPNext already snapshots net weight on every purchase item row. This handler adds the
missing gross-weight and dimension values at `before_submit`, so a later Item edit cannot
rewrite what a submitted purchase order, invoice or receipt measured. It only writes fields on
the document being submitted, never a ledger, Bin or a submitted historical document.

Rows copied into an amendment carry `sp_measurement_snapshot = 1` and the Item code they were
measured for. The handler leaves those values unchanged only while the row still names that
same Item; changing the Item re-snapshots the row from the new Item. New rows in an amendment
are snapshotted once. Old submitted documents are never backfilled.
"""

import frappe

from smartprocess_logistics.core import measurements
from smartprocess_logistics.core.inventory import as_decimal

SNAPSHOT_FIELDS = ("sp_gross_weight", "sp_length_cm", "sp_width_cm", "sp_height_cm")
MEASUREMENT_FIELD = {
	"sp_gross_weight": "gross_weight",
	"sp_length_cm": "length_cm",
	"sp_width_cm": "width_cm",
	"sp_height_cm": "height_cm",
}
MARKER = "sp_measurement_snapshot"
MEASUREMENT_ITEM = "sp_measurement_item"
PRESENCE = "sp_measurement_presence"
ITEM_SOURCE_FIELDS = (
	"weight_per_unit",
	"weight_uom",
	*SNAPSHOT_FIELDS,
	*measurements.PRESENCE_FIELDS,
)

CHILD_DOCTYPES = {
	"Purchase Order": "Purchase Order Item",
	"Purchase Invoice": "Purchase Invoice Item",
	"Purchase Receipt": "Purchase Receipt Item",
}


def _item_values(item_codes):
	codes = tuple(sorted({code for code in item_codes if code}))
	if not codes:
		return {}

	item_meta = frappe.get_meta("Item")
	fields = ["name"]
	fields.extend(field for field in ITEM_SOURCE_FIELDS if item_meta.get_field(field))

	return {
		row.get("name"): row
		for row in frappe.get_list(
			"Item",
			filters={"name": ("in", codes)},
			fields=fields,
			limit_page_length=0,
		)
	}


def validate_item_measurements(doc, method=None):
	"""Keep a positive measurement marked as entered; the visible checks cover explicit zero."""
	meta = frappe.get_meta("Item")
	required = (*measurements.CUSTOM_MEASUREMENT_FIELDS, *measurements.PRESENCE_FIELDS)
	if not all(meta.get_field(field) for field in required):
		return

	for field in measurements.CUSTOM_MEASUREMENT_FIELDS:
		value = getattr(doc, field, None)
		if value not in (None, "") and as_decimal(value) != 0:
			setattr(doc, measurements.PRESENCE_BY_MEASUREMENT[field], 1)


def snapshot_purchase_document(doc, method=None):
	"""Fill the missing measurement snapshot on the rows being submitted."""
	child_doctype = CHILD_DOCTYPES.get(doc.doctype)
	if not child_doctype or getattr(doc, "_action", None) != "submit":
		return

	child_meta = frappe.get_meta(child_doctype)
	if not all(
		child_meta.get_field(field)
		for field in (*SNAPSHOT_FIELDS, MARKER, MEASUREMENT_ITEM, PRESENCE)
	):
		return
	if not all(frappe.get_meta("Item").get_field(field) for field in SNAPSHOT_FIELDS):
		return

	items = _item_values(row.get("item_code") for row in doc.items)
	is_amendment = bool(getattr(doc, "amended_from", None))

	for row in doc.items:
		item_code = row.get("item_code")
		if not item_code:
			continue
		if (
			is_amendment
			and row.get(MARKER)
			and measurements.snapshot_item_matches(row.get(MEASUREMENT_ITEM), item_code)
		):
			continue
		if item_code not in items:
			continue

		item_values = items.get(item_code) or {}
		values = measurements.snapshot_for_item(item_values)
		for field in SNAPSHOT_FIELDS:
			setattr(row, field, getattr(values, MEASUREMENT_FIELD[field]))
		row.set(MARKER, 1)
		row.set(MEASUREMENT_ITEM, item_code)
		row.set(PRESENCE, measurements.presence_text(measurements.snapshot_presence(item_values)))
