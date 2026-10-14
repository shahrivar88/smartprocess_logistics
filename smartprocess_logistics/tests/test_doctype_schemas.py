"""The DocType definitions must satisfy Frappe's own rules before anything is installed.

This test reads JSON files from disk and derives from `UnitTestCase`, which neither seeds
records nor rolls anything back, so it writes nothing and needs no test data.
"""

import json
import pathlib
import tempfile
from contextlib import contextmanager

from smartprocess_logistics.core.normalization import NORMALIZATION_VERSION
from smartprocess_logistics.tests import schema_checks
from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.tests.schema_checks import check_all


class TestDocTypeSchemas(UnitTestCase):
	def test_every_doctype_definition_is_valid(self):
		problems = check_all()

		self.assertEqual(problems, [], msg="\n".join(problems))


class TestSetOnlyOnceIsRefused(UnitTestCase):
	"""The check for the defect that stopped the installation three times.

	`Logistics Settings.installed_on` was declared `set_only_once`, on the theory that the
	framework would then refuse any later change to it. Frappe honours the flag only when a
	document is **inserted** — `Document.validate_set_only_once` returns immediately unless
	`_doc_before_save` exists — and `installer.init_singles` inserts the Single's row before
	`after_install` runs. So the first write this app made was an update, and it was refused with
	`CannotChangeConstantError`.

	That rule lives in the framework and cannot run in a database-free suite, so what is tested
	here is the property the rule demands of this app's metadata: no field carries the flag.
	"""

	def test_no_field_in_this_app_sets_set_only_once(self):
		for _path, doc in self.definitions():
			for field in doc.get("fields") or []:
				with self.subTest(doctype=doc["name"], field=field.get("fieldname")):
					self.assertNotIn("set_only_once", field)

	def test_the_check_reports_a_field_that_sets_it(self):
		problems = self.check([{"fieldname": "installed_on", "fieldtype": "Datetime", "set_only_once": 1}])

		self.assertEqual(len(problems), 1)
		self.assertIn("set_only_once", problems[0])
		self.assertIn("installed_on", problems[0])

	def test_the_check_accepts_the_corrected_metadata(self):
		problems = self.check([{"fieldname": "installed_on", "fieldtype": "Datetime", "read_only": 1}])

		self.assertEqual(problems, [])

	def test_the_app_wide_validator_runs_the_check(self):
		"""`check_all` is what the suite asserts on, so this pins that it calls the rule."""
		with self.temporary_doctype({"set_only_once": 1}):
			problems = check_all()

		self.assertTrue(
			any("set_only_once" in problem for problem in problems),
			f"check_all did not apply the set_only_once rule: {problems}",
		)

	def definitions(self):
		return [
			(path, json.loads(path.read_text(encoding="utf-8"))) for path in schema_checks.doctype_paths()
		]

	def check(self, fields):
		problems = []
		schema_checks._check_set_only_once(
			pathlib.Path("logistics_x.json"), {"name": "Logistics X", "fields": fields}, problems
		)

		return problems

	@contextmanager
	def temporary_doctype(self, field):
		"""Point `check_all` at a DocType built on the spot, for the length of the block."""
		with tempfile.TemporaryDirectory() as directory:
			folder = pathlib.Path(directory) / "logistics_x"
			folder.mkdir()
			json_path = folder / "logistics_x.json"
			json_path.write_text(
				json.dumps({"name": "Logistics X", "fields": [{"fieldname": "x", **field}]}),
				encoding="utf-8",
			)

			original = schema_checks.doctype_paths
			schema_checks.doctype_paths = lambda: [json_path]

			try:
				yield json_path
			finally:
				schema_checks.doctype_paths = original


