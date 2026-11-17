"""The classification table: what it is made of, who may change it, and who answers.

The rules themselves are pure and live in `tests/test_warehouse_policy.py`; the resolver's
behaviour with a site is not reachable without a bench. What is reachable from disk is the
artifact and the wiring, so this file pins both: the two DocType definitions, the permission row
that decides who may rewrite every warehouse's operational answer, and the fact that the server
module asks the resolver instead of repeating it.

The last group is the one that will fail first if the rule is ever copied: it holds every path
that classifies a warehouse to the single answer in `warehouse/classification.py`, including the
form script, which is where a second copy of a Persian default would otherwise hide.
"""

import json
import pathlib
import re

from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.warehouse import policy

APP = pathlib.Path(__file__).resolve().parent.parent
MODULE = APP / "smartprocess_logistics"

SETTINGS_JSON = MODULE / "doctype" / "logistics_settings" / "logistics_settings.json"
CHILD_JSON = (
	MODULE / "doctype" / "logistics_warehouse_classification" / "logistics_warehouse_classification.json"
)
SERVER_MODULE = APP / "warehouse" / "classification_settings.py"
RESOLVER = APP / "warehouse" / "classification.py"
FORM_SCRIPT = APP / "public" / "js" / "logistics_settings_classification.js"

TABLE = "warehouse_classifications"
SECTION = "classification_section"
CHILD_DOCTYPE = "Logistics Warehouse Classification"


def read_json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def field_of(document, fieldname):
	return next((field for field in document["fields"] if field["fieldname"] == fieldname), None)


class TestTheSettingsField(UnitTestCase):
	def test_the_table_is_a_table_of_the_child_doctype(self):
		field = field_of(read_json(SETTINGS_JSON), TABLE)

		self.assertIsNotNone(field, "the classification table is not on Logistics Settings")
		self.assertEqual(field["fieldtype"], "Table")
		self.assertEqual(field["options"], CHILD_DOCTYPE)
		self.assertEqual(field["label"], "طبقه‌بندی انواع انبار")

	def test_the_warning_sits_above_the_table_and_names_every_effect(self):
		"""The section break is the only place an administrator is told what a mapping moves."""
		document = read_json(SETTINGS_JSON)
		order = document["field_order"]
		section = field_of(document, SECTION)

		self.assertIsNotNone(section)
		self.assertEqual(section["fieldtype"], "Section Break")
		self.assertEqual(order.index(SECTION), order.index(TABLE) - 1, "the warning does not come first")

		description = section["description"]
		for effect in ("قابل فروش", "Delivery Note", "رزرو", "کسری تأمین", "Stock Entry"):
			with self.subTest(effect=effect):
				self.assertIn(effect, description)

	def test_the_warning_promises_that_nothing_else_is_written(self):
		description = field_of(read_json(SETTINGS_JSON), SECTION)["description"]

		self.assertIn("Warehouse", description)
		self.assertIn("تغییر نمی‌کند", description)
		self.assertIn("بازنویسی نمی‌شود", description)
		self.assertIn("جدول خالی است", description, "an empty table must say it keeps the current behaviour")

	def test_the_table_says_that_an_incomplete_one_is_refused(self):
		description = field_of(read_json(SETTINGS_JSON), TABLE).get("description") or ""

		self.assertIn("ناقص", description)
		self.assertIn("بدون نوع", description)

	def test_only_an_administrator_role_may_write_it(self):
		"""The permission table is the first gate; the validate handler asks again."""
		permissions = read_json(SETTINGS_JSON)["permissions"]

		self.assertEqual(
			{row["role"] for row in permissions if row.get("write")},
			{"Logistics Serial Manager", "System Manager"},
		)
		self.assertTrue(
			{"Logistics Serial Viewer", "Logistics Serial Editor"}
			<= {row["role"] for row in permissions if row.get("read")},
			"the two read-only roles must still be able to open the settings",
		)


class TestTheChildDocType(UnitTestCase):
	def test_it_is_a_table_row_with_no_permissions_of_its_own(self):
		document = read_json(CHILD_JSON)

		self.assertEqual(document["name"], CHILD_DOCTYPE)
		self.assertEqual(document.get("istable"), 1)
		self.assertEqual(document.get("permissions"), [], "a child row is reached through its parent")

	def test_a_row_is_a_type_and_one_of_three_answers(self):
		document = read_json(CHILD_JSON)
		warehouse_type = field_of(document, "warehouse_type")
		classification = field_of(document, "classification")

		self.assertEqual(warehouse_type["fieldtype"], "Link")
		self.assertEqual(warehouse_type["options"], "Warehouse Type")
		self.assertFalse(warehouse_type.get("reqd"), "the blank row is the bucket for untyped warehouses")

		self.assertEqual(classification["fieldtype"], "Select")
		self.assertEqual(classification["options"].split("\n"), list(policy.CLASSIFICATIONS))
		self.assertTrue(classification.get("reqd"))

	def test_the_stored_words_are_the_ones_the_resolver_uses(self):
		"""A Desk Select shows the stored string as its own label, so the code is the word."""
		classification = field_of(read_json(CHILD_JSON), "classification")

		self.assertIn(classification["default"], policy.CLASSIFICATIONS)


