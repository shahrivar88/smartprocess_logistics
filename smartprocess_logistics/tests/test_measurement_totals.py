"""Pure tests for document-level weight and volume aggregation."""

from decimal import Decimal

from smartprocess_logistics.core import measurement_totals
from smartprocess_logistics.core.measurements import Measurement
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


def measurement(
	weight=0,
	uom="Kg",
	gross=0,
	length=0,
	width=0,
	height=0,
	source="current",
):
	return Measurement(
		weight_per_unit=weight,
		weight_uom=uom,
		gross_weight=gross,
		length_cm=length,
		width_cm=width,
		height_cm=height,
		source=source,
	)


def line(
	document="PR-1",
	stock_qty=1,
	rejected=0,
	measurement_value=None,
	net=True,
	gross=True,
	volume=True,
	idx=0,
):
	return measurement_totals.MeasurementLine(
		document=document,
		item_code=f"ITEM-{idx}",
		stock_qty=stock_qty,
		rejected_stock_qty=rejected,
		measurement=measurement_value or measurement(weight=2),
		net_present=net,
		gross_present=gross,
		volume_present=volume,
		idx=idx,
	)


class TestPresence(UnitTestCase):
	def test_a_zero_with_a_weight_uom_is_an_entered_zero(self):
		self.assertTrue(measurement_totals.weight_is_present(measurement(weight=0, uom="Kg")))

	def test_a_zero_without_a_weight_uom_is_missing(self):
		self.assertFalse(measurement_totals.weight_is_present(measurement(weight=0, uom=None)))

	def test_a_positive_weight_is_present_even_without_a_uom(self):
		self.assertTrue(measurement_totals.weight_is_present(measurement(weight=2, uom=None)))


class TestDocumentTotals(UnitTestCase):
	def test_accepted_weight_uses_stock_quantity(self):
		lines = [
			line(stock_qty=4, measurement_value=measurement(weight=2), idx=1),
			line(stock_qty=3, measurement_value=measurement(weight=5), idx=2),
		]

		totals = measurement_totals.document_totals("PR-1", lines)

		self.assertEqual(totals.net_total, D("23"))
		self.assertEqual(totals.weight_uom, "Kg")
		self.assertEqual(totals.net_covered, 2)
		self.assertEqual(totals.net_missing, 0)

	def test_a_missing_weight_does_not_become_zero_in_the_total(self):
		lines = [
			line(stock_qty=4, measurement_value=measurement(weight=2), idx=1),
			line(stock_qty=9, measurement_value=measurement(weight=0, uom=None), net=False, idx=2),
		]

		totals = measurement_totals.document_totals("PR-1", lines)

		self.assertEqual(totals.net_total, D("8"))
		self.assertEqual(totals.net_covered, 1)
		self.assertEqual(totals.net_missing, 1)
		self.assertFalse(totals.complete)

	def test_an_entered_zero_counts_as_covered_and_sums_to_zero(self):
		lines = [line(stock_qty=4, measurement_value=measurement(weight=0, uom="Kg"))]

		totals = measurement_totals.document_totals("PR-1", lines)

		self.assertEqual(totals.net_total, D("0"))
		self.assertEqual(totals.net_covered, 1)
		self.assertEqual(totals.net_missing, 0)

	def test_mixed_units_are_not_summed_without_a_conversion(self):
		lines = [
			line(stock_qty=1, measurement_value=measurement(weight=2, uom="Kg"), idx=1),
			line(stock_qty=1, measurement_value=measurement(weight=500, uom="g"), idx=2),
		]

		totals = measurement_totals.document_totals("PR-1", lines)

		self.assertIsNone(totals.net_total)
		self.assertEqual(totals.unresolved_uoms, ("Kg", "g"))
		self.assertEqual(totals.net_by_uom, (("Kg", D("2")), ("g", D("500"))))

	def test_mixed_units_are_normalised_when_a_conversion_exists(self):
		lines = [
			line(stock_qty=1, measurement_value=measurement(weight=2, uom="Kg"), idx=1),
			line(stock_qty=1, measurement_value=measurement(weight=500, uom="g"), idx=2),
		]

		def convert(from_uom, to_uom):
			if from_uom == to_uom:
				return D("1")
			if (from_uom, to_uom) == ("g", "Kg"):
				return D("0.001")
			return None

		totals = measurement_totals.document_totals("PR-1", lines, convert=convert)

		self.assertEqual(totals.net_total, D("2.5"))
		self.assertEqual(totals.weight_uom, "Kg")
		self.assertEqual(totals.unresolved_uoms, ())

	def test_an_explicit_target_wins_and_an_unconvertible_unit_stays_out(self):
		lines = [
			line(stock_qty=1, measurement_value=measurement(weight=2, uom="Kg"), idx=1),
			line(stock_qty=1, measurement_value=measurement(weight=500, uom="g"), idx=2),
		]

		def convert(from_uom, to_uom):
			if from_uom == to_uom:
				return D("1")
			return None

		totals = measurement_totals.document_totals("PR-1", lines, target_uom="Kg", convert=convert)

		self.assertIsNone(totals.net_total)
		self.assertEqual(totals.weight_uom, "Kg")
		self.assertEqual(totals.unresolved_uoms, ("g",))

	def test_gross_weight_and_volume_use_their_own_flags(self):
		value = measurement(weight=2, gross=3, length=10, width=5, height=2)

		totals = measurement_totals.document_totals(
			"PR-1",
			[line(stock_qty=4, measurement_value=value, gross=True, volume=True)],
		)

		self.assertEqual(totals.net_total, D("8"))
		self.assertEqual(totals.gross_total, D("12"))
		self.assertEqual(totals.volume_cm3, D("400"))
		self.assertEqual(totals.volume_m3, D("0.0004"))

	def test_missing_gross_and_volume_keep_totals_empty(self):
		value = measurement(weight=2, gross=0, length=0, width=0, height=0)

		totals = measurement_totals.document_totals(
			"PR-1",
			[line(stock_qty=4, measurement_value=value, gross=False, volume=False)],
		)

		self.assertEqual(totals.net_total, D("8"))
		self.assertIsNone(totals.gross_total)
		self.assertIsNone(totals.volume_cm3)
		self.assertEqual(totals.gross_missing, 1)
		self.assertEqual(totals.volume_missing, 1)

	def test_rejected_quantity_is_reported_separately(self):
		lines = [line(stock_qty=10, rejected=2, measurement_value=measurement(weight=5))]

		totals = measurement_totals.document_totals("PR-1", lines)

		self.assertEqual(totals.net_total, D("50"))
		self.assertEqual(totals.rejected_net_total, D("10"))
		self.assertEqual(totals.rejected_rows, 1)

	def test_two_partial_receipts_keep_their_own_totals(self):
		first = measurement_totals.document_totals(
			"PR-1", [line(document="PR-1", stock_qty=4, measurement_value=measurement(weight=2))]
		)
		second = measurement_totals.document_totals(
			"PR-2", [line(document="PR-2", stock_qty=6, measurement_value=measurement(weight=2))]
		)

		self.assertEqual(first.net_total, D("8"))
		self.assertEqual(second.net_total, D("12"))

	def test_a_document_with_no_lines_is_not_complete(self):
		totals = measurement_totals.document_totals("PR-1", [])

		self.assertEqual(totals.rows, 0)
		self.assertIsNone(totals.net_total)
		self.assertFalse(totals.complete)
