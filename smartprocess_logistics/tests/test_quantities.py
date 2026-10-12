"""Requirement, shortage and excess arithmetic, including returns and fractions."""

from smartprocess_logistics.tests.base import UnitTestCase

from smartprocess_logistics.core.quantities import (
	CODE_NEGATIVE_REGISTERED,
	CODE_NOT_A_NUMBER,
	CODE_NOT_FINITE,
	CODE_TOO_LARGE,
	MAX_UNITS,
	QUANTITY_EPSILON,
	QuantityError,
	coverage_summary,
	quantity_is_integral,
	required_units,
	row_coverage,
)


class TestRequiredUnits(UnitTestCase):
	def test_whole_quantities(self):
		self.assertEqual(required_units(1), 1)
		self.assertEqual(required_units(10), 10)
		self.assertEqual(required_units(0), 0)

	def test_a_return_needs_a_label_per_returned_unit(self):
		self.assertEqual(required_units(-10), 10)

	def test_fractions_round_up(self):
		self.assertEqual(required_units(2.5), 3)
		self.assertEqual(required_units(0.1), 1)
		self.assertEqual(required_units(1.999), 2)

	def test_a_float_noise_quantity_is_read_as_whole(self):
		self.assertEqual(required_units(2 + QUANTITY_EPSILON / 2), 2)

	def test_just_above_a_whole_number_rounds_up(self):
		self.assertEqual(required_units(2 + QUANTITY_EPSILON * 10), 3)

	def test_a_negative_fraction_rounds_up_too(self):
		self.assertEqual(required_units(-2.5), 3)

	def test_non_numbers_are_refused(self):
		for value in (None, "10", [10], True):
			with self.subTest(value=value):
				with self.assertRaises(QuantityError) as caught:
					required_units(value)

				self.assertEqual(caught.exception.code, CODE_NOT_A_NUMBER)

	def test_infinities_and_nan_are_refused(self):
		for value in (float("inf"), float("-inf"), float("nan")):
			with self.subTest(value=value):
				with self.assertRaises(QuantityError) as caught:
					required_units(value)

				self.assertEqual(caught.exception.code, CODE_NOT_FINITE)

	def test_an_absurd_quantity_is_refused(self):
		with self.assertRaises(QuantityError) as caught:
			required_units(MAX_UNITS + 1)

		self.assertEqual(caught.exception.code, CODE_TOO_LARGE)


class TestQuantityIsIntegral(UnitTestCase):
	def test_whole_quantities(self):
		self.assertTrue(quantity_is_integral(5))
		self.assertTrue(quantity_is_integral(-5))
		self.assertTrue(quantity_is_integral(0))

	def test_fractions(self):
		self.assertFalse(quantity_is_integral(2.5))
		self.assertFalse(quantity_is_integral(0.1))

	def test_float_noise_is_tolerated(self):
		self.assertTrue(quantity_is_integral(3 + QUANTITY_EPSILON / 2))

	def test_non_numbers_are_refused(self):
		with self.assertRaises(QuantityError):
			quantity_is_integral("3")


class TestRowCoverage(UnitTestCase):
	def test_a_complete_row(self):
		coverage = row_coverage("row-1", "ITEM-1", 10, 10)

		self.assertEqual(coverage.required, 10)
		self.assertEqual(coverage.registered, 10)
		self.assertEqual(coverage.shortage, 0)
		self.assertEqual(coverage.excess, 0)
		self.assertTrue(coverage.is_complete)
		self.assertFalse(coverage.is_over)

	def test_a_short_row(self):
		coverage = row_coverage("row-1", "ITEM-1", 10, 6)

		self.assertEqual(coverage.shortage, 4)
		self.assertEqual(coverage.excess, 0)
		self.assertFalse(coverage.is_complete)

	def test_an_empty_row(self):
		coverage = row_coverage("row-1", "ITEM-1", 10, 0)

		self.assertEqual(coverage.shortage, 10)
		self.assertEqual(coverage.registered, 0)

	def test_a_partial_registration_then_the_rest(self):
		first = row_coverage("row-1", "ITEM-1", 10, 6)
		later = row_coverage("row-1", "ITEM-1", 10, 10)

		self.assertEqual(first.shortage, 4)
		self.assertTrue(later.is_complete)

	def test_an_over_registered_row(self):
		coverage = row_coverage("row-1", "ITEM-1", 10, 12)

		self.assertEqual(coverage.shortage, 0)
		self.assertEqual(coverage.excess, 2)
		self.assertTrue(coverage.is_over)

	def test_a_return_row(self):
		coverage = row_coverage("row-1", "ITEM-1", -10, 10)

		self.assertEqual(coverage.required, 10)
		self.assertTrue(coverage.is_complete)

	def test_a_zero_quantity_row_accepts_nothing(self):
		coverage = row_coverage("row-1", "ITEM-1", 0, 1)

		self.assertEqual(coverage.required, 0)
		self.assertEqual(coverage.excess, 1)

	def test_a_fractional_row_needs_the_rounded_up_count(self):
		coverage = row_coverage("row-1", "ITEM-1", 2.5, 2)

		self.assertEqual(coverage.required, 3)
		self.assertEqual(coverage.shortage, 1)

	def test_a_negative_registered_count_is_refused(self):
		with self.assertRaises(QuantityError) as caught:
			row_coverage("row-1", "ITEM-1", 10, -1)

		self.assertEqual(caught.exception.code, CODE_NEGATIVE_REGISTERED)

	def test_a_non_integer_registered_count_is_refused(self):
		for value in (2.0, "2", None, True):
			with self.subTest(value=value):
				with self.assertRaises(QuantityError) as caught:
					row_coverage("row-1", "ITEM-1", 10, value)

				self.assertEqual(caught.exception.code, CODE_NOT_A_NUMBER)

	def test_coverage_is_frozen(self):
		coverage = row_coverage("row-1", "ITEM-1", 10, 6)

		with self.assertRaises(Exception):
			coverage.registered = 10


class TestCoverageSummary(UnitTestCase):
	def setUp(self):
		self.coverages = (
			row_coverage("row-1", "ITEM-1", 10, 10),
			row_coverage("row-2", "ITEM-2", 5, 3),
			row_coverage("row-3", "ITEM-3", 2, 4),
		)

	def test_totals(self):
		summary = coverage_summary(self.coverages)

		self.assertEqual(summary.rows, 3)
		self.assertEqual(summary.required, 17)
		self.assertEqual(summary.registered, 17)
		self.assertEqual(summary.shortage, 2)
		self.assertEqual(summary.excess, 2)
		self.assertEqual(summary.incomplete_rows, 1)
		self.assertEqual(summary.over_rows, 1)
		self.assertFalse(summary.is_complete)

	def test_all_complete(self):
		summary = coverage_summary(
			(
				row_coverage("row-1", "ITEM-1", 3, 3),
				row_coverage("row-2", "ITEM-2", 1, 1),
			)
		)

		self.assertTrue(summary.is_complete)
		self.assertEqual(summary.incomplete_rows, 0)

	def test_no_rows(self):
		summary = coverage_summary(())

		self.assertEqual(summary.rows, 0)
		self.assertTrue(summary.is_complete)
