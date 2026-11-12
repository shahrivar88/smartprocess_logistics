"""Static checks for Stage D fields, snapshot hooks and report wiring."""

import json
import pathlib

from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
MODULE = APP / "smartprocess_logistics"
CUSTOM = MODULE / "custom"


def read_json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def field(doc, fieldname):
	return next((row for row in doc["custom_fields"] if row["fieldname"] == fieldname), None)


class TestSettingsFields(UnitTestCase):
	def settings(self):
		return read_json(MODULE / "doctype" / "logistics_settings" / "logistics_settings.json")

	def test_the_three_inventory_policy_fields_exist(self):
		doc = self.settings()
		fields = {row["fieldname"]: row for row in doc["fields"]}

		self.assertEqual(fields["sp_allocation_priority"]["fieldtype"], "Select")
		self.assertEqual(fields["sp_allocation_priority"]["default"], "order_then_delivery")
		self.assertEqual(fields["sp_virtual_stock_covers_procurement"]["default"], "1")
		self.assertEqual(fields["sp_recommended_purchase_net_of_open_po"]["default"], "1")
		self.assertEqual(fields["sp_inventory_policy_version"]["fieldtype"], "Int")
		self.assertEqual(fields["sp_inventory_policy_version"]["default"], "0")
		self.assertTrue(fields["sp_inventory_policy_version"]["read_only"])
		self.assertTrue(fields["sp_inventory_policy_version"]["hidden"])

	def test_the_fields_are_in_a_section_and_a_tab(self):
		doc = self.settings()
		fields = {row["fieldname"]: row for row in doc["fields"]}
		order = doc["field_order"]

		self.assertLess(order.index("inventory_section"), order.index("sp_allocation_priority"))
		self.assertEqual(fields["inventory_section"]["fieldtype"], "Section Break")
		self.assertEqual(fields["inventory_tab"]["fieldtype"], "Tab Break")

	def test_report_data_reads_policy_dynamically(self):
		source = (APP / "warehouse" / "report_data.py").read_text(encoding="utf-8")

		self.assertIn("policies.inventory_policy()", source)
		self.assertIn("policy.allocation_priority", source)
		self.assertIn("policy.recommended_purchase_net_of_open_po", source)
		self.assertIn("fulfilment.stock_by_item", source)
		self.assertIn("transit_by_item", source)
		self.assertNotIn("policy.virtual_stock_covers_procurement", source)


class TestSettingsTranslations(UnitTestCase):
	def test_select_option_labels_keep_their_internal_values(self):
		translations = (APP / "translations" / "fa.csv").read_text(encoding="utf-8")

		for value, label in (
			("oldest_order", "قدیمی‌ترین سفارش"),
			("earliest_delivery", "نزدیک‌ترین تاریخ تحویل"),
			("order_then_delivery", "تاریخ سفارش و سپس تاریخ تحویل"),
		):
			with self.subTest(value=value):
				self.assertIn(f"{value},{label}", translations)

		doc = read_json(MODULE / "doctype" / "logistics_settings" / "logistics_settings.json")
		priority = next(row for row in doc["fields"] if row["fieldname"] == "sp_allocation_priority")
		self.assertEqual(
			priority["options"].splitlines(),
			["oldest_order", "earliest_delivery", "order_then_delivery"],
		)
		self.assertIn("Warehouse Inventory,موجودی انبار", translations)


class TestItemFields(UnitTestCase):
	def test_the_four_measurement_fields_are_custom_and_non_negative(self):
		doc = read_json(CUSTOM / "item.json")

		for name in ("sp_gross_weight", "sp_length_cm", "sp_width_cm", "sp_height_cm"):
			with self.subTest(field=name):
				row = field(doc, name)
				self.assertIsNotNone(row)
				self.assertEqual(row["fieldtype"], "Float")
				self.assertTrue(row["non_negative"])
				self.assertNotIn("default", row)

	def test_each_measurement_has_a_presence_check(self):
		doc = read_json(CUSTOM / "item.json")

		for name in (
			"sp_gross_weight_entered",
			"sp_length_cm_entered",
			"sp_width_cm_entered",
			"sp_height_cm_entered",
		):
			with self.subTest(field=name):
				row = field(doc, name)
				self.assertIsNotNone(row)
				self.assertEqual(row["fieldtype"], "Check")
				self.assertEqual(row["default"], "0")
				self.assertFalse(row.get("hidden"))
				self.assertFalse(row.get("read_only"))

	def test_there_is_no_redundant_editable_volume_field(self):
		doc = read_json(CUSTOM / "item.json")
		names = {row["fieldname"] for row in doc["custom_fields"]}

		self.assertNotIn("sp_volume", names)


