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

from smartprocess_logistics.core import authz
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


class TestTheFilePreviewWritesNothing(UnitTestCase):
	"""Reading a file is a read: no session, no row, and nothing about a serial decided.

	The endpoint used to store a preview session so that `confirm_import` could apply it, which
	made a file import a path that wrote to the invoice. The window stages what it reads into its
	columns now, and saving is what writes — so the preview has no reason to touch the database,
	and this is the check that says so if it ever grows one again.
	"""

	def body(self):
		path = schema_checks.APP_ROOT / "smartprocess_logistics" / "api" / "endpoints.py"
		source = path.read_text(encoding="utf-8")
		start = source.index("def prepare_import_preview(")

		return source[start : source.index("\ndef ", start + 1)]

	def test_it_reads_the_file_and_returns_its_values(self):
		self.assertIn("_file_values(", self.body())
		self.assertIn('"values": list(values),', self.body())

	def test_it_stores_no_session_and_writes_nothing(self):
		self.assertNotIn("_store_session", self.body())
		self.assertNotIn("create_session", self.body())


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
	# One serial added to one row, which is what the scan station calls once per label. It sits
	# beside `set_row_serials` rather than replacing it: that one rewrites a row's whole set, and
	# an import of a file still needs to be able to.
	"add_row_serial",
	# And the same act for a batch of labels on one row, which is what a save sends: one answer
	# for one row instead of one answer per label.
	"add_row_serials",
	"delete_assignments",
	"prepare_import_preview",
	# The export and the import of this app's own records. `preview_serial_restore` is a read of
	# the file and a read of the site, and is listed with the writes below because it is part of a
	# two-step act whose second step writes; keeping the pair in one place is what makes the
	# authorization of both visible at once.
	"export_serials",
	"preview_serial_restore",
	"apply_serial_restore",
	# Moving a cancelled invoice's serials onto its amendment, and the preview of what that
	# would do. The pair is listed together for the same reason the restore pair is: the second
	# one writes, and keeping them side by side is what makes the authorization of both visible
	# at once.
	"preview_serial_transfer",
	"apply_serial_transfer",
	# The one path that may place a registration on a cancelled invoice, and the only endpoint that
	# asks for a role as well as a level (`core.authz.ENDPOINT_ROLES`). Listed beside the ordinary
	# restore, because the two differ in exactly one value and reading them together is how that
	# stays visible.
	"preview_cancelled_serial_history",
	"preview_restore_audit_records",
	"restore_audit_records",
	"restore_cancelled_serial_history",
	# What this app's reports open on: one read that names only the companies the caller may
	# read, through `core.authz`. It writes nothing, so it is named read-only below as well.
	"report_filter_defaults",
)

WRITE_METHODS = frozenset(
	{
		"delete_assignments",
		"delete_row_assignments",
		"expire_sessions",
		"find_or_create_serials",
		"insert_assignments",
		"insert_transfer",
		"insert_transfers",
		"lock_invoice",
		"lock_serials",
		"purge_sessions",
		"insert_exported",
	}
)

# Writing that goes through the orchestration rather than the gateway directly. Every endpoint
# that registers a serial builds a plan and runs it, so this — not a gateway call — is where its
# write happens; a check that only looked for `gateway.<write>` found nothing to order against
# `authz` and skipped the endpoints it most needed to cover.
# The endpoints that write nothing at all. `preview_serial_restore` reads a file and the site and
# reports what importing it would do; `export_serials` builds a document in memory and returns it.
READ_ONLY_ENDPOINTS = (
	"export_serials",
	"get_invoice_serial_status",
	"get_serial_assignments",
	# A file preview reads the file and reports the values it holds. It used to store a preview
	# session for `confirm_import` to apply, which made it a write; the window stages what it
	# reads into its columns now and saving is what writes, so the endpoint writes nothing.
	"prepare_import_preview",
	# The historical restore's own preview. It reads the file and the site and reports a *different*
	# plan from the ordinary one's — which is why it is an endpoint of its own — and it writes
	# nothing either.
	"preview_cancelled_serial_history",
	# The audit-record preview: the same file again, seen as the records it carries. It writes
	# nothing; the records it would place are placed by the endpoint beside it.
	"preview_restore_audit_records",
	"preview_serial_restore",
	# The transfer preview reads two invoices, the source's registrations and the target's rows,
	# and reports what moving them would do. It takes no lock, stores no session and writes
	# nothing at all: the only state it leaves behind is the token it returns, which is computed
	# and not kept.
	"preview_serial_transfer",
	# The report open defaults: a read of the caller's company list and the site's fiscal years.
	"report_filter_defaults",
)

