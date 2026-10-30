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

	def test_warehouse_selector_adds_actual_stock_with_one_bin_read(self):
		text = source(APP / "warehouse" / "physical_warehouse_query.py")

		self.assertIn("warehouse_stock_text", text)
		self.assertIn("actual_qty", text)
		self.assertNotIn("projected_qty", text)
		self.assertIn("item_code", text)
		self.assertIn('frappe.get_list(\n\t\t"Bin"', text)
		self.assertEqual(text.count('frappe.get_list(\n\t\t"Bin"'), 1)
		self.assertIn("has_permission", text)

	def test_sales_invoice_warehouse_script_sends_the_current_item(self):
		text = source(APP / "public" / "js" / "sales_invoice_warehouse_stock.js")

		self.assertIn("child_item_code", text)
		self.assertIn("item_code", text)
		self.assertIn("company: install", text)
		self.assertIn("Sales Invoice Item", text)

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


class TestStockVisibilitySettings(UnitTestCase):
	"""The seven independent display switches, and the wiring that reads each one."""

	FIELDS = (
		"sp_show_si_item_stock",
		"sp_show_warehouse_stock_in_selector",
		"sp_si_physical_warehouses_only",
		"sp_show_quotation_item_stock",
		"sp_show_quotation_warehouse_stock",
		"sp_show_so_item_stock",
		"sp_show_so_warehouse_stock",
	)

	def settings(self):
		path = MODULE / "doctype" / "logistics_settings" / "logistics_settings.json"
		doc = json.loads(path.read_text(encoding="utf-8"))
		return doc, {field.get("fieldname"): field for field in doc["fields"]}

	def test_seven_check_switches_default_to_enabled(self):
		_doc, fields = self.settings()

		for name in self.FIELDS:
			with self.subTest(fieldname=name):
				field = fields[name]
				self.assertEqual(field["fieldtype"], "Check")
				self.assertEqual(field["default"], "1")

	def test_they_live_under_the_inventory_tab(self):
		doc, _fields = self.settings()
		tab, section = None, None

		for name in doc["field_order"]:
			field = next((row for row in doc["fields"] if row["fieldname"] == name), None)
			if field and field["fieldtype"] == "Tab Break":
				tab = name
			if field and field["fieldtype"] == "Section Break":
				section = name
			if name in self.FIELDS:
				with self.subTest(fieldname=name):
					self.assertEqual(tab, "inventory_tab")
					self.assertEqual(section, "display_section")

	def test_the_server_reads_the_same_names_the_desk_does(self):
		constants = source(APP / "warehouse" / "visibility_settings.py")

		for name in self.FIELDS:
			with self.subTest(fieldname=name):
				self.assertIn(f'"{name}"', constants)

	def test_a_missing_singles_row_reads_as_the_default_not_as_off(self):
		"""`get_single_value` turns a missing Check into zero, which would hide a new switch."""
		text = source(APP / "warehouse" / "visibility_settings.py")

		self.assertIn("get_singles_dict", text)
		self.assertIn("field.default", text)
		self.assertIn("field is None", text)

	def test_the_boot_payload_carries_the_switches(self):
		hooks = source(APP / "hooks.py")

		self.assertIn('boot_session = "smartprocess_logistics.boot.boot_session"', hooks)
		self.assertIn("/assets/smartprocess_logistics/js/visibility.js", hooks)
		self.assertTrue((APP / "boot.py").exists())
		self.assertTrue((APP / "public" / "js" / "visibility.js").exists())

	def test_each_document_reads_its_own_switches(self):
		item_search = source(APP / "warehouse" / "sales_item_search.py")
		warehouses = source(APP / "warehouse" / "physical_warehouse_query.py")

		for text, names in (
			(
				item_search,
				("SHOW_SI_ITEM_STOCK", "SHOW_QUOTATION_ITEM_STOCK", "SHOW_SO_ITEM_STOCK"),
			),
			(
				warehouses,
				(
					"SHOW_SI_WAREHOUSE_STOCK",
					"SI_PHYSICAL_WAREHOUSES_ONLY",
					"SHOW_QUOTATION_WAREHOUSE_STOCK",
					"SHOW_SO_WAREHOUSE_STOCK",
				),
			),
		):
			for name in names:
				with self.subTest(setting=name):
					self.assertIn(f"visibility_settings.{name}", text)

	def test_the_quotation_and_sales_order_selectors_offer_both_kinds(self):
		text = source(APP / "warehouse" / "physical_warehouse_query.py")

		for endpoint in ("quotation_warehouses", "sales_order_warehouses"):
			with self.subTest(endpoint=endpoint):
				self.assertIn(f"def {endpoint}(", text)
		self.assertEqual(text.count("physical_only=False"), 2)

	def test_a_virtual_warehouse_is_labelled_where_a_selector_offers_it(self):
		"""It is a real warehouse with real stock, but it cannot supply a sale."""
		text = source(APP / "warehouse" / "physical_warehouse_query.py")

		self.assertIn('VIRTUAL_ROW_LABEL = "انبار مجازی"', text)
		self.assertIn("def _warehouse_label(", text)
		self.assertIn("warehouse_type", text)

	def test_only_the_quotation_endpoint_is_new_in_the_item_search(self):
		text = source(APP / "warehouse" / "sales_item_search.py")

		self.assertIn("def items_for_quotation(", text)
		self.assertIn("QUOTATION_QUERY =", text)

	def test_the_forms_register_the_scripts_they_need(self):
		hooks = source(APP / "hooks_composition.py")

		self.assertIn('"Quotation": "public/js/quotation_stock_visibility.js"', hooks)
		self.assertIn("sales_order_stock_visibility.js", hooks)

	def test_every_form_asks_the_shared_switch_reader(self):
		for name in (
			"sales_invoice_warehouse_stock.js",
			"quotation_stock_visibility.js",
			"sales_order_stock_visibility.js",
		):
			text = source(APP / "public" / "js" / name)

			with self.subTest(script=name):
				self.assertIn("smartprocess_logistics.visibility", text)
				self.assertIn(".enabled(", text)
				self.assertNotIn("ignore_permissions", text)


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
