"""Static checks for the Stage C UI endpoints, reports and workspace wiring."""

import json
import pathlib
import re

from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
MODULE = APP / "smartprocess_logistics"
REPORT_DIR = MODULE / "report"


def source(path):
	return path.read_text(encoding="utf-8")


class TestSalesEndpoints(UnitTestCase):
	def test_item_search_wraps_the_native_item_query(self):
		text = source(APP / "warehouse" / "sales_item_search.py")

		self.assertIn("from erpnext.controllers.queries import item_query", text)
		self.assertIn('query_filters = {"is_sales_item": 1}', text)
		self.assertIn("items_for_sales_invoice", text)
		self.assertIn("items_for_sales_order", text)
		self.assertIn("include_virtual", text)

	def test_physical_warehouse_query_uses_permission_aware_get_list(self):
		text = source(APP / "warehouse" / "physical_warehouse_query.py")

		self.assertIn("frappe.get_list(", text)
		self.assertIn("is_group", text)
		self.assertIn("disabled", text)
		self.assertIn("warehouse_type", text)
		self.assertNotIn("ignore_permissions", text)

	def test_new_endpoints_do_not_write_or_bypass_permissions(self):
		for name in ("sales_item_search.py", "physical_warehouse_query.py", "sales_order_progress.py"):
			text = source(APP / "warehouse" / name)
			with self.subTest(file=name):
				for forbidden in ("ignore_permissions", "frappe.db.commit", ".insert(", ".submit("):
					self.assertNotIn(forbidden, text)

	def test_settings_field_names_stay_in_the_policy_adapter(self):
		for path in (
			APP / "warehouse" / "report_data.py",
			APP / "warehouse" / "sales_item_search.py",
			APP / "warehouse" / "sales_order_progress.py",
		):
			text = source(path)
			with self.subTest(file=path.name):
				for field in (
					"sp_allocation_priority",
					"sp_virtual_stock_covers_procurement",
					"sp_recommended_purchase_net_of_open_po",
				):
					self.assertNotIn(field, text)

	def test_item_and_bin_reads_use_permission_aware_get_list(self):
		for path, patterns in (
			(
				APP / "warehouse" / "report_data.py",
				(r"get_list\(\s*ITEM_READ", r'get_list\(\s*"Bin"'),
			),
			(
				APP / "warehouse" / "sales_item_search.py",
				(r'get_list\(\s*"Bin"', r'get_list\(\s*"Item"'),
			),
		):
			text = source(path)
			with self.subTest(file=path.name):
				for pattern in patterns:
					self.assertRegex(text, pattern)

	def test_empty_warehouse_stock_path_returns_three_collections(self):
		text = source(APP / "warehouse" / "report_data.py")

		self.assertIn("return [], [], {}", text)

	def test_reports_use_the_configured_virtual_warehouse_types(self):
		for name in ("report_data.py", "sales_item_search.py"):
			text = source(APP / "warehouse" / name)
			with self.subTest(file=name):
				self.assertIn("classification.virtual_types()", text)

	def test_date_range_is_not_overwritten_by_the_second_bound(self):
		text = source(APP / "warehouse" / "report_data.py")

		self.assertIn('("between", [from_date, to_date])', text)

	def test_link_queries_apply_the_company_policy(self):
		for name in ("sales_item_search.py", "physical_warehouse_query.py"):
			text = source(APP / "warehouse" / name)
			with self.subTest(file=name):
				self.assertIn("company_scope.require_company", text)

	def test_report_and_endpoint_reads_are_bounded(self):
		report_data = source(APP / "warehouse" / "report_data.py")
		sales_item_search = source(APP / "warehouse" / "sales_item_search.py")
		warehouse_query = source(APP / "warehouse" / "physical_warehouse_query.py")
		progress = source(APP / "warehouse" / "sales_order_progress.py")

		self.assertIn("MAX_REPORT_ROWS", report_data)
		self.assertIn("def _bounded", report_data)
		self.assertIn("MAX_STOCK_ROWS", sales_item_search)
		self.assertIn("MAX_WAREHOUSE_ROWS", warehouse_query)
		self.assertIn("MAX_INVOICE_ROWS", progress)

	def test_reports_refuse_missing_dependency_permissions_explicitly(self):
		text = source(APP / "warehouse" / "report_data.py")

		self.assertIn("def _require_read", text)
		self.assertIn("frappe.PermissionError", text)
		self.assertIn("دسترسی خواندن موجودی انبار", text)
		self.assertIn("دسترسی خواندن سفارش فروش", text)
		self.assertIn("دسترسی خواندن سفارش خرید", text)