ORCHESTRATION_WRITES = frozenset(
	{
		"run_rewrite",
	}
)

# The one read that is never needed *by* an authorization: `settings()` returns a single row of
# this app's own configuration, and the policy built from it (a serial's maximum length) changes
# nothing about whether the caller may act. Everything else an endpoint reads before authorizing
# is read in order to authorize — the invoice it names is the example — which is why only this one
# is checked for its position.
SETTINGS_METHODS = frozenset({"settings", "enabled_delimiters"})

AUTHZ_METHODS = frozenset(
	{
		"authorize",
		"require_company",
		"require_endpoint",
		"require_session_owner",
		# The two-invoice check, which a transfer uses instead of `authorize`. An endpoint that
		# calls it hands `core.authz` two invoices rather than one, and each is checked for what
		# it is: the target is written to, the source is read and is cancelled.
		"require_transfer_invoices",
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


def _functions(tree):
	"""Every function defined in the module, by name."""
	return {
		node.name: node
		for node in ast.walk(tree)
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
	}


def _writing_functions(tree):
	"""The names whose body writes something, directly or by calling another that does.

	An endpoint's write is not always in the endpoint: `apply_serial_restore` plans in `core/export`
	and writes through `gateway.insert_exported`, and the batch endpoints write through
	`core.orchestration.run_rewrite`. A check that only walked an endpoint's own body found no write
	in those and skipped them — which is how the endpoint with the most interesting write came to be
	the one that was not looked at. The two functions this docstring used to name, `confirm_import`
	and the session it stored, were removed in 0.3.0; the blind spot they illustrated is why this
	check exists.
	"""
	functions = _functions(tree)
	writers = set()

	def writes(name, seen):
		if name in writers:
			return True

		if name in seen or name not in functions:
			return False

		node = functions[name]
		seen = seen | {name}

		if _call_lines(node, "gateway", WRITE_METHODS) or _call_lines(
			node, "orchestration", ORCHESTRATION_WRITES
		):
			writers.add(name)

			return True

		for inner in ast.walk(node):
			if (
				isinstance(inner, ast.Call)
				and isinstance(inner.func, ast.Name)
				and writes(inner.func.id, seen)
			):
				writers.add(name)

				return True

		return False

	for name in functions:
		writes(name, set())

	return writers


def _authorizing_functions(tree):
	"""The names whose body asks `core.authz` something, directly or by calling another that does.

	The same narrowing as `_writing_functions`, on the other side of the check, and it was needed
	for the same reason: `preview_serial_transfer` and `apply_serial_transfer` authorize both ends
	of a transfer inside `_transfer_request`, because the check needs both documents as the
	database returned them and reading them in the endpoint would mean reading them twice. A check
	that only walked an endpoint's own body reported those two as endpoints that never ask — which
	is the failure this function exists to distinguish from the real one.
	"""
	functions = _functions(tree)
	authorizers = set()

	def authorizes(name, seen):
		if name in authorizers:
			return True

		if name in seen or name not in functions:
			return False

		node = functions[name]
		seen = seen | {name}

		if _call_lines(node, "authz", AUTHZ_METHODS):
			authorizers.add(name)

			return True

		for inner in ast.walk(node):
			if (
				isinstance(inner, ast.Call)
				and isinstance(inner.func, ast.Name)
				and authorizes(inner.func.id, seen)
			):
				authorizers.add(name)

				return True

		return False

	for name in functions:
		authorizes(name, set())

	return authorizers


def _authorization_lines(node, authorizers):
	"""Where a function authorizes: its own authz calls, and its calls to anything that does."""
	lines = _call_lines(node, "authz", AUTHZ_METHODS)

	for inner in ast.walk(node):
		if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
			if inner.func.id in authorizers:
				lines.append(inner.lineno)

	return sorted(lines)


def _settings_readers(tree):
	"""The names whose body reads the site's own settings, directly or by calling another that does.

	The same narrowing as `_authorizing_functions` and `_writing_functions`, for the same reason:
	`_limits` is where four endpoints read `Logistics Settings` for the serial-length policy, so a
	check that only walked an endpoint's own body would report those four as reading nothing.
	"""
	functions = _functions(tree)
	readers = set()

	def reads(name, seen):
		if name in readers:
			return True

		if name in seen or name not in functions:
			return False

		node = functions[name]
		seen = seen | {name}

		if _call_lines(node, "gateway", SETTINGS_METHODS):
			readers.add(name)

			return True

		for inner in ast.walk(node):
			if (
				isinstance(inner, ast.Call)
				and isinstance(inner.func, ast.Name)
				and reads(inner.func.id, seen)
			):
				readers.add(name)

				return True

		return False

	for name in functions:
		reads(name, set())

	return readers


def _settings_lines(node, readers):
	"""Where a function reads the site's settings: its own call, and its calls to anything that does."""
	lines = _call_lines(node, "gateway", SETTINGS_METHODS)

	for inner in ast.walk(node):
		if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
			if inner.func.id in readers:
				lines.append(inner.lineno)

	return sorted(lines)


def _own_write_lines(node):
	"""Where a function itself writes, without following anything it calls."""
	return sorted(
		_call_lines(node, "gateway", WRITE_METHODS) + _call_lines(node, "orchestration", ORCHESTRATION_WRITES)
	)


def _write_lines(node, writers):
	"""Where a function writes: its own write calls, and its calls to anything that writes.

	The line of a call to a writing helper is the line the write happens at, as far as the caller
	is concerned, which is what an ordering check needs.
	"""
	lines = _own_write_lines(node)

	for inner in ast.walk(node):
		if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
			if inner.func.id in writers:
				lines.append(inner.lineno)

	return sorted(lines)


class TheSerialNameReadIsBoundTest(UnitTestCase):
	"""I28 from the API's side: the fact exists for one decision and reaches nothing else.

	The approved boundary is narrow — cross-company serial **names** may be read, internally, to
	stop an insert that would collide — and the two ways it could widen are a response that
	carries the fact and a parameter that lets a caller ask about a name. Neither is visible in a
	behavioural test of one endpoint, and both are visible here.
	"""

	def endpoints(self):
		"""The module under test, parsed. Its own copy: the class below has its own for its own use."""
		path = schema_checks.APP_ROOT / "smartprocess_logistics" / "api" / "endpoints.py"
		source = path.read_text(encoding="utf-8")

		return path, source, ast.parse(source)

	def test_the_endpoint_layer_never_names_the_fact(self):
		"""A layer that cannot name it cannot return it, in any endpoint added later."""
		_path, source, _tree = self.endpoints()

		self.assertNotIn(
			"serial_names",
			source,
			"api/endpoints.py names the cross-company fact: it belongs in the gateway's read only",
		)

	def test_the_exception_is_where_it_is_claimed_to_be(self):
		"""One read, in the gateway; one use, in the planner. Named here so a second one shows up."""
		gateway = (schema_checks.APP_ROOT / "smartprocess_logistics" / "api" / "gateway.py").read_text(
			encoding="utf-8"
		)
		export = (schema_checks.APP_ROOT / "smartprocess_logistics" / "core" / "export.py").read_text(
			encoding="utf-8"
		)

		self.assertIn("serial_names", gateway)
		self.assertIn("serial_names", export)

		# The gateway reads it by name and by nothing else: a widened field list is the one change
		# that would turn "is this name taken" into "which company's item is this".
		read = gateway[gateway.index("serial_names=()") :]
		read = read[: read.index("export.World(")]
		self.assertIn('["name"]', read)

	def test_no_endpoint_takes_a_serial_name_to_look_up(self):
		"""A caller who could ask would have the arbitrary lookup the decision forbids."""
		_path, _source, tree = self.endpoints()
		forbidden = {"serial_name", "serial_names", "serial_no", "normalized_serial"}
		offenders = []

		for name, node in _endpoint_functions(tree).items():
			arguments = {argument.arg for argument in node.args.args + node.args.kwonlyargs}

			if arguments & forbidden:
				offenders.append((name, sorted(arguments & forbidden)))

		self.assertEqual([], offenders, f"an endpoint takes a serial to look up: {offenders}")


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
		authorizers = _authorizing_functions(tree)

		for name, node in _endpoint_functions(tree).items():
			with self.subTest(endpoint=name):
				self.assertTrue(
					_authorization_lines(node, authorizers),
					f"{name} never calls core.authz; a request it serves is unchecked",
				)

	def test_the_endpoints_that_write_are_exactly_those_that_are_not_read_only(self):
		"""The list of read-only endpoints is written down rather than inferred from a skip.

		The write check below can only order `authz` against a write it can find, so an endpoint
		whose write it cannot find passes by saying nothing. That skip is what this test removes
		the freedom from: an endpoint either writes and is checked, or is named here as one that
		does not, and a new endpoint has to be one or the other.
		"""
		_path, _source, tree = self.endpoints_module()
		writers = _writing_functions(tree)
		silent = tuple(
			sorted(
				name for name, node in _endpoint_functions(tree).items() if not _write_lines(node, writers)
			)
		)

		self.assertEqual(silent, READ_ONLY_ENDPOINTS)

	def test_no_endpoint_writes_before_it_is_authorized(self):
		_path, source, tree = self.endpoints_module()
		writers = _writing_functions(tree)
		authorizers = _authorizing_functions(tree)
		endpoints = _endpoint_functions(tree)

		for name, node in endpoints.items():
			authorized = _authorization_lines(node, authorizers)
			own = _own_write_lines(node)
			writes = _write_lines(node, writers)

			if not writes:
				continue

			with self.subTest(endpoint=name):
				self.assertTrue(authorized, f"{name} writes without asking authz")

				if own:
					self.assertLess(
						min(authorized),
						min(own),
						f"{name} writes through the gateway before it is authorized",
					)
				else:
					# Its only write is inside a helper, and that helper's one call authorizes and
					# writes — so "before" is at or before that line. The helper's own ordering is
					# what the loop below asserts.
					self.assertLessEqual(
						min(authorized),
						min(writes),
						f"{name} hands its write to something that does not authorize first",
					)

				self.assertIn(f"def {name}", source)

		# And every helper that writes is checked in its own right, which is what makes the
		# delegation above sound rather than a way for an unchecked write to hide one call deeper.
		# A helper that both authorizes and writes is exactly where an endpoint's act ends up when
		# the act is one thing — `_restore_export` is the case that made this necessary, and it
		# authorizes site-wide, authorizes every company and then writes, in that order.
		for name, node in _functions(tree).items():
			own = _own_write_lines(node)

			if not own or name in endpoints:
				continue

			with self.subTest(function=name):
				authorized = _authorization_lines(node, authorizers)

				self.assertTrue(authorized, f"{name} writes without asking authz")
				self.assertLess(
					min(authorized),
					min(own),
					f"{name} writes through the gateway before it is authorized",
				)

	def test_no_endpoint_reads_the_site_settings_before_it_is_authorized(self):
		"""The same ordering as the writes, on the one read that authorization never needs.

		Found in the phase-20 security review: four endpoints built their policy from
		`gateway.settings()` before `authz.authorize`, while the comment in each of them said
		nothing this app reads on behalf of a request is read before the request is allowed. No
		company data was disclosed — that row is this app's own configuration — but the stated rule
		and the code disagreed, and a rule that holds "except where it does not" is not one.

		Only settings are checked, and the line above says why: every other pre-authorization read
		is either the invoice the check is *about* or a name it was given.
		"""
		_path, _source, tree = self.endpoints_module()
		readers = _settings_readers(tree)

		self.assertTrue(readers, "no function reads the settings: this check would pass by silence")

		authorizers = _authorizing_functions(tree)
		endpoints = _endpoint_functions(tree)
		checked = 0

		for name, node in endpoints.items():
			settings = _settings_lines(node, readers)

			if not settings:
				continue

			checked += 1
			authorized = _authorization_lines(node, authorizers)
			# What the endpoint reads itself, as opposed to what a helper it calls reads on its
			# behalf. The two need different assertions for the reason the write check found the
			# same thing: a call to a helper that authorizes *and* reads cannot be earlier than
			# itself.
			own = _call_lines(node, "gateway", SETTINGS_METHODS)

			with self.subTest(endpoint=name):
				self.assertTrue(authorized, f"{name} reads the settings without asking authz")

				if own:
					self.assertLess(
						min(authorized),
						min(own),
						f"{name} reads the site's settings before it is authorized",
					)
				else:
					self.assertLessEqual(
						min(authorized),
						min(settings),
						f"{name} hands its settings read to something that does not authorize first",
					)

		self.assertGreaterEqual(checked, 4, f"only {checked} endpoints were checked")

		# And every helper that both reads the settings and authorizes, in its own right — which
		# is where `_transfer_request` lives: it authorizes both invoices and only then builds the
		# policy from the settings it reads. A helper that reads the settings without authorizing
		# is not checked here and does not need to be: nothing it reads is on behalf of a request
		# that has not been allowed, because its caller was checked above.
		for name, node in _functions(tree).items():
			own = _call_lines(node, "gateway", SETTINGS_METHODS)

			if not own or name in endpoints:
				continue

			authorized = _authorization_lines(node, authorizers)

			if not authorized:
				continue

			with self.subTest(function=name):
				self.assertLess(
					min(authorized),
					min(own),
					f"{name} reads the site's settings before it is authorized",
				)

	def test_every_endpoint_that_passes_an_invoice_to_authz_is_listed_as_invoice_bound(self):
		"""The two lists have to agree, and only this can check that.

		`core.authz.INVOICE_ENDPOINTS` decides which endpoints check the invoice they were given —
		including when there is none, which is the answer that must not differ between a missing
		name and a forbidden one. An endpoint that starts passing an invoice without being listed,
		or a name in the list that no endpoint matches, is a hole or a typo; both are invisible
		without reading the calls.
		"""
		_path, _source, tree = self.endpoints_module()
		listed = set(authz.INVOICE_ENDPOINTS)
		passing = set()

		for name, node in _endpoint_functions(tree).items():
			for inner in ast.walk(node):
				if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Attribute):
					continue

				if inner.func.attr != "authorize":
					continue

				if any(keyword.arg == "invoice" for keyword in inner.keywords):
					passing.add(name)

		self.assertEqual(
			sorted(passing),
			sorted(listed),
			"an endpoint that names an invoice and is not in INVOICE_ENDPOINTS is unchecked;"
			" a name in INVOICE_ENDPOINTS that no endpoint matches is a typo",
		)

	def test_every_endpoint_that_authorizes_two_invoices_is_listed_as_a_transfer(self):
		"""The two-invoice counterpart of the invoice-bound check above.

		`preview_serial_transfer` and `apply_serial_transfer` are the only endpoints about two
		invoices, so they are the only ones that can hand `core.authz` two — and a third one that
		started doing it without being listed would be an endpoint whose both ends were checked
		against the wrong rule, invisible without reading the calls.
		"""
		_path, _source, tree = self.endpoints_module()
		# The function that asks, found by the call it makes rather than by its name: it is the
		# one that reads both documents, because the check needs them as the database returned
		# them. Matching on a name would pass on a helper that merely mentions one.
		two_invoice = {
			name
			for name, node in _functions(tree).items()
			if _call_lines(node, "authz", frozenset({"require_transfer_invoices"}))
		}

		self.assertTrue(two_invoice, "no function asks authz about two invoices, so this is vacuous")

		passing = {
			name
			for name, node in _endpoint_functions(tree).items()
			if any(
				isinstance(inner, ast.Call)
				and isinstance(inner.func, ast.Name)
				and inner.func.id in two_invoice
				for inner in ast.walk(node)
			)
		}

		self.assertEqual(sorted(passing), sorted(authz.TRANSFER_ENDPOINTS))

	def test_the_check_fires_on_a_write_that_comes_first(self):
		# The check has to be able to fail, or it is a comment with a test around it.
		tree = ast.parse(
			"@frappe.whitelist()\n"
			"def do_thing(x):\n"
			"    gateway.insert_assignments(x)\n"
			"    authz.require_endpoint(principal, 'do_thing')\n"
		)
		node = _endpoint_functions(tree)["do_thing"]

		authorized = _call_lines(node, "authz", AUTHZ_METHODS)
		writes = _call_lines(node, "gateway", WRITE_METHODS)

		self.assertTrue(writes)
		self.assertGreater(min(authorized), min(writes))

	def test_the_check_follows_a_write_into_a_helper(self):
		"""The blind spot itself: an endpoint whose write is one call deeper than its own body."""
		tree = ast.parse(
			"def store(gateway, payload):\n"
			"    gateway.insert_assignments(payload)\n"
			"\n"
			"@frappe.whitelist()\n"
			"def do_thing(x):\n"
			"    store(gateway, x)\n"
			"    authz.require_endpoint(principal, 'do_thing')\n"
		)
		writers = _writing_functions(tree)
		node = _endpoint_functions(tree)["do_thing"]

		self.assertIn("store", writers)

		writes = _write_lines(node, writers)

		self.assertTrue(writes, "a write through a helper is still a write")
		self.assertGreater(
			min(_call_lines(node, "authz", AUTHZ_METHODS)),
			min(writes),
			"and it is ordered against the authorization like any other",
		)

	def test_the_check_fires_on_an_endpoint_that_never_asks(self):
		tree = ast.parse("@frappe.whitelist()\ndef do_thing(x):\n    return gateway.read_rows(x)\n")
		node = _endpoint_functions(tree)["do_thing"]

		self.assertEqual(_call_lines(node, "authz", AUTHZ_METHODS), [])
