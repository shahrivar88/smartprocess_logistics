"""The inter-app registry: the static lists, and what they are joined with.

The registry is only worth having if it cannot drift from the app. These tests hold it to the
hooks, the fixture files, the Custom Field files and the installer that actually ship, and hold
`rows()` to saying "applied" only when the facts say so.
"""

import json
import pathlib
import re

from smartprocess_logistics import hooks_composition
from smartprocess_logistics.core import interop as registry
from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
MODULE = APP / "smartprocess_logistics"

TITLE = "وابستگی‌ها و تغییرات بین‌ماژولی"


def read_json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def check_kinds(changes):
	return [change.check[0] for change in changes if change.check]


def field_checks():
	return {
		change.check[1]: list(change.check[2])
		for change in registry.APPLIED_CHANGES
		if change.check and change.check[0] == registry.CHECK_FIELDS
	}


def record_checks():
	return {
		(change.check[1], change.check[2])
		for change in registry.APPLIED_CHANGES
		if change.check and change.check[0] == registry.CHECK_RECORD
	}


def all_facts_present():
	"""Facts of a site where everything the registry declares has been applied."""
	installed = {dependency.app: "0.0.0" for dependency in registry.DEPENDENCIES}
	records = {}
	fields = {}
	for change in registry.APPLIED_CHANGES:
		if not change.check:
			continue
		if change.check[0] == registry.CHECK_RECORD:
			records[(change.check[1], change.check[2])] = True
		elif change.check[0] == registry.CHECK_RECORDS:
			records.update({(change.check[1], name): True for name in change.check[2]})
		elif change.check[0] == registry.CHECK_FIELDS:
			fields[change.check[1]] = frozenset(change.check[2])
	return {"installed": installed, "records": records, "fields": fields}


class TestRegistryShape(UnitTestCase):
	def test_every_entry_states_every_fact_a_reader_needs(self):
		for entry in registry.DEPENDENCIES:
			with self.subTest(app=entry.app):
				for value in (
					entry.component,
					entry.purpose,
					entry.constraint,
					entry.configuration,
					entry.deployment,
					entry.verification,
					entry.removal,
				):
					self.assertTrue(value.strip())

		for change in registry.APPLIED_CHANGES:
			with self.subTest(target=change.target):
				for value in (
					change.target,
					change.purpose,
					change.mechanism,
					change.configuration,
					change.deployment,
					change.verification,
					change.removal,
				):
					self.assertTrue(value.strip())

	def test_a_dependency_declares_that_it_is_required_or_optional(self):
		required = {dependency.app for dependency in registry.DEPENDENCIES if dependency.required}
		optional = {dependency.app for dependency in registry.DEPENDENCIES if not dependency.required}

		self.assertEqual(required, {"frappe", "erpnext"})
		self.assertEqual(optional, {"smartprocess_erpnext_accounting"})

	def test_every_check_kind_is_one_the_core_answers(self):
		known = {registry.CHECK_RECORD, registry.CHECK_RECORDS, registry.CHECK_FIELDS}

		for kind in check_kinds(registry.APPLIED_CHANGES):
			with self.subTest(kind=kind):
				self.assertIn(kind, known)

	def test_the_required_checks_are_exactly_the_checks_the_changes_carry(self):
		expected = tuple(change.check for change in registry.APPLIED_CHANGES if change.check)

		self.assertEqual(registry.required_checks(), expected)
		self.assertEqual(len(set(registry.required_checks())), len(expected))


