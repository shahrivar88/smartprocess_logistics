"""Unit measurements for Items and purchase documents, without a schema.

The future Item form will carry gross weight and three dimensions; net weight already exists on
the standard Item and on every standard purchase/sales item row. This module defines the
calculation and the boundary between current Item values and historical transaction snapshots,
but it does not create fields, write documents or import Frappe.

Dimensions are centimetres and calculated volume is cubic centimetres. Weight uses the Item's
standard `weight_uom`; the report layer must not mix weight units without converting them.
"""

from dataclasses import dataclass
from decimal import Decimal

from smartprocess_logistics.core.inventory import ZERO, as_decimal, max_zero, quantity

CURRENT = "current"
HISTORICAL = "historical"
SOURCES = (CURRENT, HISTORICAL)

# The minimum custom snapshot values a finalized transaction row needs if gross weight and
# dimensions must remain immutable. Native rows already snapshot net weight and weight UOM.
SNAPSHOT_FIELDS = (
	"weight_per_unit",
	"weight_uom",
	"sp_gross_weight",
	"sp_length_cm",
	"sp_width_cm",
	"sp_height_cm",
)

# Item-level presence flags. A positive value is marked automatically by the Item validator;
# the flags make an explicit zero distinguishable from a value that was never entered. The
# child snapshot stores the resulting field names in one compact string.
CUSTOM_MEASUREMENT_FIELDS = ("sp_gross_weight", "sp_length_cm", "sp_width_cm", "sp_height_cm")
PRESENCE_FIELDS = (
	"sp_gross_weight_entered",
	"sp_length_cm_entered",
	"sp_width_cm_entered",
	"sp_height_cm_entered",
)
PRESENCE_BY_MEASUREMENT = dict(zip(CUSTOM_MEASUREMENT_FIELDS, PRESENCE_FIELDS))
PRESENCE_FIELD = "sp_measurement_presence"

Quantity = Decimal | float | int | str | None


@dataclass(frozen=True)
class Measurement:
	"""One stock unit's weight and dimensions."""

	weight_per_unit: Quantity = ZERO
	weight_uom: str | None = None
	gross_weight: Quantity = ZERO
	length_cm: Quantity = ZERO
	width_cm: Quantity = ZERO
	height_cm: Quantity = ZERO
	source: str = CURRENT


@dataclass(frozen=True)
class MeasurementTotals:
	"""Totals for a quantity of one Item, in the measurement's own units."""

	net_weight: Decimal
	gross_weight: Decimal
	volume_cm3: Decimal
	volume_m3: Decimal
	weight_uom: str | None
	source: str


def _non_negative(value, field):
	result = as_decimal(value, field)
	if result < ZERO:
		raise ValueError(f"{field} must not be negative")
	return result


def validate_measurement(measurement):
	"""Check the values Stage D will store; raises rather than accepting a negative size."""
	_non_negative(measurement.weight_per_unit, "weight_per_unit")
	_non_negative(measurement.gross_weight, "gross_weight")
	_non_negative(measurement.length_cm, "length_cm")
	_non_negative(measurement.width_cm, "width_cm")
	_non_negative(measurement.height_cm, "height_cm")

	if measurement.source not in SOURCES:
		raise ValueError(f"unknown measurement source: {measurement.source}")

	return True


def volume_cm3(measurement):
	"""Volume of one stock unit, in cubic centimetres."""
	validate_measurement(measurement)
	length = as_decimal(measurement.length_cm, "length_cm")
	width = as_decimal(measurement.width_cm, "width_cm")
	height = as_decimal(measurement.height_cm, "height_cm")

	return quantity(length * width * height)


