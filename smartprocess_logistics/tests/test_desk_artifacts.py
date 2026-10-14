"""The Desk artefacts this app ships, checked as files before anything is installed.

These are JSON documents Frappe imports on `bench install-app` and on every migrate: a
`Workspace`, a `Workspace Sidebar`, a `Desktop Icon`, a Script `Report` and an `Item`
customization. None of them is exercised by the application tests, and a mistake in one is
invisible until somebody opens the Desk and finds the navigation missing — which is exactly how
two of them were found.

So the rules below are read off the framework's own behaviour, and each one is a way the app has
been broken or could be:

- **A sidebar whose name is the module name.** `boot.get_sidebar_items` calls
  `auto_generate_sidebar_from_module`, which builds a sidebar for every module that has no
  sidebar **named after it**. The generated one carries the module's DocTypes with their
  English names, so leaving it to be generated showed a second entry beside the Persian one.
- **One sidebar carries the items, and the icon points at it.** A sidebar with no real item is
  skipped by `get_sidebar_items`, which is what makes the module-named one invisible rather than
  a duplicate.
- **Card breaks match the content blocks.** A `Workspace`'s `content` names its cards, and the
  `links` define them. When the two disagree the card renders empty with no error anywhere.
- **The report's file name is its document name.** `query_report.get_script` looks for
  `<module>/report/<scrub(name)>/<scrub(name)>.js`, so a report whose folder is spelled
  differently loads no filters and silently shows none.
- **The hooks point at files that exist.** `doctype_js` and `app_include_css` are paths from
  the app package; a typo is a 404 in the browser and a console message nobody reads.
"""

import json
import pathlib
import re

from smartprocess_logistics.tests.base import UnitTestCase

# `APP` is the app package — `hooks.py`, `api/`, `core/`, and the two app-level folder names
# Frappe reads from beside it (`desktop_icon`, `workspace_sidebar`). `MODULE` is the module
# inside it, which is where DocTypes, reports, workspaces and customizations live.
APP = pathlib.Path(__file__).resolve().parent.parent
MODULE = APP / "smartprocess_logistics"

SIDEBAR_DIR = APP / "workspace_sidebar"
ICON_DIR = APP / "desktop_icon"
WORKSPACE_DIR = MODULE / "workspace"
REPORT_DIR = MODULE / "report"


def read_json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def sidebar_files():
	return sorted(path for path in SIDEBAR_DIR.glob("*.json"))


def sidebars():
	return [(path, read_json(path)) for path in sidebar_files()]


def module_name():
	for line in (APP / "modules.txt").read_text(encoding="utf-8").splitlines():
		if line.strip():
			return line.strip()

	raise AssertionError("modules.txt holds no module name")


class TestWorkspaceSidebar(UnitTestCase):
	def test_exactly_one_sidebar_carries_the_navigation(self):
		carrying = [
			(doc["name"], [item for item in doc["items"] if item.get("type") != "Section Break"])
			for _path, doc in sidebars()
			if any(item.get("type") != "Section Break" for item in doc.get("items") or [])
		]

		self.assertEqual(len(carrying), 1, f"expected one sidebar with links, found {carrying}")
		self.assertEqual(len(carrying[0][1]), 6)

	def test_the_sidebar_that_carries_them_is_named_in_persian(self):
		for _path, doc in sidebars():
			if not any(item.get("type") != "Section Break" for item in doc.get("items") or []):
				continue

			with self.subTest(name=doc["name"]):
				self.assertEqual(doc["name"], doc["title"])
				self.assertTrue(
					any(ord(character) > 127 for character in doc["name"]),
					"the sidebar label is what the panel header shows, and it is not Persian",
				)

	def test_a_sidebar_named_after_the_module_exists_and_holds_nothing(self):
		# This is the whole reason the second file exists: `auto_generate_sidebar_from_module`
		# skips a module only when a sidebar with the module's own name is already there. With no
		# items it is skipped again by `get_sidebar_items`, so it is a guard rather than a second
		# entry, and deleting it brings the English duplicate back.
		named = [doc for _path, doc in sidebars() if doc["name"] == module_name()]

		self.assertEqual(len(named), 1, "no sidebar is named after the module")
		self.assertEqual(named[0].get("items") or [], [])

	def test_every_item_points_at_something_this_app_ships(self):
		workers = {"DocType", "Report", "Workspace"}
		expected = {
			"Logistics Serial",
			"Logistics Serial Assignment",
			"Logistics Import Session",
			"Logistics Settings",
			"Missing Sales Serials",
			"Logistics",
		}

		found = {
			item["link_to"]
			for _path, doc in sidebars()
			for item in doc.get("items") or []
			if item.get("link_type") in workers
		}

		self.assertEqual(found, expected)


