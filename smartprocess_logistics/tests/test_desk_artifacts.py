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
- **An app-level file is named after its document.** `remove_orphan_entities` finds the
  `Workspace Sidebar` and `Desktop Icon` this app ships by looking for `<scrub(name)>.json` and
  deletes the record when the file is missing — so a Latin file name under a Persian document
  name loses the navigation on the next migrate.
- **The hooks point at files that exist.** `doctype_js` and `app_include_css` are paths from
  the app package; a typo is a 404 in the browser and a console message nobody reads.
"""

import json
import pathlib
import re

from smartprocess_logistics.core import transfer
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


def hook_sources():
	"""The hook files that carry the registration a Desk test is looking for.

	`hooks.py` holds the approved composition call; `hooks_composition.py` names the approved
	scripts; `hooks_commitment.py` names the development-only committed-stock script. The
	release subset has the first two and not the third, so these checks work in both trees.
	"""
	names = ("hooks.py", "hooks_composition.py", "hooks_commitment.py")
	return "\n".join((APP / name).read_text(encoding="utf-8") for name in names if (APP / name).exists())


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
	def test_each_workspace_sidebar_carries_its_own_navigation(self):
		carrying = [
			(doc["name"], [item for item in doc["items"] if item.get("type") != "Section Break"])
			for _path, doc in sidebars()
			if any(item.get("type") != "Section Break" for item in doc.get("items") or [])
		]

		self.assertEqual(len(carrying), 2, f"expected the two workspace sidebars, found {carrying}")
		self.assertEqual(carrying[0][0], "انبار و خرید")
		self.assertEqual(len(carrying[0][1]), 14)
		self.assertEqual(carrying[1][0], "سریال لجستیک")
		self.assertEqual(len(carrying[1][1]), 6)

	def test_the_operational_report_leads_the_reports_section(self):
		_path, doc = next(
			(path, doc) for path, doc in sidebars() if doc["name"] == "انبار و خرید"
		)
		reports = [
			item["link_to"]
			for item in doc["items"]
			if item.get("link_type") == "Report"
		]

		self.assertEqual(reports[0], "تراز موجودی")

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
			"Warehouse and Purchasing",
			"Warehouse Inventory",
			"Sales Orders Ready for Invoicing",
			"Item Weight and Volume",
			"Purchased Not Received",
			"تراز موجودی",
			"Purchase Order",
			"Purchase Receipt",
			"Purchase Invoice",
			"Item",
			"Warehouse",
			"Stock Entry",
			"Sales Order",
			"Sales Invoice",
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

		self.assertEqual(len(icons), 2)
		for icon in icons:
			with self.subTest(icon=icon["name"]):
				self.assertEqual(icon["link_type"], "Workspace Sidebar")
				self.assertIn(icon["link_to"], carrying)

	def test_the_label_and_the_name_are_the_same_string(self):
		# `Desktop Icon` names itself from `label` (`_sync_autoname_field` keeps the two equal),
		# so a file where they differ would be rewritten on import and the guess would win.
		for path in sorted(ICON_DIR.glob("*.json")):
			doc = read_json(path)

			with self.subTest(path=path.name):
				self.assertEqual(doc["name"], doc["label"])

	def test_the_icons_are_shown_to_the_apps_own_roles(self):
		docs = {doc["name"]: doc for doc in (read_json(path) for path in sorted(ICON_DIR.glob("*.json")))}

		serial_roles = {row["role"] for row in docs["سریال لجستیک"].get("roles") or []}
		logistics_roles = {row["role"] for row in docs["لجستیک"].get("roles") or []}

		self.assertTrue(
			{"Logistics Serial Viewer", "Logistics Serial Editor", "Logistics Serial Manager"}
			<= serial_roles
		)
		self.assertTrue({"Stock User", "Purchase User", "Sales User", "System Manager"} <= logistics_roles)


class TestAppsScreenEntry(UnitTestCase):
	"""v16 opens apps through add_to_apps_screen and the app's Dock, not a second sidebar."""

	def test_the_app_declares_its_apps_screen_entry(self):
		source = (APP / "hooks.py").read_text(encoding="utf-8")

		self.assertIn("add_to_apps_screen", source)
		self.assertIn('"name": app_name', source)
		self.assertIn('"route": app_home', source)

	def test_the_dock_opens_the_app_sidebar(self):
		path = APP / "dock" / "smartprocess_logistics" / "smartprocess_logistics.json"
		doc = read_json(path)

		self.assertEqual(doc["app"], "smartprocess_logistics")
		self.assertEqual(doc["name"], "smartprocess_logistics")
		self.assertEqual(doc["standard"], 1)
		self.assertEqual(
			(doc["items"][0]["link_type"], doc["items"][0]["link_to"]),
			("Sidebar", "SmartProcess Logistics"),
		)


