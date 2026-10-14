"""A stand-in for the Frappe runtime, so the lifecycle hooks can be tested off a bench.

`install.py` and the three `setup` modules import `frappe` at module level, so nothing in them
can be imported on a machine without a bench. This module puts a small, recording object into
`sys.modules["frappe"]` for the duration of a test, which is what lets those four modules be
exercised — including the paths that would otherwise first run on a real site.

It is deliberately narrow. It implements the calls those four modules make, and raises for
anything else, because a test double that returns `None` for a call nobody thought about lets a
test pass on behaviour the real framework does not have.

Two rules are enforced rather than merely recorded, so a violation fails whichever test happens
to touch it:

- `ignore_permissions` on a read or an insert raises. This app never bypasses permissions.
- `frappe.get_all` and `frappe.db.get_all` raise. They skip permission checks; `get_list` is the
  permission-checking form and is the only list API this app may use.

What it is not
--------------
It is not Frappe. `information_schema` reflection, `add_unique`'s commit before its ALTER, the
`@atomic` wrapper around `after_migrate`, DocType permission evaluation and the order
`installer.install_app` calls things in are all absent here. What these tests pin is this app's
own logic and its ordering; the framework's half of the contract was read from the source in
`erpnext-backend-1` and is recorded in the module docstrings rather than re-tested here.
"""

import os
import sys
import tempfile
import traceback
import types
from datetime import datetime

from smartprocess_logistics.core.defaults import DEFAULT_NEXT_ROW_COMMAND
from smartprocess_logistics.core.normalization import NORMALIZATION_VERSION

SETTINGS = "Logistics Settings"


class FakeThrow(Exception):
	"""What `frappe.throw` raises, standing in for `frappe.ValidationError`."""


class QueryDeadlockError(Exception):
	"""Stands in for `frappe.QueryDeadlockError`, which is raised from the driver's error code.

	The cleanup job catches this class, so the fake runtime has to carry it: an `except` clause
	whose class is missing from the stand-in turns one failure into a different one.
	"""


class QueryTimeoutError(Exception):
	"""Stands in for `frappe.QueryTimeoutError`, the other way a lock wait ends."""


class SettingsDoc:
	"""A stand-in for the `Logistics Settings` Single.

	`installed_on` is a property on purpose: the tests that matter most are about how many times
	it is written and whether it is ever written twice, and a plain attribute cannot tell those
	apart from a read.
	"""

	def __init__(
		self,
		installed_on=None,
		delimiters=None,
		item_markers=None,
		normalization_version=NORMALIZATION_VERSION,
		next_row_command=None,
	):
		self._installed_on = installed_on
		self.delimiters = [dict(row) for row in (delimiters or [])]
		self.item_markers = [dict(row) for row in (item_markers or [])]
		# The scan window's next-column command, and the same reasoning as the version above: the
		# shipped default is what a bench models, because that is what a row carries once the
		# column exists — Frappe inserts a Single from the DocType's defaults, and the migrate
		# that adds the column is followed by the seed that fills it. `next_row_command=None` is
		# the one other state: a settings row that predates the column, which is what the migrate
		# actually meets and what the seed exists for.
		self.next_row_command = DEFAULT_NEXT_ROW_COMMAND if next_row_command is None else next_row_command
		# The shipped default, not a placeholder: `Logistics Settings.normalization_version`
		# defaults to the code's own rule set, and `schema_checks` refuses a definition where the
		# two disagree. So a bench built here models a site that is up to date, which is what
		# makes "a migrate changes nothing" a meaningful assertion. A test that wants the drifted
		# state passes a lower number and says so.
		self.normalization_version = int(normalization_version)
		# `saves` counts completed saves. `save_failures` makes the next that many raise first,
		# which is how the install on SmartProcessERP behaved when Frappe refused the row.
		self.saves = 0
		self.save_failures = 0
		self.installed_on_writes = []
		# What a fetch would return: a save that raises writes nothing, so the instance has to
		# be able to go back to what was last stored. Without this the fake would keep values a
		# refused save never persisted, and an idempotency test would pass for the wrong reason.
		self.persisted = self._snapshot()

	@property
	def installed_on(self):
		return self._installed_on

	@installed_on.setter
	def installed_on(self, value):
		self._installed_on = value
		self.installed_on_writes.append(value)

	def get(self, field):
		return getattr(self, field, None)

	def append(self, table, row):
		getattr(self, table).append(dict(row))

	def save(self):
		if self.save_failures:
			self.save_failures -= 1
			self._roll_back()
			raise FakeThrow("simulated: a required field was blank")

		self.saves += 1
		self.persisted = self._snapshot()

	def _snapshot(self):
		return {
			"installed_on": self._installed_on,
			"delimiters": [dict(row) for row in self.delimiters],
			"item_markers": [dict(row) for row in self.item_markers],
			"normalization_version": self.normalization_version,
			"next_row_command": self.next_row_command,
		}

	def _roll_back(self):
		"""What Frappe leaves behind when validation refuses a save: the stored state, unchanged."""
		self._installed_on = self.persisted["installed_on"]
		self.delimiters = [dict(row) for row in self.persisted["delimiters"]]
		self.item_markers = [dict(row) for row in self.persisted["item_markers"]]
		self.normalization_version = self.persisted["normalization_version"]
		self.next_row_command = self.persisted["next_row_command"]

	def as_dict(self):
		return {
			"doctype": SETTINGS,
			"installed_on": self.installed_on,
			"delimiters": [dict(row) for row in self.delimiters],
			"item_markers": [dict(row) for row in self.item_markers],
			"normalization_version": self.normalization_version,
		}


