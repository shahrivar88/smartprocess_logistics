"""What a report opens on, and the two settings that decide how it is drawn.

The decisions are pure (`core.report_defaults`), so the company choice and the fiscal-year
window are checked without a site. The two Desk artefacts the release adds — the receipt's
warehouse-type switch and the Item flag's label — are read from the JSON this app ships.
"""

import datetime
import json
import pathlib

from smartprocess_logistics.core import report_defaults
from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = APP / "smartprocess_logistics" / "doctype" / "logistics_settings" / "logistics_settings.json"
ITEM = APP / "smartprocess_logistics" / "custom" / "item.json"


def read_json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def field_of(document, fieldname):
	return next((field for field in document["fields"] if field["fieldname"] == fieldname), None)


class ChooseCompanyTest(UnitTestCase):
	def test_the_readers_own_default_wins_when_they_may_reach_it(self):
		self.assertEqual(report_defaults.choose_company("A", ["B", "A"]), "A")

	def test_a_default_they_may_not_read_falls_back_to_a_company_they_may(self):
		self.assertEqual(report_defaults.choose_company("Z", ["B", "C"]), "B")

	def test_the_company_scope_narrows_the_permitted_list(self):
		self.assertEqual(
			report_defaults.choose_company("A", ["A", "B"], frozenset({"B"})),
			"B",
		)

	def test_no_permitted_company_is_named(self):
		self.assertIsNone(report_defaults.choose_company("A", []))
		self.assertIsNone(report_defaults.choose_company("A", [], frozenset({"A"})))
		self.assertIsNone(report_defaults.choose_company("", []))


class FiscalYearWindowTest(UnitTestCase):
	def test_a_jalali_year_is_read_from_the_dates_it_is_stored_with(self):
		years = [("1404", datetime.date(2025, 3, 21), datetime.date(2026, 3, 20))]

		self.assertEqual(
			report_defaults.fiscal_year_window(datetime.date(2025, 11, 23), years),
			("2025-03-21", "2026-03-20"),
		)

	def test_the_year_that_contains_today_is_the_one_returned(self):
		years = [
			("1403", datetime.date(2024, 3, 21), datetime.date(2025, 3, 20)),
			("1404", datetime.date(2025, 3, 21), datetime.date(2026, 3, 20)),
			("1405", datetime.date(2026, 3, 21), datetime.date(2027, 3, 20)),
		]

		self.assertEqual(
			report_defaults.fiscal_year_window(datetime.date(2026, 1, 5), years),
			("2025-03-21", "2026-03-20"),
		)

	def test_overlapping_years_resolve_deterministically(self):
		years = [
			("older", "2024-01-01", "2025-12-31"),
			("newer", "2025-01-01", "2026-12-31"),
		]

		self.assertEqual(
			report_defaults.fiscal_year_window("2025-06-01", years),
			("2025-01-01", "2026-12-31"),
		)

	def test_dates_may_arrive_as_text(self):
		years = [("1404", "2025-03-21", "2026-03-20")]

		self.assertEqual(
			report_defaults.fiscal_year_window("2025-11-23", years),
			("2025-03-21", "2026-03-20"),
		)

	def test_a_mapping_row_is_read_like_a_record(self):
		"""The gateway's own read returns mappings; the keys, not their order, name the fields."""
		years = [
			{
				"name": "1404",
				"year_end_date": "2026-03-20",
				"year_start_date": "2025-03-21",
			}
		]

		self.assertEqual(
			report_defaults.fiscal_year_window("2025-11-23", years),
			("2025-03-21", "2026-03-20"),
		)

	def test_a_site_with_no_matching_year_invents_no_boundary(self):
		self.assertEqual(report_defaults.fiscal_year_window("2025-06-01", []), ("", ""))
		self.assertEqual(
			report_defaults.fiscal_year_window("2030-06-01", [("1404", "2025-03-21", "2026-03-20")]),
			("", ""),
		)


class TheReceiptSwitchTest(UnitTestCase):
	def test_it_is_a_check_that_defaults_to_on(self):
		field = field_of(read_json(SETTINGS), "sp_show_pr_warehouse_type")

		self.assertIsNotNone(field, "the receipt's warehouse-type switch is missing")
		self.assertEqual(field["fieldtype"], "Check")
		self.assertEqual(field["default"], "1")

	def test_it_is_placed_under_its_own_section(self):
		document = read_json(SETTINGS)
		order = document["field_order"]
		section = order.index("receipt_display_section")

		self.assertEqual(order[section + 1], "sp_show_pr_warehouse_type")
		self.assertEqual(field_of(document, "receipt_display_section")["fieldtype"], "Section Break")


class TheItemFlagTest(UnitTestCase):
	def test_the_checkbox_says_it_is_a_logistics_requirement(self):
		field = next(
			field for field in read_json(ITEM)["custom_fields"] if field["fieldname"] == "sp_serial_required"
		)

		self.assertEqual(field["label"], "نیازمند ثبت سریال در لجستیک")

	def test_it_stays_independent_of_the_standard_serial_flag(self):
		field = next(
			field for field in read_json(ITEM)["custom_fields"] if field["fieldname"] == "sp_serial_required"
		)

		self.assertEqual(field["fieldtype"], "Check")
		self.assertEqual(field["default"], "0")
		self.assertEqual(field["insert_after"], "has_serial_no")
		self.assertNotIn("reqd", field)