class TestTheAppLevelFilesAreNamedAfterTheirDocuments(UnitTestCase):
	"""The file name is the only thing `migrate` uses to tell a shipped artefact from an orphan.

	`frappe.model.sync.remove_orphan_entities` asks `check_if_record_exists(type="app", ...)`,
	which builds one path — `<app>/<folder>/<scrub(name)>.json` — and deletes the record when that
	path is missing. It never searches the folder, so a file whose name disagrees with its own
	document is imported on install and **deleted on the first migrate**, taking the navigation
	with it. Both `Workspace Sidebar` and `Desktop Icon` are checked by `name`; `Desktop Icon`
	exports itself as `scrub(label)`, and `label == name` is asserted above, so one rule covers
	both. This is not hypothetical: the Persian-named sidebar and icon were shipped as
	`logistics.json` and were removed by every migrate until this test was written.
	"""

	def scrub(self, text):
		# `frappe.scrub`, which is the whole of it: spaces and hyphens become underscores.
		return text.replace(" ", "_").replace("-", "_").lower()

	def test_every_app_level_file_is_named_after_the_document_it_holds(self):
		folders = {"workspace_sidebar": SIDEBAR_DIR, "desktop_icon": ICON_DIR}

		seen = 0

		for folder, directory in folders.items():
			for path in sorted(directory.glob("*.json")):
				doc = read_json(path)
				name = doc.get("name") or doc.get("label")

				with self.subTest(folder=folder, path=path.name):
					self.assertEqual(path.stem, self.scrub(name), f"migrate would delete {name!r}")
					seen += 1

		self.assertEqual(seen, 5, "the five files this app ships: three sidebars and two icons")


class TestWorkspace(UnitTestCase):
	def workspace(self, name="Logistics"):
		paths = sorted(WORKSPACE_DIR.glob("*/*.json"))
		found = [(path, read_json(path)) for path in paths if read_json(path).get("name") == name]
		self.assertEqual(len(found), 1, f"expected exactly one workspace named {name!r}")

		return found[0]

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

		for card in breaks:
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


class TestWarehouseWorkspace(TestWorkspace):
	def workspace(self, name="Warehouse and Purchasing"):
		return super().workspace(name)

	def test_it_links_the_approved_reports(self):
		_path, doc = self.workspace()

		reports = {link["link_to"] for link in doc["links"] if link.get("link_type") == "Report"}

		self.assertEqual(
			reports,
			{
				"Warehouse Inventory",
				"Sales Orders Ready for Invoicing",
				"Item Weight and Volume",
				"Purchased Not Received",
				"تراز موجودی",
			},
		)

	def test_the_operational_report_is_the_first_report_link(self):
		_path, doc = self.workspace()

		reports = [link["link_to"] for link in doc["links"] if link.get("link_type") == "Report"]

		self.assertEqual(reports[0], "تراز موجودی")
		self.assertEqual(doc["links"][0]["link_count"], len(reports))


