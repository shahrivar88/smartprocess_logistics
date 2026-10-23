"""Pure tests for weight, dimensions and historical-measurement selection."""

from decimal import Decimal

from smartprocess_logistics.core import measurements
from smartprocess_logistics.core.measurements import Measurement
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


class TestMeasurementMath(UnitTestCase):
	def test_volume_is_length_times_width_times_height(self):
		measurement = Measurement(length_cm=10, width_cm=20, height_cm=30)

		self.assertEqual(measurements.volume_cm3(measurement), D("6000"))

	def test_totals_use_stock_uom_quantity_for_net_gross_and_volume(self):
		measurement = Measurement(
			weight_per_unit=D("2.5"),
			weight_uom="Kg",
			gross_weight=D("3"),
			length_cm=10,
			width_cm=20,
			height_cm=30,
		)

		total = measurements.totals(4, measurement)

		self.assertEqual(total.net_weight, D("10"))
		self.assertEqual(total.gross_weight, D("12"))
		self.assertEqual(total.volume_cm3, D("24000"))
		self.assertEqual(total.volume_m3, D("0.024"))
		self.assertEqual(total.weight_uom, "Kg")
		self.assertEqual(total.source, measurements.CURRENT)

	def test_missing_values_read_as_zero(self):
		measurement = Measurement(length_cm=None, width_cm="", height_cm=None)

		self.assertEqual(measurements.volume_cm3(measurement), D("0"))

	def test_negative_measurements_are_refused(self):
		for field in (
			"weight_per_unit",
			"gross_weight",
			"length_cm",
			"width_cm",
			"height_cm",
		):
			with self.subTest(field=field):
				with self.assertRaises(ValueError):
					measurements.volume_cm3(Measurement(**{field: -1}))

	def test_malformed_measurements_are_refused(self):
		with self.assertRaises(ValueError):
			measurements.volume_cm3(Measurement(length_cm="abc"))

	def test_an_unknown_source_is_refused(self):
		with self.assertRaises(ValueError):
			measurements.totals(1, Measurement(source="unknown"))


class TestHistoricalMeasurements(UnitTestCase):
	def test_a_historical_snapshot_wins_over_current_item_values(self):
		current = Measurement(length_cm=10, width_cm=10, height_cm=10)
		historical = Measurement(
			length_cm=1,
			width_cm=2,
			height_cm=3,
			source=measurements.HISTORICAL,
		)

		chosen = measurements.choose_measurement(current, historical)

		self.assertIs(chosen, historical)
		self.assertEqual(chosen.source, measurements.HISTORICAL)
		self.assertEqual(measurements.volume_cm3(chosen), D("6"))

	def test_current_item_values_are_used_only_when_no_snapshot_exists(self):
		current = Measurement(length_cm=1, width_cm=2, height_cm=3)

		self.assertIs(measurements.choose_measurement(current, None), current)

	def test_a_missing_measurement_stays_missing(self):
		self.assertIsNone(measurements.choose_measurement(None, None))

	def test_the_minimum_snapshot_fields_are_explicit(self):
		self.assertEqual(
			measurements.snapshot_fields(),
			(
				"weight_per_unit",
				"weight_uom",
				"sp_gross_weight",
				"sp_length_cm",
				"sp_width_cm",
				"sp_height_cm",
			),
		)

	def test_a_new_snapshot_uses_the_item_values(self):
		snapshot = measurements.snapshot_for_item(
			{
				"weight_per_unit": 2,
				"weight_uom": "Kg",
				"sp_gross_weight": 3,
				"sp_length_cm": 10,
				"sp_width_cm": 20,
				"sp_height_cm": 30,
			}
		)

		self.assertEqual(snapshot.source, measurements.HISTORICAL)
		self.assertEqual(snapshot.gross_weight, D("3"))
		self.assertEqual(measurements.volume_cm3(snapshot), D("6000"))

	def test_an_existing_snapshot_survives_item_changes(self):
		existing = measurements.Measurement(
			weight_per_unit=2,
			gross_weight=3,
			length_cm=10,
			width_cm=20,
			height_cm=30,
			source=measurements.HISTORICAL,
		)
		changed_item = {
			"weight_per_unit": 99,
			"sp_gross_weight": 100,
			"sp_length_cm": 1,
			"sp_width_cm": 1,
			"sp_height_cm": 1,
		}

		self.assertIs(measurements.snapshot_for_item(changed_item, existing), existing)

	def test_quantity_and_uom_do_not_change_the_unit_snapshot(self):
		values = {"sp_gross_weight": 3, "sp_length_cm": 10, "sp_width_cm": 20, "sp_height_cm": 30}
		first = measurements.snapshot_for_item(values)
		second = measurements.snapshot_for_item(values)

		self.assertEqual(first, second)
		self.assertEqual(measurements.totals(2, first).gross_weight, D("6"))

	def test_a_missing_snapshot_measurement_stays_missing(self):
		snapshot = measurements.snapshot_for_item({"weight_per_unit": 2, "weight_uom": "Kg"})

		self.assertIsNone(snapshot.gross_weight)
		self.assertIsNone(snapshot.length_cm)
		self.assertIsNone(snapshot.width_cm)
		self.assertIsNone(snapshot.height_cm)

	def test_an_explicit_zero_snapshot_measurement_stays_zero(self):
		snapshot = measurements.snapshot_for_item(
			{
				"sp_gross_weight": 0,
				"sp_length_cm": 0,
				"sp_width_cm": 0,
				"sp_height_cm": 0,
			}
		)

		self.assertEqual(snapshot.gross_weight, 0)
		self.assertEqual(snapshot.length_cm, 0)
		self.assertEqual(snapshot.width_cm, 0)
		self.assertEqual(snapshot.height_cm, 0)

	def test_a_snapshot_is_only_kept_for_the_item_it_measured(self):
		self.assertTrue(measurements.snapshot_item_matches("ITEM-A", "ITEM-A"))
		self.assertFalse(measurements.snapshot_item_matches("ITEM-A", "ITEM-B"))
		self.assertFalse(measurements.snapshot_item_matches(None, "ITEM-A"))


class TestMeasurementPresence(UnitTestCase):
	def test_an_explicit_zero_is_present_only_with_its_flag(self):
		self.assertEqual(measurements.snapshot_presence({}), ())
		self.assertEqual(
			measurements.snapshot_presence({"sp_gross_weight_entered": 1, "sp_gross_weight": 0}),
			("sp_gross_weight",),
		)

	def test_a_positive_value_is_present_even_before_flags_are_migrated(self):
		self.assertEqual(
			measurements.snapshot_presence({"sp_length_cm": 12}),
			("sp_length_cm",),
		)

	def test_partial_presence_is_kept_field_by_field(self):
		present = measurements.snapshot_presence(
			{
				"sp_gross_weight_entered": 1,
				"sp_gross_weight": 0,
				"sp_length_cm_entered": 1,
				"sp_length_cm": 10,
			}
		)

		self.assertEqual(present, ("sp_gross_weight", "sp_length_cm"))

	def test_presence_text_round_trips_and_ignores_unknown_names(self):
		text = measurements.presence_text(("sp_width_cm", "sp_gross_weight"))

		self.assertEqual(text, "sp_gross_weight,sp_width_cm")
		self.assertEqual(
			measurements.parse_presence("sp_width_cm,unknown,sp_gross_weight"),
			("sp_gross_weight", "sp_width_cm"),
		)
