"""A Frappe submodule reached through the package must be imported, not assumed.

`import frappe` does not pull in every submodule. Measured in this site's own interpreter, right
after a bare `import frappe`, these exist — `db`, `local`, `session`, `utils`, `model`,
`exceptions`, `desk`, `core`, `database` — and these do not: `permissions` and `rate_limiter`.

So `frappe.permissions.get_user_permissions(...)` worked only once something else had imported
the submodule. That is invisible in a warm process and fatal in a cold one: the **first request
handled by every new worker** raised `AttributeError`, and the second request succeeded. That is
exactly what happened on SmartProcessERP, and this test is the check that would have caught it.

The scan reads the parsed module, not its text, so a submodule named in a comment or a docstring
is prose and a submodule named in an expression is a dependency.
"""

import ast
import pathlib

from smartprocess_logistics.tests import schema_checks
from smartprocess_logistics.tests.base import UnitTestCase

# Submodules a bare `import frappe` provides. Measured, not assumed; see the module docstring.
ALWAYS_PRESENT = frozenset(
	{"db", "local", "session", "utils", "model", "exceptions", "desk", "core", "database"}
)


def app_modules():
	"""Every Python module of this app, the tests excluded."""
	root = schema_checks.APP_ROOT / "smartprocess_logistics"

	return sorted(
		path for path in root.rglob("*.py") if "tests" not in path.parts and "__pycache__" not in path.parts
	)


def _is_frappe(node):
	return isinstance(node, ast.Name) and node.id == "frappe"


def submodules_reached(tree):
	"""Names reached as `frappe.<name>.something`, taken from the code."""
	found = set()

	for node in ast.walk(tree):
		if not isinstance(node, ast.Attribute):
			continue

		value = node.value

		if isinstance(value, ast.Attribute) and _is_frappe(value.value):
			found.add(value.attr)

	return found


def imported_here(tree, name):
	"""Whether this module imports `frappe.<name>` itself, rather than inheriting it."""
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			for alias in node.names:
				if alias.name == f"frappe.{name}" or alias.name.startswith(f"frappe.{name}."):
					return True

		elif isinstance(node, ast.ImportFrom):
			module = node.module or ""
			if module == f"frappe.{name}" or module.startswith(f"frappe.{name}."):
				return True

	return False


class TestFrappeSubmoduleImports(UnitTestCase):
	def test_no_module_reaches_a_submodule_it_did_not_import(self):
		for path in app_modules():
			tree = ast.parse(path.read_text(encoding="utf-8"))

			for name in sorted(submodules_reached(tree)):
				if name in ALWAYS_PRESENT:
					continue

				with self.subTest(module=path.name, submodule=name):
					self.assertTrue(
						imported_here(tree, name),
						f"{path.name} uses frappe.{name} but does not import it; a cold"
						" interpreter does not have that attribute",
					)

	def test_the_scan_sees_code_and_not_prose(self):
		"""A name in a comment is not a dependency, which a text scan would have got wrong."""
		tree = ast.parse("# frappe.permissions is mentioned here\nx = frappe.db.get_value\n")

		self.assertEqual(submodules_reached(tree), {"db"})

	def test_the_gateway_imports_permissions_by_name(self):
		"""The bug's own file: it imports the submodule and never reaches for the attribute."""
		gateway = schema_checks.APP_ROOT / "smartprocess_logistics" / "api" / "gateway.py"
		tree = ast.parse(gateway.read_text(encoding="utf-8"))

		self.assertTrue(imported_here(tree, "permissions"))
		self.assertNotIn("permissions", submodules_reached(tree))


def core_modules():
	"""The modules that must be pure: no Frappe, no database, testable off a bench."""
	root = schema_checks.APP_ROOT / "smartprocess_logistics" / "core"

	return sorted(path for path in root.glob("*.py") if path.name != "__init__.py")


def imports_frappe(tree):
	"""The import statements that reach `frappe`, whether by name or by submodule."""
	reached = []

	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			for alias in node.names:
				if alias.name == "frappe" or alias.name.startswith("frappe."):
					reached.append(alias.name)

		elif isinstance(node, ast.ImportFrom):
			module = node.module or ""

			if module == "frappe" or module.startswith("frappe."):
				reached.append(module)

	return reached


class TestCoreStaysPure(UnitTestCase):
	"""`core` is the layer every rule lives in, and the layer a bench is not needed for.

	`test_no_module_reaches_a_submodule_it_did_not_import` covers the case where code reaches
	`frappe.permissions` without importing it — the AttributeError that hit the first request of
	every new worker. This covers the other half: code in `core` importing Frappe **at all**.
	That is what keeps every rule testable with `python -m unittest` and no site, and it is the
	property the whole test suite rests on.
	"""

	def test_no_core_module_imports_frappe(self):
		self.assertTrue(core_modules(), "no core modules were found to check")

		for path in core_modules():
			tree = ast.parse(path.read_text(encoding="utf-8"))

			with self.subTest(module=path.name):
				self.assertEqual(
					imports_frappe(tree),
					[],
					f"{path.name} imports Frappe, so its rules cannot run off a bench",
				)

	def test_the_scan_reads_imports_and_not_prose(self):
		# A docstring that mentions the framework is not an import, and the check must not
		# confuse the two — the module that says the most about Frappe is the one that must not
		# touch it.
		tree = ast.parse('"""frappe.permissions is not used here."""\nimport os\n')

		self.assertEqual(imports_frappe(tree), [])