class TestReport(UnitTestCase):
	def report_files(self, name="Missing Sales Serials"):
		paths = sorted(REPORT_DIR.glob("*/*.json"))
		found = [(path, read_json(path)) for path in paths if read_json(path).get("name") == name]
		self.assertEqual(len(found), 1, f"expected exactly one report named {name!r}")

		return found[0]

	def all_reports(self):
		return [(path, read_json(path)) for path in sorted(REPORT_DIR.glob("*/*.json"))]

	def test_every_report_is_a_standard_script_report(self):
		for path, doc in self.all_reports():
			with self.subTest(report=doc.get("name")):
				self.assertEqual(doc["is_standard"], "Yes")
				self.assertEqual(doc["report_type"], "Script Report")
				self.assertTrue(doc.get("roles"))
				slug = doc["name"].replace(" ", "_").lower()
				self.assertEqual(path.parent.name, slug)
				self.assertEqual(path.stem, slug)
				self.assertTrue((path.parent / f"{slug}.py").exists())
				self.assertTrue((path.parent / f"{slug}.js").exists())

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
		hooks = hook_sources()

		# `doctype_js` is a path from the app package; `app_include_css` is the URL the browser
		# asks for, which `/assets/<app>/` maps onto the package's `public` directory.
		for hook_value, on_disk in (
			("public/js/sales_invoice_serial_button.js", APP / "public/js/sales_invoice_serial_button.js"),
			(
				"public/js/sales_invoice_serial_transfer.js",
				APP / "public/js/sales_invoice_serial_transfer.js",
			),
			("public/js/logistics_settings_transfer.js", APP / "public/js/logistics_settings_transfer.js"),
			("/assets/smartprocess_logistics/css/logistics.css", APP / "public/css/logistics.css"),
		):
			with self.subTest(file=hook_value):
				self.assertIn(hook_value, hooks)
				self.assertTrue(on_disk.exists(), f"{on_disk} does not exist")


class TestTheTransferWindow(UnitTestCase):
	"""The second toolbar button: a review of two documents, in the source's own order.

	The window draws what `core.transfer.preview_of` returned and imposes no order of its own. It
	was written the other way first — four separately-grouped lists from the server, regrouped in
	the browser — and the table came out grouped by outcome, which is the one order an operator
	cannot hold against the invoice they cancelled. `deployment/lab/verify_transfer_logic.js`
	drives the drawing; what is pinned here is the shape of the source: where its data comes from,
	where the vocabulary lives, and that it does not branch on a reason code.
	"""

	def source(self):
		return (APP / "public" / "js" / "sales_invoice_serial_transfer.js").read_text(encoding="utf-8")

	def test_both_sales_invoice_scripts_are_registered_as_a_list(self):
		"""Frappe accepts a list here, and it reads each path: `get_code_files_via_hooks` wraps a
		single path in one. Registering the second script as a second dict key would replace the
		first, because a dict has no second key of the same name."""
		hooks = hook_sources()

		self.assertIn('"Sales Invoice": [', hooks)

		for name in ("sales_invoice_serial_button.js", "sales_invoice_serial_transfer.js"):
			with self.subTest(script=name):
				self.assertIn(name, hooks)

	def test_it_adds_one_button_and_no_group(self):
		"""One press, one window, and no menu in between — the same rule the serial button follows."""
		source = self.source()
		match = re.search(r"add_custom_button\((.*?)\);", source, re.DOTALL)

		self.assertIsNotNone(match, "the button is no longer added at all")
		self.assertEqual(source.count("add_custom_button("), 1)
		self.assertEqual(match.group(1).count('__("'), 1, f"a label and no group: {match.group(1)!r}")
		self.assertIn("open_transfer_dialog(frm)", match.group(1))

	def test_it_draws_the_servers_list_and_regroups_nothing(self):
		source = self.source()

		self.assertIn("preview.serials", source)

		for key in ("preview.moved", "preview.warnings", "preview.blocked", "preview.unselected"):
			with self.subTest(key=key):
				self.assertNotIn(key, source)

	def test_it_branches_on_the_servers_flag_and_not_on_a_reason_code(self):
		"""`answerable` is the server saying an answer would change this row's outcome. The window
		reading the code instead would be a second place that knows which refusals are questions."""
		source = self.source()

		self.assertIn("row.answerable", source)
		self.assertNotIn('=== "ACKNOWLEDGEMENT_MISSING"', source)

	def test_every_reason_the_record_can_hold_has_a_label_in_the_window(self):
		"""The one place the two languages meet, and the only way to see them drift apart."""
		source = self.source()

		for reason in transfer.RECORDABLE_REASONS:
			with self.subTest(reason=reason):
				self.assertIn(f"{reason}:", source)

	def test_the_window_names_no_serial_identity(self):
		"""The identity is derived on the server; a window that named one would be deciding it."""
		self.assertNotIn("normalized", self.source())

	def test_it_reads_the_room_the_server_computed(self):
		"""`room` is sent so the window does not repeat the capacity rule to fill a select."""
		source = self.source()

		self.assertIn("row.room", source)
		self.assertNotIn("capacity -", source)

	def test_the_settings_script_is_one_of_the_files_the_desk_loads(self):
		hooks = hook_sources()

		self.assertIn("public/js/logistics_settings_transfer.js", hooks)

	def test_every_script_the_app_ships_is_named_in_a_hook(self):
		"""A script nothing loads is a script that was written and never wired."""
		hooks = hook_sources()

		for path in sorted((APP / "public" / "js").glob("*.js")):
			with self.subTest(script=path.name):
				self.assertIn(path.name, hooks)