class InsertableDoc:
	"""What `frappe.get_doc({...})` returns: something that can be inserted."""

	def __init__(self, bench, values):
		self._bench = bench
		self._values = dict(values)

	def insert(self, **kwargs):
		self._bench._reject_ignore_permissions("Document.insert", kwargs)
		self._bench.inserted.append(self._values)

		if self._values.get("doctype") == "Role":
			self._bench.roles.add(self._values["role_name"])

		return self


class FakeBench:
	"""The recorded state of one fake runtime, and the assertions it will not let code pass."""

	def __init__(self, site="test-site", now="2026-10-12 10:00:00.000000", site_path=None):
		self.site = site
		self.now = now
		self._own_site_path = site_path is None
		self.site_path = site_path or tempfile.mkdtemp(prefix="fake-bench-")

		# DocTypes that exist, and the rows each one holds.
		self.doctypes = set()
		self.tables = {}
		self.count_overrides = {}
		self.paging_ignored = False

		self.roles = set()
		# (table, index name) -> (columns, unique). What `information_schema.STATISTICS` would
		# report for the two tables this app owns.
		self.indexes = {}
		self.settings = None

		# Everything the code under test did, for the test to assert on.
		self.inserted = []
		self.added_unique = []
		self.get_list_calls = []
		self.sql_calls = []
		self.deletions = []
		self.throws = []
		self.messages = []
		self.errors = []
		self.rollbacks = 0
		# A server where a constraint name is taken in another schema: `add_unique` returns and
		# leaves nothing behind. The only way to notice is to read the catalogue back.
		self.silent_add_unique = False
		# `(doctype, name)` pairs `delete_doc` refuses, which is how a linked document is modelled.
		self.delete_failures = set()

	@property
	def db(self):
		return self._db

	@property
	def now_datetime(self):
		"""`frappe.utils.now_datetime()`: the same clock as `now()`, as a datetime.

		The two are not the same thing in the framework either — `now()` answers with the string
		a Datetime field stores, `now_datetime()` with a `datetime` — and code that compares an
		expiry has to get the second one. Handing it the first raised TypeError on this site.
		"""
		return datetime.strptime(self.now, "%Y-%m-%d %H:%M:%S.%f")

	def _reject_ignore_permissions(self, where, kwargs):
		if kwargs.get("ignore_permissions"):
			raise AssertionError(f"{where} was called with ignore_permissions=True")

	def add_doctype(self, name, rows=None):
		"""Register a DocType, and optionally its table."""
		self.doctypes.add(name)

		if rows is not None:
			self.tables[name] = [dict(row) for row in rows]

		return self

	def add_index(self, doctype, name, columns, unique=True):
		"""Register an index that is already on the table.

		`unique=False` models the case the constraint guard has to catch: a plain index whose
		name is the one the app wants for a unique constraint.
		"""
		self.indexes[(f"tab{doctype}", name)] = (tuple(columns), bool(unique))

		return self

	def add_settings(self, **kwargs):
		self.add_doctype(SETTINGS)
		self.settings = SettingsDoc(**kwargs)

		return self

	# --- the surface the app under test actually uses -----------------------------------

	def exists(self, doctype, name=None):
		if doctype == "DocType":
			return name in self.doctypes
		if doctype == "Role":
			# Roles are reachable two ways: the set `ensure_roles` inserts into, and a registered
			# table a test seeded. A site can have either, and `after_uninstall` must see both.
			if name in self.roles:
				return True

			if not isinstance(name, dict):
				return any(row.get("name") == name for row in self.tables.get("Role", []))

			return any(self._matches(row, name) for row in self.tables.get("Role", []))

		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not implement exists({doctype!r})")

		rows = self.tables.get(doctype, [])

		if isinstance(name, dict):
			return any(self._matches(row, name) for row in rows)

		return any(row.get("name") == name for row in rows)

	def count(self, doctype, filters=None):
		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not have a table for {doctype!r}")

		if doctype in self.count_overrides and not filters:
			return self.count_overrides[doctype]

		rows = self.tables.get(doctype, [])

		if filters:
			return sum(1 for row in rows if self._matches(row, filters))

		return len(rows)

	def _matches(self, row, filters):
		"""Whether one row satisfies a filter — a value, or `{field: ["in", (...)]}`."""
		for field, condition in filters.items():
			if isinstance(condition, (list, tuple)) and len(condition) == 2 and condition[0] == "in":
				if row.get(field) not in set(condition[1]):
					return False
			elif row.get(field) != condition:
				return False

		return True

	def get_list(self, doctype, **kwargs):
		self._reject_ignore_permissions(f"get_list({doctype})", kwargs)
		self.get_list_calls.append({"doctype": doctype, **kwargs})

		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not have a table for {doctype!r}")

		rows = list(self.tables.get(doctype, []))
		filters = kwargs.get("filters")

		if filters:
			rows = [row for row in rows if self._matches(row, filters)]

		offset = 0 if self.paging_ignored else kwargs.get("offset", 0)
		limit = kwargs.get("limit")

		page = rows[offset:] if limit is None else rows[offset : offset + limit]

		if kwargs.get("pluck"):
			return [row.get(kwargs["pluck"]) for row in page]

		return [dict(row) for row in page]

	def delete_doc(self, doctype, name, **kwargs):
		self._reject_ignore_permissions(f"delete_doc({doctype})", kwargs)

		if (doctype, name) in self.delete_failures:
			raise FakeThrow(f"simulated: {doctype} {name} is linked and cannot be deleted")

		self.deletions.append((doctype, name))

		if doctype in self.tables:
			self.tables[doctype] = [row for row in self.tables[doctype] if row.get("name") != name]

		if doctype == "Role":
			self.roles.discard(name)

		return name

	def get_single(self, doctype):
		if doctype != SETTINGS:
			raise AssertionError(f"the fake only implements the {SETTINGS} Single")
		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not have {doctype!r} registered")

		return self.settings

	def get_doc(self, values):
		return InsertableDoc(self, values)

	def rollback(self):
		"""What the framework does when a request or a job ends in a failure it caught."""
		self.rollbacks += 1

		return None

	def sql(self, query, params=None, **kwargs):
		"""Two kinds of statement, told apart by their parameters.

		A statement whose parameters are a **dict** is the one read of `information_schema` the
		constraint guard makes, and it is answered from the registered indexes. Everything else —
		the archive's own statements — is recorded and answered with the next canned result, if a
		test queued one. The fake does not speak SQL, and no test here pretends it does: what these
		tests pin is that the statements are parameterized, which rows are projected into them and
		in which order, and what the code decides. That MariaDB carries the statement out correctly
		is what the rehearsal in `deployment/lab/` measures on a real site.
		"""
		self._reject_ignore_permissions("db.sql", kwargs)
		self.sql_calls.append({"query": query, "params": params})

		if isinstance(params, dict):
			state = self.indexes.get((params["table"], params["constraint"]))

			if not state:
				return []

			columns, unique = state

			# `NON_UNIQUE` is 0 for a unique index, 1 for a plain one — the real column's sense.
			return [(column, 0 if unique else 1) for column in columns]

	def add_unique(self, doctype, fields, constraint_name):
		self.added_unique.append((doctype, tuple(fields), constraint_name))

		# `silent_add_unique` reproduces what the framework does when the name is already taken in
		# *another schema* of the same server: it returns without altering anything. The database
		# here therefore still has no such index, which is what the app's look-back reads.
		if self.silent_add_unique:
			return

		self.indexes[(f"tab{doctype}", constraint_name)] = (tuple(fields), True)

	def get_site_path(self, *joins):
		return os.path.join(self.site_path, *joins)

	def throw(self, message, title=None, **kwargs):
		self.throws.append(message)
		raise FakeThrow(message)

	def msgprint(self, message, **kwargs):
		self.messages.append(message)

	def log_error(self, message=None, title=None, **kwargs):
		self.errors.append({"message": message, "title": title})