class TestRegistryMatchesTheApp(UnitTestCase):
	def test_declared_dependencies_match_required_apps(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		required = {dependency.app for dependency in registry.DEPENDENCIES if dependency.required}

		self.assertIn('required_apps = ["frappe", "erpnext"]', hooks)
		self.assertEqual(required, {"frappe", "erpnext"})

	def test_the_optional_dependency_is_never_imported(self):
		pattern = re.compile(
			r"^\s*(?:import\s+smartprocess_erpnext_accounting|from\s+smartprocess_erpnext_accounting\s+import)",
			re.MULTILINE,
		)

		for path in APP.rglob("*.py"):
			with self.subTest(file=path.name):
				self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))

	def test_doc_event_entries_cover_exactly_the_registered_doctypes(self):
		registered = set(hooks_composition.approved_doc_events())
		listed = set(registry.STANDARD_DOC_EVENT_DOCTYPES) | set(registry.OWN_DOC_EVENT_DOCTYPES)

		self.assertEqual(listed, registered)
		self.assertFalse(set(registry.STANDARD_DOC_EVENT_DOCTYPES) & set(registry.OWN_DOC_EVENT_DOCTYPES))

	def test_script_entries_cover_exactly_the_registered_doctypes(self):
		registered = set(hooks_composition.approved_doctype_js())
		listed = set(registry.STANDARD_SCRIPT_DOCTYPES) | set(registry.OWN_SCRIPT_DOCTYPES)

		self.assertEqual(listed, registered)
		self.assertEqual(set(registry.OWN_SCRIPT_DOCTYPES), {"Logistics Settings"})

	def test_the_registry_script_is_the_one_the_form_loads(self):
		scripts = hooks_composition.approved_doctype_js()["Logistics Settings"]
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")

		self.assertIn("public/js/logistics_settings_dependencies.js", scripts)
		self.assertIn("doctype_js = hooks_composition.approved_doctype_js()", hooks)

	def test_field_entries_match_the_shipped_custom_field_files(self):
		shipped = {}
		for path in sorted((MODULE / "custom").glob("*.json")):
			doc = read_json(path)
			shipped[doc["doctype"]] = [row["fieldname"] for row in doc["custom_fields"]]

		self.assertEqual(field_checks(), shipped)

	def test_record_entries_match_the_shipped_fixtures(self):
		shipped = set()
		for path in sorted((APP / "fixtures").glob("*.json")):
			for row in read_json(path):
				shipped.add((row["doctype"], row["name"]))

		self.assertEqual(record_checks(), shipped)

	def test_the_registry_lists_the_roles_the_installer_creates(self):
		def imported_roles(path):
			"""The ROLE_* names a file imports from `core.authz`, and only those."""
			names = set()
			for block in re.findall(
				r"core\.authz import([^)]*)", path.read_text(encoding="utf-8"), re.DOTALL
			):
				names.update(re.findall(r"ROLE_([A-Z_]+)", block))
			return names

		self.assertEqual(
			imported_roles(APP / "core" / "interop.py"),
			imported_roles(APP / "setup" / "roles.py"),
		)

	def test_the_css_asset_is_the_one_the_hooks_register(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")

		self.assertIn(f'app_include_css = ["{registry.CSS_ASSET}"]', hooks)
		self.assertTrue((APP / "public" / registry.CSS_ASSET.removeprefix("/assets/smartprocess_logistics/")).exists())

	def test_the_settings_form_carries_the_tab_and_the_field(self):
		doc = read_json(MODULE / "doctype" / "logistics_settings" / "logistics_settings.json")
		fields = {row["fieldname"]: row for row in doc["fields"]}

		self.assertEqual(fields["interop_tab"]["fieldtype"], "Tab Break")
		self.assertEqual(fields["interop_tab"]["label"], TITLE)
		self.assertEqual(fields["interop_section"]["fieldtype"], "Section Break")
		self.assertEqual(fields["interop_registry"]["fieldtype"], "HTML")
		self.assertLess(doc["field_order"].index("interop_tab"), doc["field_order"].index("interop_registry"))


class TestRegistryStatus(UnitTestCase):
	def test_a_complete_site_shows_every_entry_as_active(self):
		rows = registry.rows(all_facts_present())

		for row in rows:
			with self.subTest(target=row["target"]):
				self.assertEqual(row["status"], registry.STATUS_ACTIVE)
				self.assertEqual(row["severity"], registry.SEVERITY_OK)
				self.assertEqual(row["missing"], [])

	def test_no_facts_means_no_checked_entry_claims_to_be_applied(self):
		checked = {change.target for change in registry.APPLIED_CHANGES if change.check}
		rows = registry.rows({})

		self.assertEqual(len(rows), len(registry.DEPENDENCIES) + len(registry.APPLIED_CHANGES))
		for row in rows:
			with self.subTest(target=row["target"]):
				if row["target"] in checked:
					self.assertNotEqual(row["status"], registry.STATUS_ACTIVE)

	def test_a_missing_required_app_is_a_blocker_and_an_optional_one_is_not(self):
		rows = registry.rows({"installed": {"frappe": "16.55.2", "erpnext": "16.54.0"}})
		by_app = {row["target"].split(" — ")[0]: row for row in rows}

		self.assertEqual(by_app["frappe"]["status"], registry.STATUS_ACTIVE)
		self.assertEqual(by_app["frappe"]["version"], "16.55.2")
		self.assertEqual(by_app["smartprocess_erpnext_accounting"]["status"], registry.STATUS_ABSENT)
		self.assertEqual(by_app["smartprocess_erpnext_accounting"]["severity"], registry.SEVERITY_INFO)

		missing_required = {
			row["target"].split(" — ")[0]: row
			for row in registry.rows({"installed": {"erpnext": "16.54.0"}})
		}
		self.assertEqual(missing_required["frappe"]["severity"], registry.SEVERITY_BLOCKER)

	def test_one_missing_role_is_partial_and_names_only_that_role(self):
		facts = all_facts_present()
		facts["records"][("Role", registry.ROLE_RECORDS[0])] = False
		rows = registry.rows(facts)
		role_row = next(row for row in rows if row["target"].startswith("Role"))

		self.assertEqual(role_row["status"], registry.STATUS_PARTIAL)
		self.assertEqual(role_row["missing"], [registry.ROLE_RECORDS[0]])

	def test_a_missing_field_is_reported_by_name(self):
		facts = all_facts_present()
		facts["fields"]["Item"] = frozenset(set(registry.ITEM_FIELDS) - {"sp_serial_required"})
		rows = registry.rows(facts)
		item_row = next(row for row in rows if row["target"].startswith("Item "))

		self.assertEqual(item_row["status"], registry.STATUS_PARTIAL)
		self.assertEqual(item_row["missing"], ["sp_serial_required"])

	def test_a_doctype_with_no_field_facts_is_absent_rather_than_applied(self):
		facts = all_facts_present()
		facts["fields"]["Item"] = frozenset()
		rows = registry.rows(facts)
		item_row = next(row for row in rows if row["target"].startswith("Item "))

		self.assertEqual(item_row["status"], registry.STATUS_ABSENT)
		self.assertEqual(len(item_row["missing"]), len(registry.ITEM_FIELDS))

	def test_an_entry_without_a_check_reports_the_mechanism_as_applied(self):
		rows = registry.rows({})
		hooked = next(row for row in rows if row["target"].startswith("اسناد استاندارد — "))

		self.assertEqual(hooked["status"], registry.STATUS_ACTIVE)
		self.assertEqual(hooked["status_label"], "اعمال شده")


class TestRegistryReader(UnitTestCase):
	def test_the_reader_only_reads(self):
		source = (APP / "interop.py").read_text(encoding="utf-8")

		for forbidden in (
			"frappe.db.set_value",
			"frappe.db.sql",
			"frappe.db.commit",
			".insert(",
			".save(",
			"ignore_permissions",
			"frappe.get_doc(",
		):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, source)

	def test_the_reader_asks_for_read_permission(self):
		source = (APP / "interop.py").read_text(encoding="utf-8")

		self.assertIn('frappe.has_permission(SETTINGS_DOCTYPE, "read")', source)
		self.assertIn("methods=[\"GET\"]", source)

	def test_the_reader_names_the_same_title_the_form_shows(self):
		source = (APP / "interop.py").read_text(encoding="utf-8")

		self.assertIn(f'TITLE = "{TITLE}"', source)

	def test_the_form_script_decides_nothing(self):
		source = (APP / "public" / "js" / "logistics_settings_dependencies.js").read_text(
			encoding="utf-8"
		)

		for forbidden in ("frm.set_value", "frm.save", "frappe.model.set_value", "frm.add_child"):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, source)
		self.assertIn("frappe.utils.escape_html", source)