class TestTheTwoRestores(UnitTestCase):
	"""One flow, two acts, and the differences between them written down in one place.

	The historical restore writes to cancelled invoices — the one thing every other write in this
	app refuses — so it is a button of its own and not a mode the ordinary one falls into. What is
	pinned here is that the *only* difference between them is the table at the top of the file:
	two endpoint names, a label, and a warning. A second copy of the flow would be a second place
	for the warnings to drift.

	The behavioural half — that the archivist sees three buttons and the Manager two, that the
	historical preview carries the warning, and that confirming calls the historical endpoint with
	the preview's own token — is driven in a browser by `deployment/lab/preview_settings_restore.html`,
	because a source text can say which endpoint a call names and only a runtime can say that it
	is the one that ran.
	"""

	def source(self):
		return (APP / "public" / "js" / "logistics_settings_transfer.js").read_text(encoding="utf-8")

	def test_there_is_one_flow_and_a_table_of_two_modes(self):
		source = self.source()

		self.assertEqual(source.count("const RESTORES = {"), 1)
		self.assertEqual(source.count("preview_endpoint:"), 3)
		self.assertEqual(source.count("apply_endpoint:"), 3)

	def test_the_modes_name_the_four_endpoints(self):
		source = self.source()

		for endpoint in (
			"preview_serial_restore",
			"apply_serial_restore",
			"preview_cancelled_serial_history",
			"restore_cancelled_serial_history",
			"preview_restore_audit_records",
			"restore_audit_records",
		):
			with self.subTest(endpoint=endpoint):
				self.assertIn(endpoint, source)

	def test_the_records_mode_carries_its_own_warning(self):
		"""The other act that needs one: it makes documents that record acts performed elsewhere."""
		source = self.source()
		start = source.index("records: {")
		mode = source[start : source.index("history: {", start)]

		warning = mode[mode.index("warning: __(") : mode.index("),", mode.index("warning: __("))]

		self.assertIn("هشدار", warning)
		self.assertIn("بازآوری‌شده", warning)

	def test_every_mode_that_writes_to_cancelled_invoices_is_separately_labelled(self):
		source = self.source()

		self.assertIn("بازآوری تاریخچهٔ فاکتورهای لغو‌شده", source)
		self.assertIn("بازآوری سابقه‌های انتقال", source)
		self.assertNotEqual(source.index("history: {"), source.index("records: {"), "two modes, two entries")

	def test_the_historical_warning_says_cancelled(self):
		"""That mode's own warning text, and not the first one in the file.

		Two assertions narrowed this test twice. It began over the whole mode's slice, which
		contains the button's label and the comments around it — both naming cancelled invoices —
		so a warning shortened to "this returns registrations" passed. Then it read the first
		`warning: __(` in the file, which after the third mode was added was no longer this one.
		"""
		source = self.source()
		start = source.index("history: {")
		mode = source[start : source.index("function is_manager")]
		warning = mode[mode.index("warning: __(") : mode.index("),", mode.index("warning: __("))]

		self.assertIn("هشدار", warning)
		self.assertIn("لغو‌شده", warning)

	def test_the_ordinary_mode_has_no_warning_of_its_own(self):
		source = self.source()
		live = source[source.index("live: {") : source.index("history: {")]

		self.assertIn('warning: ""', live)

	def test_only_the_archivist_sees_the_historical_button(self):
		source = self.source()
		hook = source[source.index('frappe.ui.form.on("Logistics Settings"') : source.index("const RESTORES")]

		self.assertIn("is_archivist()", hook)
		self.assertIn("Logistics Serial Archivist", source)
		self.assertLess(
			hook.index("is_archivist()"),
			hook.index("بازآوری تاریخچهٔ فاکتورهای لغو‌شده"),
			"the button is drawn inside the role check, not beside it",
		)

	def test_it_decides_no_policy_and_writes_through_the_endpoints(self):
		source = self.source()

		self.assertNotIn("CANCELLED_ONLY", source)
		self.assertNotIn("CANCELLED_REFUSED", source)
		self.assertNotIn("XMLHttpRequest", source)

	def test_confirming_always_sends_the_token(self):
		"""The token is what ties the write to the file the operator actually saw."""
		source = self.source()
		apply_body = source[source.index("function apply(token, file, restore)") :]

		self.assertIn("args: { content: file.content, token: token }", apply_body)
		self.assertIn("if (!token)", apply_body)

	def test_the_result_is_reported_under_the_act_that_was_performed(self):
		source = self.source()
		apply_body = source[source.index("function apply(token, file, restore)") :]

		self.assertIn("title: restore.title", apply_body)

	def test_it_says_the_transfer_records_are_not_restored(self):
		"""A file from a version 2 export carries them, and neither restore places one."""
		source = self.source()

		self.assertIn("records_in_file", source)

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