class FakeLifecycle:
	"""Installs the fake into `sys.modules` for the length of a test, and takes it out after.

	Used from `setUp`/`tearDown` rather than as a context manager so the tests keep working on
	the Python version this project declares as its floor.
	"""

	MODULES = (
		"frappe",
		"frappe.utils",
		"smartprocess_logistics.install",
		"smartprocess_logistics.hooks",
		"smartprocess_logistics.setup",
		"smartprocess_logistics.setup.roles",
		"smartprocess_logistics.setup.constraints",
		"smartprocess_logistics.setup.seeds",
	)

	def __init__(self, bench=None):
		self.bench = bench or FakeBench()
		self._saved = {}

	def start(self):
		self._saved = {name: sys.modules.get(name) for name in self.MODULES}

		for name in self.MODULES:
			sys.modules.pop(name, None)

		sys.modules["frappe"] = self._build()

		return self.bench

	def stop(self):
		for name in self.MODULES:
			sys.modules.pop(name, None)

		for name, module in self._saved.items():
			if module is not None:
				sys.modules[name] = module

		if self.bench._own_site_path:
			_remove_tree(self.bench.site_path)

	def _build(self):
		bench = self.bench

		frappe = types.ModuleType("frappe")
		frappe.__dict__.update(
			{
				"_": lambda message, *args, **kwargs: message,
				"ValidationError": FakeThrow,
				"throw": bench.throw,
				"msgprint": bench.msgprint,
				"log_error": bench.log_error,
				# The real one formats the exception being handled, so that is what this returns:
				# a test can then assert on the cause a hook logged, not on a placeholder.
				"get_traceback": lambda *args, **kwargs: traceback.format_exc(),
				"bold": lambda value: f"<b>{value}</b>",
				"get_single": bench.get_single,
				"get_doc": bench.get_doc,
				"get_list": bench.get_list,
				"get_site_path": bench.get_site_path,
				"delete_doc": bench.delete_doc,
				"local": types.SimpleNamespace(site=bench.site),
				"session": types.SimpleNamespace(user="Administrator"),
				"conf": types.SimpleNamespace(db_name="fake_db"),
			}
		)

		# Not implemented on purpose. Both skip permission checks, and nothing in this app may
		# use them; a failure here names the caller instead of quietly returning rows.
		def forbidden(name):
			def raise_(*args, **kwargs):
				raise AssertionError(f"{name} skips permission checks and this app must not use it")

			return raise_

		frappe.get_all = forbidden("frappe.get_all")
		frappe.QueryDeadlockError = QueryDeadlockError
		frappe.QueryTimeoutError = QueryTimeoutError

		utils = types.ModuleType("frappe.utils")
		utils.now = lambda: bench.now
		utils.now_datetime = lambda: bench.now_datetime
		frappe.utils = utils

		bench._db = types.SimpleNamespace(
			exists=bench.exists,
			count=bench.count,
			sql=bench.sql,
			add_unique=bench.add_unique,
			get_list=bench.get_list,
			get_all=forbidden("frappe.db.get_all"),
			rollback=bench.rollback,
		)
		frappe.db = bench._db

		return frappe


