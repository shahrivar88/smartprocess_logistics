"""Document-level weight and volume totals, without Frappe.

Rows carry a measurement snapshot and the quantities actually received. This module groups
those rows by document, converts weight units only when the caller supplies a conversion, and
keeps missing measurements out of the sums. A missing value is `None`; an entered zero stays
zero. Volume is unit-independent (cubic centimetres) and is summed separately.

The per-unit arithmetic is in `core.measurements`; this module does not repeat it.
"""

from dataclasses import dataclass
from decimal import Decimal

from smartprocess_logistics.core.inventory import ZERO, as_decimal, quantity
from smartprocess_logistics.core.measurements import Measurement, totals

Quantity = Decimal | float | int | str | None


@dataclass(frozen=True)
class MeasurementLine:
	"""One document row, accepted and rejected quantities in Stock UOM."""

	document: str
	item_code: str = ""
	stock_qty: Quantity = ZERO
	rejected_stock_qty: Quantity = ZERO
	measurement: Measurement = Measurement()
	net_present: bool = False
	gross_present: bool = False
	volume_present: bool = False
	idx: int = 0


@dataclass(frozen=True)
class DocumentTotals:
	"""What one document's rows add up to, with its coverage and unresolved units."""

	document: str
	rows: int
	net_total: Decimal | None
	gross_total: Decimal | None
	weight_uom: str | None
	volume_cm3: Decimal | None
	volume_m3: Decimal | None
	net_by_uom: tuple
	gross_by_uom: tuple
	rejected_net_total: Decimal | None
	rejected_gross_total: Decimal | None
	rejected_net_by_uom: tuple
	rejected_gross_by_uom: tuple
	rejected_volume_cm3: Decimal | None
	rejected_volume_m3: Decimal | None
	net_covered: int
	gross_covered: int
	volume_covered: int
	net_missing: int
	gross_missing: int
	volume_missing: int
	rejected_rows: int
	unresolved_uoms: tuple = ()
	complete: bool = False


def _text(value):
	return str(value or "").strip()


def weight_is_present(measurement):
	"""A zero with a Weight UOM is an entered zero; zero without one is missing.

	ERPNext's native weight is a non-null Float, so the value alone cannot tell an omitted
	measurement from a real zero. The UOM is the only honest signal the standard fields carry.
	"""
	if as_decimal(measurement.weight_per_unit, "weight_per_unit") != ZERO:
		return True
	return bool(_text(measurement.weight_uom))


def _add(bucket, uom, value):
	bucket[uom] = bucket.get(uom, ZERO) + value


def _weight_uom(measurement):
	return _text(measurement.weight_uom)


def _choose_target(maps, requested, convert):
	if requested:
		return _text(requested) or None

	counts = {}
	for bucket in maps:
		for uom in bucket:
			if uom:
				counts[uom] = counts.get(uom, 0) + 1

	if not counts:
		return None
	if len(counts) == 1:
		return next(iter(counts))

	candidate = sorted(counts, key=lambda uom: (-counts[uom], uom))[0]
	for uom in counts:
		factor = convert(uom, candidate)
		if factor is None or as_decimal(factor, "conversion_factor") <= ZERO:
			return None
	return candidate


def _normalise(bucket, target, convert):
	"""A total in `target`, or `None` when a unit cannot be converted."""
	if not bucket:
		return None, ()
	if not target:
		return None, tuple(sorted(bucket))

	total = ZERO
	unresolved = []
	for uom, value in sorted(bucket.items()):
		factor = Decimal("1") if uom == target else convert(uom, target)
		if factor is None or as_decimal(factor, "conversion_factor") <= ZERO:
			unresolved.append(uom)
			continue
		total += value * as_decimal(factor, "conversion_factor")

	if unresolved:
		return None, tuple(unresolved)
	return quantity(total), ()


def _sum_volume(values):
	if not values:
		return None
	return quantity(sum(values, ZERO))