class TestTheSerialToolbar(UnitTestCase):
	"""The one serial button on a Sales Invoice: one press, one window, and nothing behind a menu.

	The toolbar used to carry a «سریال‌ها» dropdown holding «ثبت سریال» beside «جزئیات سریال‌ها»,
	which made the operator open a menu to reach the window they always want. The rule now is one
	plain button whose own label is what they press, so what this pins is the shape of the call
	that draws it: a single `add_custom_button`, two arguments rather than three, the window's own
	opening function as the action, and no second entry to reappear.

	The behavioural half of the same rule — that the button opens the real dialog, and that a
	cancelled invoice gets none — is driven in a browser by `deployment/lab/preview_scan_header.html`,
	because a source text can say what a call looks like and only a runtime can say what it does.
	"""

	def source(self):
		return (APP / "public" / "js" / "sales_invoice_serial_button.js").read_text(encoding="utf-8")

	def test_one_button_is_added_and_it_is_the_window_itself(self):
		source = self.source()

		self.assertEqual(source.count("add_custom_button("), 1)
		self.assertIn('add_custom_button(__("سریال‌ها"),', source)
		self.assertIn("smartprocess_logistics.open_serial_dialog(frm)", source)

	def test_the_button_is_not_a_group(self):
		"""A third argument makes a dropdown; the operator's one press must not need a second.

		Both arguments are here and there is no third: one translated label for the button and one
		action for it. A group would be a second translated string in the same call, which is what
		the count below is about.
		"""
		match = re.search(r"add_custom_button\((.*?)\);", self.source(), re.DOTALL)

		self.assertIsNotNone(match, "the button is no longer added at all")

		call = match.group(1)

		self.assertEqual(call.count('__("'), 1, f"the call carries a label and no group: {call!r}")
		self.assertEqual(call.count("=>"), 1, "and exactly one action")

	def test_the_details_entry_is_gone_and_its_window_is_not(self):
		"""The label is gone from the app; the window it opened is still here, unreachable."""
		source = self.source()

		self.assertNotIn('__("جزئیات سریال‌ها")', source)
		self.assertEqual(source.count("open_details_dialog = function"), 1)

	def test_the_window_is_opened_by_the_one_function_that_defines_it(self):
		"""No second implementation: the button calls the function the dialog has always had."""
		source = self.source()

		self.assertEqual(source.count("open_serial_dialog = function"), 1)

	def test_the_button_is_still_gated_on_the_editor_role(self):
		source = self.source()
		hook = source.split('frappe.ui.form.on("Sales Invoice"', 1)[1].split("\n});", 1)[0]

		self.assertIn("docstatus === 2", hook)
		self.assertIn("smartprocess_logistics.allowed(smartprocess_logistics.ROLE.editor)", hook)
		self.assertLess(
			hook.index("ROLE.editor"),
			hook.index("add_custom_button("),
			"the button is added inside the role check, not beside it",
		)

	def test_the_delivery_choice_is_read_from_the_list_and_not_from_its_box(self):
		"""The box around the list carries the same class, and jQuery fires a delegated handler
		once for every matching element on the bubble path.

		With a bare `.sp-scan-delivery`, the handler ran twice: once for the `<select>` with the
		operator's note, and once for the `<div>` around it, where `$(this).val()` is empty. The
		second run took the empty value as a refusal and deleted the note the first run had just
		stored, so the row stayed ambiguous and its save was blocked — the defect the browser
		harness caught. Qualifying the selector with `select.` is what keeps it to one run.
		"""
		source = self.source()

		self.assertIn('<div class="sp-scan-delivery${delivery_state_class(facts.state)}">', source)
		self.assertIn('on("change", "select.sp-scan-delivery"', source)
		self.assertNotIn('on("change", ".sp-scan-delivery"', source)


