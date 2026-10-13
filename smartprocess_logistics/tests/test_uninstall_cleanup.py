"""`after_uninstall`: the navigation and the roles that a removal leaves behind.

This hook is the only part of the uninstall path that stayed when the app stopped keeping its own
copy of its records. It is not about data: it is about the two things Frappe's own cleanup cannot
see, both of them because of how this app names things.

- The desktop icon is named `سریال لجستیک`. `frappe.utils.install.delete_desktop_icon_and_sidebar`
  removes the icons whose name or `parent_icon` equals the app's **title**, and the title is the
  English `SmartProcess Logistics`. The icon therefore survives the removal, pointing at a
  sidebar that no longer exists.
- The three roles have no link to a module anywhere, so nothing about the removal reaches them.

The tests below pin both, and the property that makes the hook safe to have at all: it never
raises. It runs after the app is already gone, so an exception would report a completed removal
as a failed one.
"""

import importlib
import json
import pathlib

from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.tests.fake_bench import FakeLifecycle
from smartprocess_logistics.tests.test_lifecycle import populated_bench

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "smartprocess_logistics"


def shipped_icon_name():
	icon_files = sorted((APP_ROOT / "desktop_icon").glob("*.json"))

	if not icon_files:
		raise AssertionError("the app ships no desktop icon")

	return json.loads(icon_files[0].read_text(encoding="utf-8"))["name"]