def document_totals(document, lines, *, target_uom=None, convert=None):
	"""Aggregate one document's accepted and rejected rows.

	`convert(from_uom, to_uom)` returns a Decimal factor or `None`. When no target is requested,
	a single shared unit is used; mixed units are converted only when every unit can reach one
	target, otherwise the weight total stays `None` and `unresolved_uoms` names why.
	"""
	convert = convert or (lambda from_uom, to_uom: Decimal("1") if from_uom == to_uom else None)
	lines = tuple(lines)
	net_by_uom = {}
	gross_by_uom = {}
	rejected_net_by_uom = {}
	rejected_gross_by_uom = {}
	net_volume = []
	rejected_volume = []
	net_covered = gross_covered = volume_covered = 0
	net_missing = gross_missing = volume_missing = 0
	rejected_rows = 0

	for line in lines:
		accepted = totals(line.stock_qty, line.measurement)
		rejected = totals(line.rejected_stock_qty or ZERO, line.measurement)
		uom = _weight_uom(line.measurement)

		if line.net_present:
			net_covered += 1
			_add(net_by_uom, uom, accepted.net_weight)
		else:
			net_missing += 1

		if line.gross_present:
			gross_covered += 1
			_add(gross_by_uom, uom, accepted.gross_weight)
		else:
			gross_missing += 1

		if line.volume_present:
			volume_covered += 1
			net_volume.append(accepted.volume_cm3)
		else:
			volume_missing += 1

		if as_decimal(line.rejected_stock_qty or ZERO, "rejected_stock_qty") > ZERO:
			rejected_rows += 1
			if line.net_present:
				_add(rejected_net_by_uom, uom, rejected.net_weight)
			if line.gross_present:
				_add(rejected_gross_by_uom, uom, rejected.gross_weight)
			if line.volume_present:
				rejected_volume.append(rejected.volume_cm3)

	all_weight_maps = (net_by_uom, gross_by_uom, rejected_net_by_uom, rejected_gross_by_uom)
	target = _choose_target(all_weight_maps, target_uom, convert)
	net_total, net_unresolved = _normalise(net_by_uom, target, convert)
	gross_total, gross_unresolved = _normalise(gross_by_uom, target, convert)
	rejected_net_total, rejected_net_unresolved = _normalise(rejected_net_by_uom, target, convert)
	rejected_gross_total, rejected_gross_unresolved = _normalise(
		rejected_gross_by_uom, target, convert
	)

	volume_cm3 = _sum_volume(net_volume)
	rejected_volume_cm3 = _sum_volume(rejected_volume)
	unresolved = tuple(
		sorted(
			set(net_unresolved)
			| set(gross_unresolved)
			| set(rejected_net_unresolved)
			| set(rejected_gross_unresolved)
		)
	)

	return DocumentTotals(
		document=document,
		rows=len(lines),
		net_total=net_total,
		gross_total=gross_total,
		weight_uom=target,
		volume_cm3=volume_cm3,
		volume_m3=quantity(volume_cm3 / Decimal("1000000")) if volume_cm3 is not None else None,
		net_by_uom=tuple(sorted(net_by_uom.items())),
		gross_by_uom=tuple(sorted(gross_by_uom.items())),
		rejected_net_total=rejected_net_total,
		rejected_gross_total=rejected_gross_total,
		rejected_net_by_uom=tuple(sorted(rejected_net_by_uom.items())),
		rejected_gross_by_uom=tuple(sorted(rejected_gross_by_uom.items())),
		rejected_volume_cm3=rejected_volume_cm3,
		rejected_volume_m3=quantity(rejected_volume_cm3 / Decimal("1000000"))
		if rejected_volume_cm3 is not None
		else None,
		net_covered=net_covered,
		gross_covered=gross_covered,
		volume_covered=volume_covered,
		net_missing=net_missing,
		gross_missing=gross_missing,
		volume_missing=volume_missing,
		rejected_rows=rejected_rows,
		unresolved_uoms=unresolved,
		complete=bool(lines)
		and net_missing == 0
		and gross_missing == 0
		and volume_missing == 0
		and not unresolved,
	)
