"""The two standard records this app ships as fixtures, and the label that names them.

`frappe.utils.fixtures.sync_fixtures` imports every `fixtures/*.json` on install and on
migrate, and each record is imported by its own name: a repeat replaces it rather than adding a
second one. That is why the files are pinned here — a record that lost its name would be
re-created with a generated one on every migrate, and a second `Physical` type would make the
classification ambiguous.
"""

import csv
import json
import pathlib

from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.warehouse import policy

APP = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = APP / "fixtures"
PHYSICAL = "Physical"
PHYSICAL_LABEL = "فیزیکی"


def records(name):
	return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestTheShippedFixture(UnitTestCase):
	def test_the_physical_type_is_shipped_once_with_a_fixed_name(self):
		rows = records("warehouse_type.json")

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["doctype"], "Warehouse Type")
		self.assertEqual(rows[0]["name"], PHYSICAL)
		self.assertEqual(rows[0]["description"], PHYSICAL_LABEL)
		self.assertFalse(policy.is_virtual_type(rows[0]["name"]))

	def test_the_default_setter_names_the_warehouse_field_and_the_physical_type(self):
		rows = records("property_setter.json")
		row = next(item for item in rows if item["property"] == "default")

		self.assertEqual(row["doctype"], "Property Setter")
		self.assertEqual(row["doctype_or_field"], "DocField")
		self.assertEqual(row["doc_type"], "Warehouse")
		self.assertEqual(row["field_name"], "warehouse_type")
		self.assertEqual(row["value"], PHYSICAL)
		self.assertTrue(row["name"], "a generated name would add a second setter on every migrate")

	def test_the_label_is_translatable(self):
		"""`build_for_autosuggest` translates a Link label only for a `translated_doctype`."""
		rows = records("property_setter.json")
		row = next(item for item in rows if item["property"] == "translated_doctype")

		self.assertEqual(row["doctype_or_field"], "DocType")
		self.assertEqual(row["doc_type"], "Warehouse Type")
		self.assertEqual(row["property_type"], "Check")
		self.assertEqual(str(row["value"]), "1")
		self.assertTrue(row["name"])

	def test_the_only_setters_shipped_are_the_three_expected_ones(self):
		rows = records("property_setter.json")

		self.assertEqual(len(rows), 2)
		self.assertEqual(
			sorted((row["doc_type"], row.get("field_name") or "", row["property"]) for row in rows),
			[
				("Warehouse", "warehouse_type", "default"),
				("Warehouse Type", "", "translated_doctype"),
			],
		)

	def test_no_other_record_is_shipped(self):
		"""A fixture folder is imported wholesale; only these two files may live in it."""
		self.assertEqual(
			sorted(path.name for path in FIXTURES.glob("*.json")),
			["property_setter.json", "warehouse_type.json"],
		)

	def test_the_hooks_declare_every_shipped_record(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")

		self.assertIn("fixtures = [", hooks)
		self.assertIn('"dt": "Warehouse Type"', hooks)
		self.assertIn('"dt": "Property Setter"', hooks)
		self.assertIn('"translated_doctype"', hooks)

	def test_the_persian_label_is_in_the_translation_catalogue(self):
		with (APP / "translations" / "fa.csv").open(encoding="utf-8", newline="") as handle:
			rows = list(csv.reader(handle))

		self.assertIn([PHYSICAL, PHYSICAL_LABEL], rows)