class FakeReadRuntime(FakeLifecycle):
	"""A second fake runtime, for the reads `api/gateway.py` makes.

	`FakeLifecycle` forbids `frappe.get_all` outright, and it is right to: nothing in the lifecycle
	or setup modules may use an API that skips permission checks. The gateway's exporting and
	planning reads do use it, and for a reason that survives this stage — `Logistics Serial` has no
	company column, so no permission condition a framework could apply would restrict it, and the
	app's own company policy has to be visible in the filters the gateway passes.

	That is what this runtime exists to record. `get_all_calls` is the evidence, and the rows a test
	plants are what comes back, so a test asserts on the data an export would hand an operator
	rather than on the source text that produced it.

	`frappe.permissions` is injected as a module of its own because the gateway imports
	`get_user_permissions` from it by name. The two ways a principal can be restricted — the user's
	`Company` user permissions, and the site's strict setting — are fields on this object, so a test
	states a site's policy instead of reading one from a database that is not there.
	"""

	MODULES = FakeLifecycle.MODULES + (
		"frappe.permissions",
		"smartprocess_logistics.api",
		"smartprocess_logistics.api.gateway",
		"smartprocess_logistics.permissions",
	)

	def __init__(self, bench=None):
		super().__init__(bench)
		self.get_all_calls = []
		self.user = "Administrator"
		self.roles = ("System Manager",)
		# user -> the companies their `Company` user permissions allow. A user absent from this
		# mapping has no such permission, which is `None` — every company — and not "none".
		self.company_permissions = {}
		self.strict_companies = False

	# --- what the gateway calls ----------------------------------------------------------

	def get_all(self, doctype, **kwargs):
		self.get_all_calls.append({"doctype": doctype, **kwargs})

		if doctype not in self.bench.doctypes:
			raise AssertionError(f"the fake does not have a table for {doctype!r}")

		rows = [dict(row) for row in self.bench.tables.get(doctype, [])]
		filters = kwargs.get("filters")

		if filters:
			rows = [row for row in rows if self.bench._matches(row, filters)]

		fields = kwargs.get("fields")

		if fields:
			rows = [{field: row.get(field) for field in fields} for row in rows]

		order_by = kwargs.get("order_by") or ""

		if order_by.endswith(" asc"):
			column = order_by[:-4].strip()
			rows.sort(key=lambda row: row.get(column) or "")

		return rows

	def get_roles(self, user):
		return list(self.roles)

	def get_user_permissions(self, user):
		"""`frappe.permissions.get_user_permissions`'s shape: a doctype -> rows mapping."""
		if user not in self.company_permissions:
			return {}

		return {"Company": [{"doc": company} for company in self.company_permissions[user]]}

	def get_single_value(self, doctype, field):
		if field == "apply_strict_user_permissions":
			return self.strict_companies

		return None

	def escape(self, value):
		"""Enough of `frappe.db.escape` to state a condition: quotes doubled, then wrapped."""
		return "'" + str(value).replace("'", "''") + "'"

	def calls_to(self, doctype):
		"""Every recorded read of one DocType, in order."""
		return [call for call in self.get_all_calls if call["doctype"] == doctype]

	# --- the fake module -----------------------------------------------------------------

	def _build(self):
		frappe = super()._build()

		frappe.get_all = self.get_all
		frappe.get_roles = self.get_roles
		frappe.session = types.SimpleNamespace(user=self.user)
		frappe.flags = types.SimpleNamespace(in_import=False)
		frappe.PermissionError = PermissionError
		frappe.db.escape = self.escape
		frappe.db.get_single_value = self.get_single_value

		permissions = types.ModuleType("frappe.permissions")
		permissions.get_user_permissions = self.get_user_permissions

		# `api/gateway.py` imports both submodules **by name** (`from frappe.utils import now`,
		# `from frappe.permissions import get_user_permissions`), and a from-import of `a.b` needs
		# `a.b` in `sys.modules` or `a` to be a package. The lifecycle modules reach their helpers
		# through the attribute instead, which is why the base runtime gets away with neither.
		frappe.__path__ = []
		sys.modules["frappe.utils"] = frappe.utils
		sys.modules["frappe.permissions"] = permissions

		return frappe


def _remove_tree(path):
	if not os.path.isdir(path):
		return

	for root, directories, files in os.walk(path, topdown=False):
		for name in files:
			os.remove(os.path.join(root, name))
		for name in directories:
			os.rmdir(os.path.join(root, name))

	os.rmdir(path)