class TestSnapshotFields(UnitTestCase):
	def check_child(self, filename):
		doc = read_json(CUSTOM / filename)

		for name in ("sp_gross_weight", "sp_length_cm", "sp_width_cm", "sp_height_cm"):
			with self.subTest(file=filename, field=name):
				row = field(doc, name)
				self.assertIsNotNone(row)
				self.assertEqual(row["fieldtype"], "Float")
				self.assertTrue(row["read_only"])
				self.assertTrue(row["non_negative"])
				self.assertNotIn("default", row)

		marker = field(doc, "sp_measurement_snapshot")
		self.assertIsNotNone(marker)
		self.assertEqual(marker["fieldtype"], "Check")
		self.assertTrue(marker["read_only"])
		self.assertEqual(marker["default"], "0")

		item = field(doc, "sp_measurement_item")
		self.assertIsNotNone(item)
		self.assertEqual(item["fieldtype"], "Data")
		self.assertTrue(item["read_only"])
		self.assertTrue(item["hidden"])
		self.assertEqual(item["insert_after"], "sp_measurement_snapshot")

		presence = field(doc, "sp_measurement_presence")
		self.assertIsNotNone(presence)
		self.assertEqual(presence["fieldtype"], "Data")
		self.assertTrue(presence["read_only"])
		self.assertTrue(presence["hidden"])
		self.assertEqual(presence["insert_after"], "sp_measurement_item")
		self.assertEqual(doc["sync_on_migrate"], 1)

	def test_purchase_order_item(self):
		self.check_child("purchase_order_item.json")

	def test_purchase_invoice_item(self):
		self.check_child("purchase_invoice_item.json")

	def test_purchase_receipt_item(self):
		self.check_child("purchase_receipt_item.json")

	def test_snapshot_hooks_run_before_submit_only(self):
		text = (APP / "hooks_composition.py").read_text(encoding="utf-8")

		for doctype in ("Purchase Order", "Purchase Invoice", "Purchase Receipt"):
			with self.subTest(doctype=doctype):
				self.assertIn(f'"{doctype}"', text)
		self.assertEqual(text.count("smartprocess_logistics.measurements.snapshot_purchase_document"), 3)
		self.assertIn('"before_submit"', text)
		self.assertNotIn('"after_migrate": "smartprocess_logistics.measurements', text)

	def test_the_snapshot_handler_does_not_write_history_or_commit(self):
		text = (APP / "measurements.py").read_text(encoding="utf-8")

		for forbidden in (
			"frappe.db.set_value",
			"frappe.db.commit",
			"after_migrate",
			"scheduler",
			"on_cancel",
			"after_cancel",
			"on_update_after_submit",
			"frappe.db.sql",
			"ignore_permissions",
		):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, text)

	def test_snapshots_are_not_backfilled_or_resynced(self):
		text = (APP / "measurements.py").read_text(encoding="utf-8")

		self.assertIn("before_submit", (APP / "hooks_composition.py").read_text(encoding="utf-8"))
		self.assertNotIn("patch", text.lower())
		self.assertNotIn("scheduler_events", (APP / "hooks_composition.py").read_text(encoding="utf-8"))

	def test_the_snapshot_handler_checks_the_item_identity(self):
		text = (APP / "measurements.py").read_text(encoding="utf-8")

		self.assertIn("sp_measurement_item", text)
		self.assertIn("snapshot_item_matches", text)
		self.assertIn("row.set(MEASUREMENT_ITEM, item_code)", text)
		self.assertIn("row.set(PRESENCE", text)

	def test_the_item_validator_marks_positive_measurements(self):
		text = (APP / "measurements.py").read_text(encoding="utf-8")
		hooks = (APP / "hooks_composition.py").read_text(encoding="utf-8")

		self.assertIn("def validate_item_measurements", text)
		self.assertIn("smartprocess_logistics.measurements.validate_item_measurements", hooks)

	def test_the_snapshot_handler_runs_on_submit_and_only_trusts_amendments(self):
		text = (APP / "measurements.py").read_text(encoding="utf-8")

		self.assertIn('getattr(doc, "_action", None) != "submit"', text)
		self.assertNotIn("doc.docstatus != 0", text)
		self.assertIn("amended_from", text)


class TestCargoReference(UnitTestCase):
	def test_purchase_receipt_has_an_optional_cargo_reference(self):
		doc = read_json(CUSTOM / "purchase_receipt.json")
		row = field(doc, "sp_cargo_ref")

		self.assertIsNotNone(row)
		self.assertEqual(row["fieldtype"], "Data")
		self.assertEqual(row["label"], "شناسه بار")
		self.assertFalse(row.get("reqd"))
		self.assertTrue(row.get("in_standard_filter"))
		self.assertFalse(row.get("unique"))
		self.assertFalse(row.get("set_only_once"))

	def test_no_cargo_doctype_is_smuggled_in(self):
		names = {path.parent.name for path in MODULE.glob("doctype/*/*.json")}

		self.assertNotIn("cargo", names)
		self.assertNotIn("cargo_tracking", names)


class TestWeightReport(UnitTestCase):
	def test_the_report_exposes_cargo_and_cubic_metres(self):
		source = (MODULE / "report" / "item_weight_and_volume" / "item_weight_and_volume.py").read_text(
			encoding="utf-8"
		)

		self.assertIn("cargo_ref", source)
		self.assertIn("volume_m3", source)
		self.assertIn("measurement_source", source)