class TestReports(UnitTestCase):
	def report_docs(self):
		return [
			(path, json.loads(path.read_text(encoding="utf-8")))
			for path in sorted(REPORT_DIR.glob("*/*.json"))
		]

	def test_the_five_stage_c_reports_are_defined(self):
		names = {doc["name"] for _path, doc in self.report_docs()}

		self.assertTrue(
			{
				"Warehouse Inventory",
				"Inventory and Procurement Dashboard",
				"Sales Orders Ready for Invoicing",
				"Procurement Shortage",
				"Item Weight and Volume",
			}
			<= names
		)

	def test_every_report_has_a_company_filter(self):
		for path, doc in self.report_docs():
			if doc["name"] not in {
				"Warehouse Inventory",
				"Inventory and Procurement Dashboard",
				"Sales Orders Ready for Invoicing",
				"Procurement Shortage",
				"Item Weight and Volume",
			}:
				continue

			js = path.with_suffix(".js").read_text(encoding="utf-8")
			with self.subTest(report=doc["name"]):
				self.assertIn('fieldname: "company"', js)
				self.assertIn("reqd: 1", js)

	def test_ready_for_invoicing_keeps_physical_and_incoming_separate(self):
		path = REPORT_DIR / "sales_orders_ready_for_invoicing" / "sales_orders_ready_for_invoicing.py"
		text = source(path)

		self.assertIn("suggested_invoice_qty", text)
		self.assertIn("incoming_qty", text)
		self.assertIn("allocated_physical_qty", text)
		self.assertIn("allocated_virtual_qty", text)
		self.assertIn("billing_note", text)

	def test_report_c_has_an_operational_path_without_sales_invoice_read(self):
		report_data = source(APP / "warehouse" / "report_data.py")
		report = source(
			REPORT_DIR / "sales_orders_ready_for_invoicing" / "sales_orders_ready_for_invoicing.py"
		)
		progress = source(APP / "warehouse" / "sales_order_progress.py")
		reporting = source(APP / "core" / "reporting.py")

		self.assertIn("operational_readiness", report_data)
		self.assertIn("operational_ready_invoicing_rows", report_data)
		self.assertIn("invoiced_qty_available", reporting)
		self.assertIn("billing_note", report)
		self.assertIn("billing_available", progress)
		self.assertIn('"invoiced_qty": None', progress)

	def test_report_roles_match_the_documents_each_report_reads(self):
		expected = {
			"Warehouse Inventory": ("Bin", {"Stock User"}),
			"Inventory and Procurement Dashboard": ("Item", {"Stock User"}),
			"Sales Orders Ready for Invoicing": (
				"Sales Order",
				{"Sales User", "Accounts User", "Accounts Manager", "Stock User"},
			),
			"Procurement Shortage": ("Item", {"Stock User"}),
			"Item Weight and Volume": (
				"Purchase Invoice",
				{"Purchase User", "Accounts User", "Accounts Manager"},
			),
		}
		docs = {doc["name"]: doc for _path, doc in self.report_docs()}

		for name, (ref_doctype, roles) in expected.items():
			with self.subTest(report=name):
				doc = docs[name]
				self.assertEqual(doc["ref_doctype"], ref_doctype)
				self.assertEqual({row["role"] for row in doc["roles"]}, roles)


class TestWorkspaceAndHooks(UnitTestCase):
	def test_workspace_links_the_reports(self):
		path = MODULE / "workspace" / "warehouse_and_purchasing" / "warehouse_and_purchasing.json"
		doc = json.loads(path.read_text(encoding="utf-8"))
		links = {link["link_to"] for link in doc["links"] if link.get("link_type") == "Report"}

		self.assertEqual(
			links,
			{
				"Warehouse Inventory",
				"Inventory and Procurement Dashboard",
				"Sales Orders Ready for Invoicing",
				"Procurement Shortage",
				"Item Weight and Volume",
			},
		)

	def test_hooks_register_the_new_form_scripts(self):
		text = source(APP / "hooks_composition.py")

		self.assertIn("sales_invoice_warehouse_stock.js", text)
		self.assertIn("sales_order_stock_visibility.js", text)
		self.assertIn('"Sales Order": "public/js/sales_order_stock_visibility.js"', text)

	def test_the_progress_panel_shows_the_unavailable_billing_state(self):
		text = source(APP / "public" / "js" / "sales_order_stock_visibility.js")

		self.assertIn("billing_available", text)
		self.assertIn("billing_note", text)
		self.assertIn('"—"', text)

	def test_the_progress_panel_calls_the_get_only_endpoint_with_get(self):
		text = source(APP / "public" / "js" / "sales_order_stock_visibility.js")

		self.assertIn('"GET"', text)