# The endpoints and the gateway methods they may write through. A read is not listed: what this
# test protects is that nothing is *written* before the request is allowed to write it.
ENDPOINTS = (
	"get_invoice_serial_status",
	"get_serial_assignments",
	"set_row_serials",
	"delete_assignments",
	"prepare_import_preview",
	"confirm_import",
	# The export and the import of this app's own records. `preview_serial_restore` is a read of
	# the file and a read of the site, and is listed with the writes below because it is part of a
	# two-step act whose second step writes; keeping the pair in one place is what makes the
	# authorization of both visible at once.
	"export_serials",
	"preview_serial_restore",
	"apply_serial_restore",
)

WRITE_METHODS = frozenset(
	{
		"create_session",
		"delete_assignments",
		"delete_row_assignments",
		"expire_sessions",
		"find_or_create_serials",
		"insert_assignments",
		"lock_invoice",
		"lock_serials",
		"lock_session",
		"mark_session_applied",
		"purge_sessions",
		"insert_exported",
	}
)

AUTHZ_METHODS = frozenset(
	{
		"authorize",
		"require_company",
		"require_endpoint",
		"require_session_owner",
	}
)


def _endpoint_functions(tree):
	"""Every function in the module decorated with `frappe.whitelist`."""
	found = {}

	for node in ast.walk(tree):
		if not isinstance(node, ast.FunctionDef):
			continue

		for decorator in node.decorator_list:
			target = decorator.func if isinstance(decorator, ast.Call) else decorator

			if isinstance(target, ast.Attribute) and target.attr == "whitelist" and _is_frappe(target.value):
				found[node.name] = node

	return found


def _call_lines(node, owner, methods):
	"""The line numbers of calls to `<owner>.<method>` for the methods given, inside `node`."""
	lines = []

	for inner in ast.walk(node):
		if not isinstance(inner, ast.Call):
			continue

		func = inner.func

		if (
			isinstance(func, ast.Attribute)
			and isinstance(func.value, ast.Name)
			and func.value.id == owner
			and func.attr in methods
		):
			lines.append(inner.lineno)

	return lines


class TestEveryEndpointAuthorizes(UnitTestCase):
	"""Authorization is a decision in `core.authz`, and an endpoint that forgets to ask is a
	request that runs unchecked.

	The endpoints and the gateway cannot be exercised without a bench, so this reads them: the
	set of whitelisted functions is pinned, every one of them asks `core.authz` something, and no
	write through the gateway happens before that. A new endpoint, or a write moved above the
	check, fails here rather than on somebody's site.
	"""

	def endpoints_module(self):
		path = schema_checks.APP_ROOT / "smartprocess_logistics" / "api" / "endpoints.py"
		source = path.read_text(encoding="utf-8")

		return path, source, ast.parse(source)

	def test_the_whitelisted_functions_are_exactly_the_endpoints_named_here(self):
		_path, _source, tree = self.endpoints_module()

		self.assertEqual(tuple(sorted(_endpoint_functions(tree))), tuple(sorted(ENDPOINTS)))

	def test_every_endpoint_asks_core_authz(self):
		_path, _source, tree = self.endpoints_module()

		for name, node in _endpoint_functions(tree).items():
			with self.subTest(endpoint=name):
				self.assertTrue(
					_call_lines(node, "authz", AUTHZ_METHODS),
					f"{name} never calls core.authz; a request it serves is unchecked",
				)

	def test_no_endpoint_writes_before_it_is_authorized(self):
		_path, source, tree = self.endpoints_module()

		for name, node in _endpoint_functions(tree).items():
			authorized = _call_lines(node, "authz", AUTHZ_METHODS)
			writes = _call_lines(node, "gateway", WRITE_METHODS)

			if not writes:
				continue

			with self.subTest(endpoint=name):
				self.assertTrue(authorized, f"{name} writes without asking authz")
				self.assertLess(
					min(authorized),
					min(writes),
					f"{name} writes through the gateway before it is authorized",
				)
				self.assertIn(f"def {name}", source)

	def test_the_check_fires_on_a_write_that_comes_first(self):
		# The check has to be able to fail, or it is a comment with a test around it.
		tree = ast.parse(
			"@frappe.whitelist()\n"
			"def do_thing(x):\n"
			"    gateway.create_session(x)\n"
			"    authz.require_endpoint(principal, 'do_thing')\n"
		)
		node = _endpoint_functions(tree)["do_thing"]

		authorized = _call_lines(node, "authz", AUTHZ_METHODS)
		writes = _call_lines(node, "gateway", WRITE_METHODS)

		self.assertTrue(writes)
		self.assertGreater(min(authorized), min(writes))

	def test_the_check_fires_on_an_endpoint_that_never_asks(self):
		tree = ast.parse("@frappe.whitelist()\ndef do_thing(x):\n    return gateway.read_rows(x)\n")
		node = _endpoint_functions(tree)["do_thing"]

		self.assertEqual(_call_lines(node, "authz", AUTHZ_METHODS), [])
