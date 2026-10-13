"""Every DocType must carry a controller module that Frappe can actually import.

Why this exists
---------------
The first installation attempt on SmartProcessERP stopped inside `sync_for` with

    ImportError: Module import failed for Logistics Import Delimiter
    No module named '...doctype.logistics_import_delimiter.logistics_import_delimiter'

The DocType JSONs were valid, so every other check in this package passed. What was missing was
the Python module: `DocType.on_update` calls `run_module_method("on_doctype_update")` for every
DocType it syncs, and that imports `<app>.<module>.doctype.<folder>.<folder>` and takes the class
named after the DocType with its spaces and dashes removed, whether or not the controller defines
anything. A DocType folder with only `__init__.py` and a JSON is therefore a DocType that cannot
be installed.

Two checks, deliberately different
---------------------------------
The static half lives in `schema_checks._check_controller`, beside the other per-file rules, and
can be run without a site. This module adds what a static reading cannot show: the module really
is importable **by the dotted path Frappe uses**, and the class really is there and really is a
`Document` subclass. Importing it needs `frappe.model.document`, which a machine with no bench does
not have, so this module stands a two-attribute stand-in in for it, the same way
`tests/fake_bench.py` does for the lifecycle tests.
"""

import ast
import importlib
import json
import pathlib
import sys
import tempfile
import types

from smartprocess_logistics.tests import schema_checks
from smartprocess_logistics.tests.base import UnitTestCase

FAKE_MODULES = ("frappe", "frappe.model", "frappe.model.document")


def dotted_module_name(json_path: pathlib.Path) -> str:
	"""The import path Frappe builds for a DocType, derived from the JSON's own location."""
	return ".".join(json_path.relative_to(schema_checks.APP_ROOT).with_suffix("").parts)