class TestDesktopIcon(UnitTestCase):
	def test_the_icon_opens_the_sidebar_that_carries_the_links(self):
		carrying = [
			doc["name"]
			for _path, doc in sidebars()
			if any(item.get("type") != "Section Break" for item in doc.get("items") or [])
		]
		icons = [read_json(path) for path in sorted(ICON_DIR.glob("*.json"))]

		self.assertEqual(len(icons), 1)
		self.assertEqual(icons[0]["link_type"], "Workspace Sidebar")
		self.assertIn(icons[0]["link_to"], carrying)

	def test_the_label_and_the_name_are_the_same_string(self):
		# `Desktop Icon` names itself from `label` (`_sync_autoname_field` keeps the two equal),
		# so a file where they differ would be rewritten on import and the guess would win.
		for path in sorted(ICON_DIR.glob("*.json")):
			doc = read_json(path)

			with self.subTest(path=path.name):
				self.assertEqual(doc["name"], doc["label"])

	def test_the_icon_is_shown_to_the_apps_own_roles(self):
		doc = read_json(sorted(ICON_DIR.glob("*.json"))[0])
		roles = {row["role"] for row in doc.get("roles") or []}

		self.assertTrue(
			{"Logistics Serial Viewer", "Logistics Serial Editor", "Logistics Serial Manager"} <= roles
		)


class TestWorkspace(UnitTestCase):
	def workspace(self):
		paths = sorted(WORKSPACE_DIR.glob("*/*.json"))
		self.assertEqual(len(paths), 1)
		path = paths[0]

		return path, read_json(path)

	def test_the_cards_the_content_names_are_the_cards_the_links_define(self):
		_path, doc = self.workspace()

		named = [
			block["data"]["card_name"] for block in json.loads(doc["content"]) if block["type"] == "card"
		]
		defined = [link["label"] for link in doc["links"] if link["type"] == "Card Break"]

		self.assertEqual(named, defined)

	def test_every_card_holds_at_least_one_link(self):
		_path, doc = self.workspace()

		breaks = [link for link in doc["links"] if link["type"] == "Card Break"]

		for index, card in enumerate(breaks):
			following = doc["links"][doc["links"].index(card) + 1 :]
			links_in_card = []

			for link in following:
				if link["type"] == "Card Break":
					break
				links_in_card.append(link)

			with self.subTest(card=card["label"]):
				self.assertTrue(links_in_card, f"card {card['label']} is empty")
				self.assertEqual(card.get("link_count"), len(links_in_card))

	def test_the_workspace_is_public_and_titled_in_persian(self):
		_path, doc = self.workspace()

		self.assertEqual(doc["public"], 1)
		self.assertTrue(any(ord(character) > 127 for character in doc["title"]))


class TestReport(UnitTestCase):
	def report_files(self):
		paths = sorted(REPORT_DIR.glob("*/*.json"))
		self.assertEqual(len(paths), 1, "expected exactly one report")

		return paths[0], read_json(paths[0])

	def test_the_folder_and_the_files_are_named_after_the_report(self):
		path, doc = self.report_files()
		slug = doc["name"].replace(" ", "_").lower()

		self.assertEqual(path.parent.name, slug)
		self.assertEqual(path.stem, slug)
		self.assertTrue((path.parent / f"{slug}.py").exists(), "the report has no execute()")
		self.assertTrue((path.parent / f"{slug}.js").exists(), "the report has no filters")

	def test_it_is_a_standard_script_report_over_the_invoice(self):
		_path, doc = self.report_files()

		self.assertEqual(doc["is_standard"], "Yes")
		self.assertEqual(doc["report_type"], "Script Report")
		self.assertEqual(doc["ref_doctype"], "Sales Invoice")

	def test_the_apps_own_roles_may_run_it(self):
		_path, doc = self.report_files()
		roles = {row["role"] for row in doc.get("roles") or []}

		self.assertTrue({"Logistics Serial Viewer", "Logistics Serial Editor"} <= roles)


