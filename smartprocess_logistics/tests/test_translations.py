"""The Persian module label: one catalogue, one entry, and the title it translates.

Frappe v16 compiles `locale/<lang>.po` into the catalogue the Desk reads; it does not read
`translations/*.csv` at runtime. These tests hold the shipped catalogue to the one message the
app needs, and hold that message to the title the sidebar actually carries.
"""

import ast
import gettext
import json
import pathlib

from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
MODULE = APP / "smartprocess_logistics"
LOCALE = APP / "locale"
CATALOGUE = LOCALE / "fa.po"
COMPILED = LOCALE / "fa" / "LC_MESSAGES" / "smartprocess_logistics.mo"
SIDEBAR = MODULE / "sidebar" / "smartprocess_logistics" / "smartprocess_logistics.json"
CSV = APP / "translations" / "fa.csv"

PERSIAN = "لجستیک پردازش هوشمند"


def unquote(value):
	return ast.literal_eval(value.strip())


def translated_entries(path):
	"""The `msgid`/`msgstr` pairs of a PO file, without a gettext dependency.

	The header is skipped: its `msgid` is empty and its `msgstr` holds the catalogue metadata.
	"""
	entries = {}
	msgid = None
	msgstr = ""
	for line in path.read_text(encoding="utf-8").splitlines():
		if not line or line.startswith("#"):
			continue
		if line.startswith("msgid "):
			if msgid:
				entries[msgid] = msgstr
			msgid = unquote(line[len("msgid ") :])
			msgstr = ""
		elif line.startswith("msgstr "):
			msgstr = unquote(line[len("msgstr ") :])
	if msgid:
		entries[msgid] = msgstr
	return entries


class TestPersianModuleLabel(UnitTestCase):
	def test_the_catalogue_ships_with_the_app(self):
		self.assertTrue(CATALOGUE.is_file())

	def test_the_catalogue_is_persian_and_utf8(self):
		header = CATALOGUE.read_text(encoding="utf-8")

		self.assertIn('"Language: fa\\n"', header)
		self.assertIn('"Content-Transfer-Encoding: 8bit\\n"', header)
		self.assertIn('"Content-Type: text/plain; charset=UTF-8\\n"', header)

	def test_the_catalogue_holds_one_entry_and_it_is_the_module_label(self):
		entries = translated_entries(CATALOGUE)

		self.assertEqual(entries, {"SmartProcess Logistics": PERSIAN})

	def test_the_translated_title_is_the_one_the_sidebar_ships(self):
		sidebar = json.loads(SIDEBAR.read_text(encoding="utf-8"))

		self.assertEqual(sidebar["app"], "smartprocess_logistics")
		self.assertEqual(sidebar["title"], "SmartProcess Logistics")
		self.assertIn(sidebar["title"], translated_entries(CATALOGUE))

	def test_the_label_is_persian_and_not_the_english_fallback(self):
		msgstr = translated_entries(CATALOGUE)["SmartProcess Logistics"]

		self.assertEqual(msgstr, PERSIAN)
		self.assertNotEqual(msgstr, "SmartProcess Logistics")

	def test_the_csv_brand_row_agrees_with_the_catalogue(self):
		rows = [row for row in (line.split(",") for line in CSV.read_text(encoding="utf-8").splitlines())]
		brand = [row for row in rows if row and row[0] == "SmartProcess Logistics"]

		self.assertEqual(len(brand), 1)
		self.assertEqual(brand[0][1], PERSIAN)

	def test_no_other_catalogue_could_translate_a_message_another_app_owns(self):
		self.assertEqual([path.name for path in LOCALE.glob("*.po")], ["fa.po"])

	def test_the_shipped_catalogue_is_the_compiled_po(self):
		"""The `.mo` the deploy mounts must say what the `.po` says.

		It is shipped rather than compiled at deploy time because this stack's `sites/assets`
		resolves into image content, so a catalogue compiled inside the container is erased by
		the next recreate. That makes the `.mo` a source file, and this test the thing that
		keeps it honest when the `.po` changes.
		"""
		self.assertTrue(COMPILED.is_file())

		with COMPILED.open("rb") as handle:
			compiled = gettext.GNUTranslations(handle)
		shipped = {
			msgid: compiled.gettext(msgid) for msgid in translated_entries(CATALOGUE)
		}

		self.assertEqual(shipped, translated_entries(CATALOGUE))
		self.assertEqual(shipped, {"SmartProcess Logistics": PERSIAN})

	def test_a_message_outside_the_catalogue_is_left_alone(self):
		with COMPILED.open("rb") as handle:
			compiled = gettext.GNUTranslations(handle)

			self.assertEqual(compiled.gettext("Purchase Invoice"), "Purchase Invoice")