class TestTheCountersFollowTheSave(UnitTestCase):
	"""A save's answer already carries the invoice's new totals; the chips must show them at once.

	`add_row_serials` returns `summary` beside the row, and the station stored it — but only
	`load_status` redrew the chips, so after «ذخیره سریال‌ها» the counters kept the numbers of
	the previous save until the operator pressed «به‌روزرسانی» or reopened the window. The rule
	pinned here is the one the fix put in place: the counters are drawn in one place,
	`render_summary`, and both the refresh and the save call it.
	"""

	def source(self):
		return (APP / "public" / "js" / "sales_invoice_serial_button.js").read_text(encoding="utf-8")

	def test_the_counters_are_drawn_in_one_place(self):
		source = self.source()

		self.assertEqual(source.count("const render_summary = function"), 1)
		self.assertEqual(
			source.count('.sp-logistics-summary").replaceWith('),
			1,
			"the summary is replaced in the helper only; a second call site drifts",
		)

	def test_the_save_redraws_the_counters_before_it_redraws_the_row(self):
		source = self.source()
		marker = "state.status.summary = answer.summary || state.status.summary;"

		self.assertEqual(source.count(marker), 1)

		after = source.split(marker, 1)[1]

		self.assertLess(
			after.index("render_summary();"),
			after.index("drop_pending_many("),
			"the save draws the new totals in its own success path",
		)


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
