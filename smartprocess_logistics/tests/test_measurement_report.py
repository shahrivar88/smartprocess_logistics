"""Static checks for the grouped weight/volume report and the receipt summary panel."""

import json
import pathlib

from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
REPORT_DIR = APP / "smartprocess_logistics" / "report" / "item_weight_and_volume"


def source(path):
	return path.read_text(encoding="utf-8")


class TestWeightVolumeReport(UnitTestCase):
	def report_py(self):
		return source(REPORT_DIR / "item_weight_and_volume.py")

	def test_the_report_shows_document_rows_and_coverage(self):
		text = self.report_py()

		for label in (
			"نوع ردیف",
			"جمع سند",
			"پوشش اندازه‌گیری",
			"هشدار",
			"مقدار ردشده (Stock UOM)",
			"وزن خالص ردشده",
			"حجم ردشده (cm³)",
		):
			with self.subTest(label=label):
				self.assertIn(label, text)

	def test_document_summaries_reuse_the_core_aggregation(self):
		text = source(APP / "warehouse" / "report_data.py")

		self.assertIn("measurement_totals.document_totals", text)
		self.assertIn("measurement_summary.make_converter", text)
		self.assertIn("_with_document_summaries", text)
		self.assertIn('"row_type": "جمع سند"', text)
		self.assertIn('"row_type": "ردیف کالا"', text)
		self.assertIn("net_available=net_present", text)
		self.assertIn("rejected_stock_qty", text)
		self.assertIn("weight_uom", text)

	def test_the_report_offers_a_target_weight_uom_filter(self):
		text = source(REPORT_DIR / "item_weight_and_volume.js")

		self.assertIn('fieldname: "weight_uom"', text)
		self.assertIn("واحد وزن هدف", text)
		self.assertIn('data.row_type === "جمع سند"', text)

	def test_the_core_aggregation_has_no_frappe_import(self):
		text = source(APP / "core" / "measurement_totals.py")

		self.assertNotIn("import frappe", text)
		self.assertNotIn("from frappe", text)
		self.assertIn("from smartprocess_logistics.core.measurements import", text)

	def test_the_summary_layers_do_not_duplicate_the_per_unit_formula(self):
		text = source(APP / "warehouse" / "measurement_summary.py")

		self.assertIn("measurement_totals.document_totals", text)
		self.assertNotIn("weight_per_unit * ", text)
		self.assertNotIn("volume_cm3 =", text)
		self.assertIn("get_uom_conv_factor", text)

	def test_missing_and_zero_are_distinguished(self):
		core = source(APP / "core" / "measurement_totals.py")
		summary = source(APP / "warehouse" / "measurement_summary.py")

		self.assertIn("def weight_is_present", core)
		self.assertIn("net_missing", core)
		self.assertIn("net_covered", core)
		self.assertIn("نامشخص است", summary)
		self.assertIn("جمع ناقص", summary)


class TestPurchaseReceiptPanel(UnitTestCase):
	def test_the_form_script_is_registered_and_read_only(self):
		hooks = source(APP / "hooks_composition.py")
		panel = source(APP / "public" / "js" / "purchase_receipt_measurements.js")

		self.assertIn('"Purchase Receipt": "public/js/purchase_receipt_measurements.js"', hooks)
		self.assertIn("purchase_receipt_measurements.purchase_receipt_measurements", panel)
		self.assertIn("frm.dashboard.add_section", panel)
		self.assertIn("وزن خالص پذیرفته‌شده", panel)
		self.assertIn("وزن ناخالص پذیرفته‌شده", panel)
		self.assertIn("حجم پذیرفته‌شده", panel)
		self.assertIn("وزن خالص ردشده", panel)
		self.assertIn("وزن ناخالص ردشده", panel)
		self.assertIn("refresh", panel)
		self.assertIn("__unsaved", panel)

		for forbidden in ("set_value", ".save(", ".submit(", "ignore_permissions"):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, panel)

	def test_the_endpoint_is_a_permissioned_get_and_never_writes(self):
		text = source(APP / "warehouse" / "purchase_receipt_measurements.py")

		self.assertIn('@frappe.whitelist(methods=["GET"])', text)
		self.assertIn("frappe.has_permission", text)
		self.assertIn('frappe.PermissionError', text)
		self.assertIn("purchase_receipt_summary", text)

		for forbidden in ("ignore_permissions", "frappe.db.commit", ".insert(", ".submit(", ".save("):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, text)

	def test_the_endpoint_uses_the_same_summary_engine(self):
		text = source(APP / "warehouse" / "purchase_receipt_measurements.py")
		summary = source(APP / "warehouse" / "measurement_summary.py")

		self.assertIn("from smartprocess_logistics.warehouse import measurement_summary", text)
		self.assertIn("def purchase_receipt_summary", summary)
		self.assertIn("measurement_totals.document_totals", summary)


class TestReportJson(UnitTestCase):
	def test_the_report_keeps_rows_and_adds_no_blind_total_row(self):
		doc = json.loads((REPORT_DIR / "item_weight_and_volume.json").read_text(encoding="utf-8"))

		self.assertEqual(doc["name"], "Item Weight and Volume")
		self.assertEqual(doc["report_type"], "Script Report")
		self.assertEqual(doc["add_total_row"], 0)