class TestNormalizationVersionMatchesTheCode(UnitTestCase):
	"""The shipped default must be the rule set the code applies.

	This app shipped `1` while `core.normalization.NORMALIZATION_VERSION` was `2`. The settings
	screen therefore described rules the app no longer used, and nothing failed: the field has no
	reader, so only reading the two values side by side showed the drift. `setup.seeds` repairs a
	site whose stored value has fallen behind; this check is what stops the shipped default from
	starting out behind, and it is what makes bumping the rule set a two-line change.
	"""

	def test_the_shipped_default_is_the_implemented_rule_set(self):
		for path, doc in self.definitions():
			problems = []
			schema_checks._check_normalization_version(path, doc, problems)

			with self.subTest(doctype=doc["name"]):
				self.assertEqual(problems, [])

	def test_the_check_reports_a_default_that_disagrees(self):
		problems = []

		schema_checks._check_normalization_version(
			pathlib.Path("logistics_settings.json"),
			{
				"name": "Logistics Settings",
				"fields": [
					{
						"fieldname": "normalization_version",
						"fieldtype": "Int",
						"default": NORMALIZATION_VERSION - 1,
					}
				],
			},
			problems,
		)

		self.assertEqual(len(problems), 1)
		self.assertIn("normalization_version", problems[0])

	def test_the_check_leaves_another_doctype_alone(self):
		problems = []

		schema_checks._check_normalization_version(
			pathlib.Path("logistics_serial.json"),
			{
				"name": "Logistics Serial",
				"fields": [{"fieldname": "normalization_version", "fieldtype": "Int", "default": "0"}],
			},
			problems,
		)

		self.assertEqual(problems, [])

	def definitions(self):
		return [
			(path, json.loads(path.read_text(encoding="utf-8"))) for path in schema_checks.doctype_paths()
		]


class TestHiddenAndMandatoryIsRefused(UnitTestCase):
	"""The check for the second metadata conflict, found by the same audit.

	Three fields were `hidden: 1` with `reqd: 1` and no default: the two server-derived
	`normalized_serial` copies and `Logistics Import Session.token`. Frappe refuses that
	combination in `doctype.py::check_hidden_and_mandatory`, because a field nobody can see is a
	field nobody can fill.

	The rule is not in the install path — it runs from the customization paths — so those DocTypes
	installed cleanly and would have failed the first time somebody saved one from the Desk. The
	check here is the same rule, run against the files before anything is installed.
	"""

	def test_no_field_in_this_app_is_hidden_and_required_without_a_default(self):
		for _path, doc in self.definitions():
			for field in doc.get("fields") or []:
				with self.subTest(doctype=doc["name"], field=field.get("fieldname")):
					refused = field.get("hidden") and field.get("reqd") and not field.get("default")
					self.assertFalse(refused, f"{field.get('fieldname')} would be refused")

	def test_the_check_reports_the_refused_combination(self):
		problems = self.check([{"fieldname": "token", "fieldtype": "Data", "hidden": 1, "reqd": 1}])

		self.assertEqual(len(problems), 1)
		self.assertIn("token", problems[0])
		self.assertIn("hidden", problems[0])

	def test_the_check_accepts_a_read_only_required_field(self):
		problems = self.check(
			[{"fieldname": "normalized_serial", "fieldtype": "Data", "read_only": 1, "reqd": 1}]
		)

		self.assertEqual(problems, [])

	def test_the_check_accepts_a_hidden_field_that_is_not_required(self):
		problems = self.check([{"fieldname": "token", "fieldtype": "Data", "hidden": 1, "read_only": 1}])

		self.assertEqual(problems, [])

	def test_the_check_accepts_a_hidden_required_field_with_a_default(self):
		problems = self.check(
			[{"fieldname": "flag", "fieldtype": "Check", "hidden": 1, "reqd": 1, "default": "0"}]
		)

		self.assertEqual(problems, [])

	def test_the_app_wide_validator_runs_the_check(self):
		with self.temporary_doctype({"hidden": 1, "reqd": 1}):
			problems = check_all()

		self.assertTrue(
			any("hidden and required" in problem for problem in problems),
			f"check_all did not apply the hidden-and-required rule: {problems}",
		)

	def definitions(self):
		return [
			(path, json.loads(path.read_text(encoding="utf-8"))) for path in schema_checks.doctype_paths()
		]

	def check(self, fields):
		problems = []
		schema_checks._check_hidden_and_mandatory(
			pathlib.Path("logistics_x.json"), {"name": "Logistics X", "fields": fields}, problems
		)

		return problems

	@contextmanager
	def temporary_doctype(self, field):
		with tempfile.TemporaryDirectory() as directory:
			folder = pathlib.Path(directory) / "logistics_x"
			folder.mkdir()
			json_path = folder / "logistics_x.json"
			json_path.write_text(
				json.dumps({"name": "Logistics X", "fields": [{"fieldname": "x", **field}]}),
				encoding="utf-8",
			)

			original = schema_checks.doctype_paths
			schema_checks.doctype_paths = lambda: [json_path]

			try:
				yield json_path
			finally:
				schema_checks.doctype_paths = original
