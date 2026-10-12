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

SETTINGS = "Logistics Settings"


class FakeThrow(Exception):
	"""What `frappe.throw` raises, standing in for `frappe.ValidationError`."""


class SettingsDoc:
	"""A stand-in for the `Logistics Settings` Single.

	`installed_on` is a property on purpose: the tests that matter most are about how many times
	it is written and whether it is ever written twice, and a plain attribute cannot tell those
	apart from a read.
	"""

	def __init__(self, installed_on=None, delimiters=None, item_markers=None):
		self._installed_on = installed_on
		self.delimiters = [dict(row) for row in (delimiters or [])]
		self.item_markers = [dict(row) for row in (item_markers or [])]
		self.saves = 0
		self.installed_on_writes = []

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
		self.saves += 1

	def as_dict(self):
		return {
			"doctype": SETTINGS,
			"installed_on": self.installed_on,
			"delimiters": [dict(row) for row in self.delimiters],
			"item_markers": [dict(row) for row in self.item_markers],
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
		self.throws = []
		self.messages = []
		self.errors = []

	@property
	def db(self):
		return self._db

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
			return name in self.roles

		raise AssertionError(f"the fake does not implement exists({doctype!r})")

	def count(self, doctype):
		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not have a table for {doctype!r}")

		if doctype in self.count_overrides:
			return self.count_overrides[doctype]

		return len(self.tables.get(doctype, []))

	def get_list(self, doctype, **kwargs):
		self._reject_ignore_permissions(f"get_list({doctype})", kwargs)
		self.get_list_calls.append({"doctype": doctype, **kwargs})

		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not have a table for {doctype!r}")

		rows = list(self.tables.get(doctype, []))
		offset = 0 if self.paging_ignored else kwargs.get("offset", 0)
		limit = kwargs.get("limit")

		page = rows[offset:] if limit is None else rows[offset : offset + limit]

		return [dict(row) for row in page]

	def get_single(self, doctype):
		if doctype != SETTINGS:
			raise AssertionError(f"the fake only implements the {SETTINGS} Single")
		if doctype not in self.doctypes:
			raise AssertionError(f"the fake does not have {doctype!r} registered")

		return self.settings

	def get_doc(self, values):
		return InsertableDoc(self, values)

	def sql(self, query, params=None, **kwargs):
		self._reject_ignore_permissions("db.sql", kwargs)
		self.sql_calls.append({"query": query, "params": params})

		state = self.indexes.get((params["table"], params["constraint"]))

		if not state:
			return []

		columns, unique = state

		# `NON_UNIQUE` is 0 for a unique index, 1 for a plain one — the real column's sense.
		return [(column, 0 if unique else 1) for column in columns]

	def add_unique(self, doctype, fields, constraint_name):
		self.added_unique.append((doctype, tuple(fields), constraint_name))
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
				"local": types.SimpleNamespace(site=bench.site),
			}
		)

		# Not implemented on purpose. Both skip permission checks, and nothing in this app may
		# use them; a failure here names the caller instead of quietly returning rows.
		def forbidden(name):
			def raise_(*args, **kwargs):
				raise AssertionError(f"{name} skips permission checks and this app must not use it")

			return raise_

		frappe.get_all = forbidden("frappe.get_all")

		utils = types.ModuleType("frappe.utils")
		utils.now = lambda: bench.now
		frappe.utils = utils

		bench._db = types.SimpleNamespace(
			exists=bench.exists,
			count=bench.count,
			sql=bench.sql,
			add_unique=bench.add_unique,
			get_list=bench.get_list,
			get_all=forbidden("frappe.db.get_all"),
		)
		frappe.db = bench._db

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