class UninstallCleanupTest(UnitTestCase):
	def setUp(self):
		self._lifecycles = []

	def tearDown(self):
		for lifecycle in reversed(self._lifecycles):
			lifecycle.stop()

	def given(self, bench):
		lifecycle = FakeLifecycle(bench)
		self._lifecycles.append(lifecycle)
		self.bench = lifecycle.start()

		return self.bench

	def load(self):
		return importlib.import_module("smartprocess_logistics.install")

	def with_navigation(self, bench, icon_name, roles=(), held=(), profile_items=()):
		"""A site with the app's navigation and its roles, and a site's own way of naming them.

		`DocField` carries the metadata the hook reads instead of a hardcoded list: every field in
		the site whose type is Link and whose options are `Role`. `Role Profile Item` is one of the
		doctypes that does **not** exist in this build, which is why the hook reads this table
		rather than a list of doctype names written from memory.
		"""
		bench.add_doctype("Desktop Icon", rows=[{"name": icon_name, "parent_icon": ""}])
		bench.add_doctype("Role", rows=[{"name": role} for role in roles])
		bench.add_doctype("Has Role", rows=[{"name": f"hr-{role}", "role": role} for role in held])
		bench.add_doctype("Custom DocPerm", rows=[])
		bench.add_doctype("DocPerm", rows=[])
		bench.add_doctype("Custom Field", rows=[])
		bench.add_doctype(
			"DocField",
			rows=[
				{
					"name": "df-1",
					"parent": "Has Role",
					"fieldname": "role",
					"fieldtype": "Link",
					"options": "Role",
				},
				{
					"name": "df-2",
					"parent": "Custom DocPerm",
					"fieldname": "role",
					"fieldtype": "Link",
					"options": "Role",
				},
				{
					"name": "df-3",
					"parent": "DocPerm",
					"fieldname": "role",
					"fieldtype": "Link",
					"options": "Role",
				},
				{
					"name": "df-4",
					"parent": "Role Profile Item",
					"fieldname": "role",
					"fieldtype": "Link",
					"options": "Role",
				},
				{
					"name": "df-5",
					"parent": "Item",
					"fieldname": "item_code",
					"fieldtype": "Link",
					"options": "Item",
				},
			],
		)

		if profile_items or profile_items == ():
			bench.add_doctype(
				"Role Profile Item",
				rows=[{"name": f"rpi-{role}", "role": role} for role in profile_items],
			)

		return bench

	def test_the_icon_this_app_named_in_persian_is_removed(self):
		bench = self.with_navigation(populated_bench(), shipped_icon_name())
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["desktop_icons"], (shipped_icon_name(),))
		self.assertEqual(report["desktop_icons_kept"], ())
		self.assertEqual(bench.deletions, [("Desktop Icon", shipped_icon_name())])

	def test_a_role_nothing_refers_to_is_removed(self):
		bench = self.with_navigation(
			populated_bench(), shipped_icon_name(), roles=("Logistics Serial Viewer",)
		)
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["roles_removed"], ("Logistics Serial Viewer",))
		self.assertEqual(report["roles_kept"], ())

	def test_a_role_a_user_still_holds_is_left_alone(self):
		"""Removing it would change what that user can do everywhere else in the site."""
		bench = self.with_navigation(
			populated_bench(),
			shipped_icon_name(),
			roles=("Logistics Serial Viewer", "Logistics Serial Editor"),
			held=("Logistics Serial Editor",),
		)
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["roles_removed"], ("Logistics Serial Viewer",))
		self.assertEqual(report["roles_kept"], ("Logistics Serial Editor",))
		self.assertNotIn(("Role", "Logistics Serial Editor"), bench.deletions)

	def test_a_role_a_role_profile_holds_is_kept(self):
		"""The reference is found through the site's own metadata, not a list written here."""
		bench = self.with_navigation(
			populated_bench(),
			shipped_icon_name(),
			roles=("Logistics Serial Viewer",),
			profile_items=("Logistics Serial Viewer",),
		)
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["roles_kept"], ("Logistics Serial Viewer",))
		self.assertEqual(report["roles_removed"], ())

	def test_a_role_the_framework_refuses_to_delete_is_reported_and_does_not_raise(self):
		"""This hook runs after the app is gone: raising would report a success as a failure."""
		bench = self.with_navigation(
			populated_bench(), shipped_icon_name(), roles=("Logistics Serial Viewer",)
		)
		bench.delete_failures = {("Role", "Logistics Serial Viewer")}
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["roles_kept"], ("Logistics Serial Viewer",))
		self.assertEqual(report["roles_removed"], ())
		self.assertEqual(len(bench.errors), 1)

	def test_an_icon_the_framework_refuses_to_delete_is_reported_and_does_not_raise(self):
		bench = self.with_navigation(populated_bench(), shipped_icon_name())
		bench.delete_failures = {("Desktop Icon", shipped_icon_name())}
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["desktop_icons"], ())
		self.assertEqual(report["desktop_icons_kept"], (shipped_icon_name(),))
		self.assertEqual(len(bench.errors), 1)

	def test_it_is_safe_on_a_site_that_has_none_of_them(self):
		"""The framework's own cleanup may already have taken them; a second pass must be quiet."""
		bench = self.with_navigation(populated_bench(), shipped_icon_name(), roles=())
		bench.tables["Desktop Icon"] = []
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(
			report,
			{
				"desktop_icons": (),
				"desktop_icons_kept": (),
				"roles_removed": (),
				"roles_kept": (),
			},
		)
		self.assertEqual(bench.deletions, [])

	def test_a_child_icon_of_this_apps_icon_is_removed_too(self):
		bench = self.with_navigation(populated_bench(), shipped_icon_name())
		bench.tables["Desktop Icon"].append(
			{"name": "child", "parent_icon": shipped_icon_name()}
		)
		bench = self.given(bench)

		report = self.load().after_uninstall()

		self.assertEqual(report["desktop_icons"], (shipped_icon_name(), "child"))

	def test_it_removes_no_document_of_any_other_doctype(self):
		bench = self.with_navigation(
			populated_bench(), shipped_icon_name(), roles=("Logistics Serial Viewer",)
		)
		before = [dict(row) for row in bench.tables["Logistics Serial"]]
		bench = self.given(bench)

		self.load().after_uninstall()

		for doctype, _name in bench.deletions:
			with self.subTest(doctype=doctype):
				self.assertIn(doctype, ("Desktop Icon", "Role"))

		self.assertEqual(bench.tables["Logistics Serial"], before)
