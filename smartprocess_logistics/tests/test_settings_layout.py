"""Which tab holds which setting, pinned so a later edit cannot quietly scatter them again.

The settings row is one Single with three tabs, and this file is the contract for what belongs in
each. It exists because the change it was written with was a **move**: every serial setting was
gathered into one tab, and a move is the kind of change that is right once and wrong the next time
somebody adds a field in a hurry. Reading the definition here means the next field has to be placed
deliberately, and that moving one out of its tab fails a test rather than a review.

It reads JSON from disk and derives from `UnitTestCase`, so it writes nothing and needs no site.
"""

import json
import pathlib

from smartprocess_logistics.core.scan import FIELDNAME
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "smartprocess_logistics"
SETTINGS_JSON = (
	APP_ROOT / "smartprocess_logistics" / "doctype" / "logistics_settings" / "logistics_settings.json"
)

LAYOUT = ("Tab Break", "Section Break", "Column Break")

# The tab every serial setting belongs in, and the two that must hold only their own kind.
SERIAL_TAB = "serial_tab"
SERIAL_TAB_LABEL = "تنظیمات سریال"

SERIAL_SETTINGS = (
	"enable_serial_registration",
	"installed_on",
	"serial_max_length",
	"normalization_version",
	FIELDNAME,
	"delimiters",
	"item_markers",
)

# What is left in the two other tabs: the file importer's own limits, and page sizes. Neither is a
# rule about a serial, which is why neither moved.
FILE_SETTINGS = ("max_import_rows", "max_import_file_kb", "max_import_sheets", "preview_ttl_minutes")
PAGE_SETTINGS = ("default_page_length", "max_page_length")


def settings_doctype():
	return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))


def field_of(document, fieldname):
	return next((field for field in document["fields"] if field["fieldname"] == fieldname), None)


def tab_of(document, fieldname):
	"""The tab a setting sits under, by walking the order the form lays out."""
	tab = None

	for name in document["field_order"]:
		field = field_of(document, name)

		if field and field["fieldtype"] == "Tab Break":
			tab = name

		if name == fieldname:
			return tab

	return None


class TestOneSerialTab(UnitTestCase):
	def test_the_serial_tab_exists_and_says_what_it_holds(self):
		tab = field_of(settings_doctype(), SERIAL_TAB)

		self.assertIsNotNone(tab, "the serial settings tab is missing")
		self.assertEqual(tab["fieldtype"], "Tab Break")
		self.assertEqual(tab["label"], SERIAL_TAB_LABEL)

	def test_every_serial_setting_is_in_it(self):
		document = settings_doctype()

		for fieldname in SERIAL_SETTINGS:
			with self.subTest(fieldname=fieldname):
				self.assertEqual(tab_of(document, fieldname), SERIAL_TAB)

	def test_it_is_the_first_tab_the_operator_sees(self):
		"""The tab an administrator opens to configure this app should be the serial one."""
		document = settings_doctype()
		tabs = [
			field["fieldname"]
			for name in document["field_order"]
			if (field := field_of(document, name)) and field["fieldtype"] == "Tab Break"
		]

		self.assertEqual(tabs[0], SERIAL_TAB, tabs)

	def test_the_other_settings_stayed_where_they_belong(self):
		document = settings_doctype()

		for fieldname in FILE_SETTINGS:
			with self.subTest(fieldname=fieldname):
				self.assertEqual(tab_of(document, fieldname), "import_tab")

		for fieldname in PAGE_SETTINGS:
			with self.subTest(fieldname=fieldname):
				self.assertEqual(tab_of(document, fieldname), "report_tab")

	def test_the_dead_setting_was_not_brought_back(self):
		"""`allow_over_quantity` was removed with the cap. A tab is not a place to hide it again."""
		document = settings_doctype()
		names = {name for name in document["field_order"]} | {
			field["fieldname"] for field in document["fields"]
		}

		self.assertNotIn("allow_over_quantity", names)


class TestTheNextRowCommandField(UnitTestCase):
	def test_it_is_a_required_text_field_with_the_shipped_default(self):
		field = field_of(settings_doctype(), FIELDNAME)

		self.assertIsNotNone(field, "the next-row command field is missing")
		self.assertEqual(field["fieldtype"], "Data")
		self.assertEqual(field["label"], "کاراکتر ردیف بعدی")
		self.assertEqual(field["default"], "**")
		self.assertTrue(field.get("reqd"), "an empty command is refused by core.scan; the form says so too")

	def test_it_says_what_it_does_and_what_it_never_does(self):
		"""The description is the only documentation an administrator gets in the Desk."""
		description = field_of(settings_doctype(), FIELDNAME).get("description") or ""

		self.assertIn("برابری کامل", description, "it must say the match is the whole value")
		self.assertIn("ذخیره نمی‌شود", description, "and that the command is never stored as a serial")

	def test_the_installed_on_description_no_longer_claims_to_gate_the_report(self):
		"""It did, until the report stopped reading it. That sentence outlived the rule it described."""
		description = field_of(settings_doctype(), "installed_on").get("description") or ""

		self.assertNotIn("بزرگ‌تر یا مساوی", description)
		self.assertIn("مبنای هیچ گزارشی نیست", description)


class TestTheLayoutIsCoherent(UnitTestCase):
	def test_every_field_is_placed_once_and_the_order_covers_them_all(self):
		document = settings_doctype()
		order = document["field_order"]
		names = [field["fieldname"] for field in document["fields"]]

		self.assertEqual(len(order), len(set(order)), "a field is listed twice in field_order")
		self.assertEqual(len(names), len(set(names)), "a fieldname is defined twice")
		self.assertEqual(
			set(order), set(names), "a field is defined but not placed, or placed but not defined"
		)

	def test_every_setting_has_a_section_above_it(self):
		"""A field with no section heading is a field nobody can find a second time."""
		document = settings_doctype()
		section = None

		for name in document["field_order"]:
			field = field_of(document, name)

			# A new tab starts with no section open, so a field dropped straight under a tab —
			# with no heading of its own — is what this catches.
			if field and field["fieldtype"] == "Tab Break":
				section = None

			if field and field["fieldtype"] == "Section Break":
				section = name

			if field and field["fieldtype"] in LAYOUT:
				continue

			with self.subTest(fieldname=name):
				self.assertIsNotNone(section, f"{name} is under no section")

	def test_the_definitions_are_valid(self):
		"""The rules that decide whether Frappe can install this DocType at all, re-asserted here.

		`tests/test_doctype_schemas.py` runs the same check over every DocType. It is repeated for
		this one because this file edits this one, and a layout change that broke the definition
		should fail beside the layout that broke it.
		"""
		from smartprocess_logistics.tests import schema_checks

		problems = [problem for problem in schema_checks.check_all() if "logistics_settings" in problem]

		self.assertEqual(problems, [])