def totals(quantity_stock_uom, measurement):
	"""Net weight, gross weight and volume for a Stock UOM quantity."""
	validate_measurement(measurement)
	units = max_zero(as_decimal(quantity_stock_uom, "quantity"))

	volume_cm3_value = quantity(units * volume_cm3(measurement))

	return MeasurementTotals(
		net_weight=quantity(units * as_decimal(measurement.weight_per_unit, "weight_per_unit")),
		gross_weight=quantity(units * as_decimal(measurement.gross_weight, "gross_weight")),
		volume_cm3=volume_cm3_value,
		volume_m3=quantity(volume_cm3_value / Decimal("1000000")),
		weight_uom=measurement.weight_uom,
		source=measurement.source,
	)


def choose_measurement(current, historical=None):
	"""Use a historical snapshot when one exists, otherwise the current Item values.

	A historical row is never reconstructed from current Item values by this function: the
	caller supplies the snapshot or supplies `None`, and the chosen source is visible on the
	returned measurement.
	"""
	if historical is not None:
		validate_measurement(historical)
		return historical
	if current is not None:
		validate_measurement(current)
	return current


def snapshot_fields():
	"""The fields a finalized transaction row must keep for immutable gross/volume totals."""
	return SNAPSHOT_FIELDS


def _supplied(values, field):
	"""Keep a missing measurement missing; an explicit zero remains zero."""
	value = (values or {}).get(field)
	return None if value in (None, "") else value


def snapshot_presence(item_values):
	"""The measurement fields an Item says were entered.

	The explicit flag wins; an un-migrated item without flags still treats a positive value as
	entered. A zero without its flag is missing, which is the only honest answer the database
	can give for an old Float column.
	"""
	values = item_values or {}
	present = []
	for field in CUSTOM_MEASUREMENT_FIELDS:
		flag = values.get(PRESENCE_BY_MEASUREMENT[field])
		value = values.get(field)
		entered = flag not in (None, "", 0, 0.0, False, "0")
		value_present = value not in (None, "") and as_decimal(value) != ZERO
		if entered or value_present:
			present.append(field)
	return tuple(present)


def presence_text(present):
	"""A stable, compact representation for a purchase child row."""
	allowed = set(present or ())
	return ",".join(field for field in CUSTOM_MEASUREMENT_FIELDS if field in allowed)


def parse_presence(text):
	"""Read the child-row presence string, ignoring unknown field names."""
	allowed = {part.strip() for part in str(text or "").split(",") if part.strip()}
	return tuple(field for field in CUSTOM_MEASUREMENT_FIELDS if field in allowed)


def snapshot_item_matches(snapshot_item, item_code):
	"""Whether a copied snapshot belongs to the row's current Item.

	A missing identity cannot prove that a copied measurement belongs to another Item, so the
	caller must snapshot the current Item rather than silently keeping a foreign measurement.
	"""
	if not snapshot_item or not item_code:
		return False
	return str(snapshot_item).strip() == str(item_code).strip()


def snapshot_for_item(item_values, existing=None):
	"""The historical measurement a new submitted row should keep.

	An existing snapshot is returned unchanged. A new row takes the current Item values and
	marks its source historical. Quantity and UOM are deliberately not inputs: the snapshot
	describes one stock unit, and changing the row quantity does not change that measurement.
	Missing values stay `None` in this pure result. Frappe's Float columns are `NOT NULL` and
	coerce `None` to zero when persisted, so a site that must distinguish an explicit zero
	needs a separate presence flag; this layer only preserves the caller's input.
	"""
	if existing is not None:
		validate_measurement(existing)
		return existing

	measurement = Measurement(
		weight_per_unit=_supplied(item_values, "weight_per_unit"),
		weight_uom=(item_values or {}).get("weight_uom") or None,
		gross_weight=_supplied(item_values, "sp_gross_weight"),
		length_cm=_supplied(item_values, "sp_length_cm"),
		width_cm=_supplied(item_values, "sp_width_cm"),
		height_cm=_supplied(item_values, "sp_height_cm"),
		source=HISTORICAL,
	)
	validate_measurement(measurement)
	return measurement