class TestTheServerModule(UnitTestCase):
	def source(self):
		return SERVER_MODULE.read_text(encoding="utf-8")

	def test_it_never_ignores_permissions(self):
		for path in (SERVER_MODULE, RESOLVER):
			with self.subTest(path=path.name):
				self.assertNotIn("ignore_permissions", path.read_text(encoding="utf-8"))

	def test_the_save_is_refused_for_a_user_who_may_not_write(self):
		"""The check is in the handler, not only in the permission table: a client can skip the form."""
		source = self.source()

		self.assertRegex(
			source,
			r'has_permission\(\s*classification\.SETTINGS_DOCTYPE,\s*"write"\s*\)',
			"the validate handler no longer asks for the write permission itself",
		)

	def test_the_read_behind_the_form_asks_for_the_read_permission(self):
		source = self.source()
		rows = re.search(r"def classification_rows\(.*?\n\n\n", source, re.DOTALL)

		self.assertIsNotNone(rows)
		self.assertRegex(rows.group(0), r'has_permission\(\s*classification\.SETTINGS_DOCTYPE,\s*"read"\s*\)')
		self.assertIn('@frappe.whitelist(methods=["GET"])', source)

	def test_the_form_reads_the_written_table_and_nothing_else(self):
		source = self.source()

		self.assertIn("classification.stored_rows()", source)
		self.assertIn("classification.known_types()", source)
		self.assertIn("policy.UNTYPED", source)

	def test_a_saved_mapping_is_dropped_after_the_write(self):
		from smartprocess_logistics import hooks_composition

		self.assertEqual(
			hooks_composition.approved_doc_events()["Logistics Settings"]["on_update"],
			"smartprocess_logistics.warehouse.classification_settings.forget_classification_mapping",
		)


class TestTheOneResolver(UnitTestCase):
	def test_the_resolver_folds_the_mapping_into_the_guard_tuple(self):
		"""Every operational guard asks one tuple; this is the line that puts the table into it."""
		source = RESOLVER.read_text(encoding="utf-8")

		self.assertIn("return policy.non_physical_names(policy_types(), stored_mapping())", source)

	def test_the_binary_guards_ask_the_mapping_aware_answer(self):
		"""`is_virtual` and `physical_warehouses` must fold a stored `blank → virtual` row, which a
		tuple of names cannot express: the blank type is the empty string, and membership treats it
		as physical before any tuple. Both call the resolver with the policy types and the stored
		mapping rather than the folded tuple."""
		source = RESOLVER.read_text(encoding="utf-8")

		self.assertIn(
			"policy.is_virtual_type(warehouse_type, policy_types(), stored_mapping())", source
		)
		self.assertIn(
			'policy.is_virtual_type(row.get("warehouse_type"), policy_types(), stored_mapping())',
			source,
		)

	def test_no_other_module_defines_a_classification_answer(self):
		offenders = []
		for path in sorted(APP.rglob("*.py")):
			if path == RESOLVER or "tests" in path.parts:
				continue
			source = path.read_text(encoding="utf-8", errors="replace")
			for name in ("def virtual_types", "def classification_of_type", "def is_virtual("):
				if name in source:
					offenders.append(f"{path.relative_to(APP)}: {name}")

		self.assertEqual(offenders, [])

	def test_every_site_caller_passes_the_resolver_and_not_the_shipped_default(self):
		"""The pure default is a signature in `policy.py`; a site caller must never leave it in place."""
		offenders = []
		for path in sorted((APP / "warehouse").glob("*.py")):
			if path == RESOLVER or path.name == "policy.py":
				continue
			for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
				if "virtual_types=" in line and "virtual_types=classification.virtual_types()" not in line:
					offenders.append(f"{path.name}:{number}: {line.strip()}")

		self.assertEqual(offenders, [])

	def test_the_form_script_carries_no_classification_of_its_own(self):
		source = FORM_SCRIPT.read_text(encoding="utf-8")

		for word in policy.CLASSIFICATIONS:
			with self.subTest(word=word):
				self.assertNotIn(f'"{word}"', source, "the default belongs to the server, not the browser")

	def test_the_form_script_calls_the_endpoint_this_module_exports(self):
		source = FORM_SCRIPT.read_text(encoding="utf-8")

		self.assertIn("smartprocess_logistics.warehouse.classification_settings.classification_rows", source)
		self.assertIn("def classification_rows(", SERVER_MODULE.read_text(encoding="utf-8"))

	def test_the_form_script_only_warns_about_a_saved_table_that_is_missing_a_row(self):
		"""Appending to a saved table would look like a change and be saved as one."""
		source = FORM_SCRIPT.read_text(encoding="utf-8")

		self.assertIn("show_alert", source)
		self.assertEqual(source.count("add_child("), 1)