class TestHooksPointAtRealFiles(UnitTestCase):
	def test_the_files_the_desk_loads_exist(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")

		# `doctype_js` is a path from the app package; `app_include_css` is the URL the browser
		# asks for, which `/assets/<app>/` maps onto the package's `public` directory.
		for hook_value, on_disk in (
			("public/js/sales_invoice_serial_button.js", APP / "public/js/sales_invoice_serial_button.js"),
			("public/js/logistics_settings_transfer.js", APP / "public/js/logistics_settings_transfer.js"),
			("/assets/smartprocess_logistics/css/logistics.css", APP / "public/css/logistics.css"),
		):
			with self.subTest(file=hook_value):
				self.assertIn(hook_value, hooks)
				self.assertTrue(on_disk.exists(), f"{on_disk} does not exist")

	def test_the_item_customization_names_the_flag_the_report_filters_on(self):
		path = MODULE / "custom" / "item.json"
		doc = read_json(path)
		fields = {field["fieldname"]: field for field in doc["custom_fields"]}

		self.assertEqual(doc["sync_on_migrate"], 1)
		self.assertIn("sp_serial_required", fields)
		self.assertEqual(fields["sp_serial_required"]["fieldtype"], "Check")
		self.assertTrue(fields["sp_serial_required"]["in_standard_filter"])


# Words that are not translated because they are not language: a format, a file suffix, an
# identifier an operator reads off a screen and types back.
ENGLISH_THAT_IS_NOT_LANGUAGE = frozenset({"XLSX", "TXT", "CSV"})

ASCII_ONLY = re.compile(r"^[A-Za-z0-9 ,.;:/()\-_'\"%{}]+$")


def _is_english(text):
	value = text.strip()

	return bool(value) and value not in ENGLISH_THAT_IS_NOT_LANGUAGE and bool(ASCII_ONLY.match(value))


def _translated_strings_in_source(path):
	"""Every literal passed to `_()` or `__()` in a Python or JavaScript file."""
	for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
		for match in re.finditer(r'(?<![A-Za-z0-9_])(?:__|_lt|_x|_n|_)\(\s*"([^"]{3,})"', line):
			yield number, match.group(1)


class TestEverythingOnScreenIsPersian(UnitTestCase):
	"""The operator this app is for reads Persian. Every label, title and message ships in it.

	This is a release check rather than a style rule: six strings — the titles of the refusals
	the install and the constraint guard raise, and the app description shown in the app list —
	had been written in English and left there, because a title is the part of a message nobody
	reads until something goes wrong. A string that is only ASCII is either a name or a gap, and
	the names are listed above rather than guessed at.
	"""

	def test_no_doctype_label_or_description_is_english(self):
		for path in sorted((MODULE / "doctype").glob("*/*.json")):
			doc = read_json(path)

			for field in doc.get("fields") or []:
				for key in ("label", "description"):
					value = field.get(key)

					if not isinstance(value, str) or not _is_english(value):
						continue

					with self.subTest(doctype=doc["name"], field=field.get("fieldname"), key=key):
						self.fail(f"{value!r} ships untranslated")

	def test_no_message_or_title_in_the_app_is_english(self):
		for path in sorted(APP.rglob("*.py")) + sorted(APP.rglob("*.js")):
			if "tests" in path.parts or "__pycache__" in path.parts:
				continue

			for number, literal in _translated_strings_in_source(path):
				if not _is_english(literal):
					continue

				with self.subTest(file=path.name, line=number):
					self.fail(f"{path.name}:{number} ships {literal!r} untranslated")

	def test_the_scan_finds_the_persian_strings_it_is_meant_to_check(self):
		# A scan that matches nothing passes every assertion above, so this pins that it reads
		# the files it claims to.
		found = 0

		for path in sorted(APP.rglob("*.js")):
			if "__pycache__" in path.parts:
				continue

			found += len(list(_translated_strings_in_source(path)))

		self.assertGreater(found, 20)