class TestDocTypeControllers(UnitTestCase):
	def setUp(self):
		self._saved = {name: sys.modules.get(name) for name in FAKE_MODULES}
		self._imported = []
		self.document_class = type("Document", (), {})

		frappe = types.ModuleType("frappe")
		model = types.ModuleType("frappe.model")
		document = types.ModuleType("frappe.model.document")
		document.Document = self.document_class
		frappe.model = model
		model.document = document

		sys.modules.update(
			{"frappe": frappe, "frappe.model": model, "frappe.model.document": document}
		)

	def tearDown(self):
		for name in self._imported:
			sys.modules.pop(name, None)

		for name, module in self._saved.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	def definitions(self):
		"""(json path, parsed JSON) for every DocType in the app."""
		return [
			(path, json.loads(path.read_text(encoding="utf-8")))
			for path in schema_checks.doctype_paths()
		]

	def load(self, json_path):
		name = dotted_module_name(json_path)
		module = importlib.import_module(name)
		self._imported.append(name)

		return name, module

	# --- the six files ------------------------------------------------------------------

	def test_every_doctype_json_has_a_controller_module_beside_it(self):
		for path, _ in self.definitions():
			with self.subTest(doctype=path.parent.name):
				self.assertTrue(path.with_suffix(".py").exists(), f"{path.name} has no controller")

	def test_every_controller_imports_by_the_path_frappe_uses(self):
		for path, _ in self.definitions():
			with self.subTest(doctype=path.parent.name):
				name, module = self.load(path)
				self.assertEqual(module.__name__, name)

	def test_every_controller_exposes_the_class_frappe_looks_for(self):
		for path, doc in self.definitions():
			with self.subTest(doctype=doc["name"]):
				_, module = self.load(path)
				classname = schema_checks.expected_controller_class(doc["name"])
				controller = getattr(module, classname, None)

				self.assertTrue(
					isinstance(controller, type),
					f"{path.name} declares no class {classname!r}",
				)
				self.assertTrue(
					issubclass(controller, self.document_class),
					f"{classname} must extend frappe.model.document.Document",
				)

	def test_the_class_name_is_the_doctype_name_without_spaces_or_dashes(self):
		"""The name is not a free choice: `get_controller` builds it from the DocType name."""
		for path, doc in self.definitions():
			with self.subTest(doctype=doc["name"]):
				_, module = self.load(path)
				expected = doc["name"].replace(" ", "").replace("-", "")

				self.assertTrue(hasattr(module, expected), f"{path.name} must declare {expected}")

	# --- the controllers stay empty -----------------------------------------------------

	def test_a_controller_declares_nothing_but_its_own_class(self):
		"""No business logic and no hooks: a decision here is a decision no test can reach."""
		for path, doc in self.definitions():
			with self.subTest(doctype=doc["name"]):
				tree = ast.parse(path.with_suffix(".py").read_text(encoding="utf-8"))
				classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
				other = [
					node
					for node in tree.body
					if not isinstance(node, (ast.ClassDef, ast.ImportFrom, ast.Expr))
				]

				self.assertEqual(len(classes), 1)
				self.assertEqual(classes[0].name, schema_checks.expected_controller_class(doc["name"]))
				self.assertEqual(len(classes[0].bases), 1)
				self.assertIsInstance(classes[0].bases[0], ast.Name)
				self.assertEqual(classes[0].bases[0].id, "Document")
				self.assertEqual(other, [], "a controller carries only its class and its import")

				body = [node for node in classes[0].body if not isinstance(node, ast.Expr)]
				self.assertTrue(
					all(isinstance(node, ast.Pass) for node in body),
					f"{classes[0].name} must not define anything",
				)

				imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
				self.assertEqual(len(imports), 1)
				self.assertEqual((imports[0].module, imports[0].names[0].name), ("frappe.model.document", "Document"))

	# --- coverage -----------------------------------------------------------------------

	def test_the_app_has_a_standard_a_single_and_a_child_doctype_to_check(self):
		"""Without this, the loops above would pass over an empty or partial set."""
		parsed = [doc for _, doc in self.definitions()]
		kinds = {
			"child": [doc for doc in parsed if doc.get("istable")],
			"single": [doc for doc in parsed if doc.get("issingle")],
			"standard": [doc for doc in parsed if not doc.get("istable") and not doc.get("issingle")],
		}

		self.assertEqual(len(parsed), 6)
		for kind, docs in kinds.items():
			with self.subTest(kind=kind):
				self.assertTrue(docs, f"no {kind} DocType is covered")

	# --- the checks catch the real defect -----------------------------------------------

	def test_the_validator_reports_a_missing_controller_module(self):
		problems = self.validate(controller=None)

		self.assertEqual(len(problems), 1)
		self.assertIn("no controller module", problems[0])

	def test_the_validator_reports_a_controller_with_the_wrong_class(self):
		problems = self.validate(controller="class SomethingElse:\n\tpass\n")

		self.assertEqual(len(problems), 1)
		self.assertIn("declares no class 'LogisticsX'", problems[0])

	def test_the_validator_accepts_a_correct_controller(self):
		problems = self.validate(
			controller="from frappe.model.document import Document\n\n\nclass LogisticsX(Document):\n\tpass\n"
		)

		self.assertEqual(problems, [])

	def validate(self, controller):
		"""Run the validator's controller rule against a DocType built on the spot."""
		with tempfile.TemporaryDirectory() as directory:
			folder = pathlib.Path(directory) / "logistics_x"
			folder.mkdir()
			json_path = folder / "logistics_x.json"
			json_path.write_text('{"name": "Logistics X"}', encoding="utf-8")

			if controller is not None:
				(folder / "logistics_x.py").write_text(controller, encoding="utf-8")

			problems = []
			schema_checks._check_controller(json_path, {"name": "Logistics X"}, problems)

			return problems

	def test_the_app_wide_validator_runs_the_controller_rule(self):
		"""`test_doctype_schemas` asserts the app is clean; this asserts the rule is wired in."""
		with tempfile.TemporaryDirectory() as directory:
			folder = pathlib.Path(directory) / "logistics_x"
			folder.mkdir()
			json_path = folder / "logistics_x.json"
			json_path.write_text('{"name": "Logistics X"}', encoding="utf-8")

			original = schema_checks.doctype_paths
			schema_checks.doctype_paths = lambda: [json_path]

			try:
				problems = schema_checks.check_all()
			finally:
				schema_checks.doctype_paths = original

		self.assertTrue(
			any("no controller module" in problem for problem in problems),
			f"check_all did not apply the controller rule: {problems}",
		)
