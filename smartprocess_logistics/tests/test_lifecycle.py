"""The install, migrate and uninstall entry points, exercised against a fake runtime.

What these tests are for
------------------------
`installer.install_app` runs `sync_for`, commits the app into `Installed Applications`, and only
then calls `after_install`; it never calls `after_migrate`. That order has three consequences
this module pins down:

1. A fresh install must create the unique constraints itself, because the migration that would
   otherwise create them never runs. A constraint declared only in `after_migrate` is absent on
   a site that has never been migrated.
2. A failure inside `after_install` leaves a site that reports the app as installed with the
   work half done. `after_migrate` is what repairs it, so it must re-assert all three steps.
3. `installed_on` is the app's own marker that the install finished, so it is written last and
   never rewritten. A recorded value is never overwritten, which is what lets an administrator
   delete a seeded delimiter and have it stay deleted.

And one about uninstall: `remove_app` deletes every record linked to this app's modules, so the
hook must not refuse merely because records exist. It proceeds, and it stops only when a
verified copy of those records cannot be written.

What these tests are not
------------------------
The fake runtime is not Frappe; see `fake_bench`. `information_schema` reflection, the commit
inside `add_unique`, the `@atomic` wrapper around `after_migrate` and DocType permission
evaluation are not modelled. The framework's half of the contract was read from the source in
`erpnext-backend-1`.
"""

import importlib
import json
import os
import pathlib

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS, DEFAULT_ITEM_MARKERS
from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.tests.fake_bench import FakeBench, FakeLifecycle, FakeThrow

SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"
SESSION = "Logistics Import Session"
SETTINGS = "Logistics Settings"

APP_TABLES = (SERIAL, ASSIGNMENT, SESSION)

# The tables the uninstall copy reads. `Logistics Import Session` is not among them: its rows
# are transient and it holds the hidden `token` and `payload`, so copying it would put a live
# capability into a file while preserving nothing worth restoring.
COPIED_DOCTYPES = (SERIAL, ASSIGNMENT)

# A value that must never reach the copy. Distinctive so the assertion cannot pass by accident.
SESSION_TOKEN = "token-that-must-not-be-copied"

VIEWER = "Logistics Serial Viewer"
EDITOR = "Logistics Serial Editor"
MANAGER = "Logistics Serial Manager"

RECORDED_AT = "2026-01-01 00:00:00.000000"

LIFECYCLE_FILES = (
	"smartprocess_logistics/install.py",
	"smartprocess_logistics/hooks.py",
	"smartprocess_logistics/setup/roles.py",
	"smartprocess_logistics/setup/constraints.py",
	"smartprocess_logistics/setup/seeds.py",
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def source(relative):
	return (REPO_ROOT / relative).read_text(encoding="utf-8")


def serial_row(name, item_code="ITEM-1", normalized=None, creation="2026-01-01 00:00:00.000000"):
	return {
		"creation": creation,
		"docstatus": 0,
		"item_code": item_code,
		"name": name,
		"normalized_serial": normalized or name.lower(),
		"owner": "Administrator",
	}


def assignment_row(name, sales_invoice="SINV-1", normalized="aa-1"):
	return {
		"creation": "2026-01-01 00:00:00.000000",
		"docstatus": 0,
		"name": name,
		"normalized_serial": normalized,
		"owner": "Administrator",
		"sales_invoice": sales_invoice,
	}


def session_row(name, token=SESSION_TOKEN):
	return {
		"creation": "2026-01-01 00:00:00.000000",
		"docstatus": 0,
		"name": name,
		"owner": "Administrator",
		"payload": '{"rows": []}',
		"token": token,
	}


def bare_bench(**settings):
	"""A site that has the app's tables and nothing in them."""
	bench = FakeBench().add_settings(**settings)

	for doctype in APP_TABLES:
		bench.add_doctype(doctype, rows=[])

	return bench


def populated_bench(**settings):
	"""A site that has the app's tables with rows in them."""
	bench = FakeBench().add_settings(**settings)
	bench.add_doctype(
		SERIAL,
		rows=[
			serial_row("SER-1", item_code="ITEM-1", normalized="aa-1"),
			serial_row("SER-2", item_code="ITEM-2", normalized="aa-2", creation="2026-01-02 00:00:00.000000"),
		],
	)
	bench.add_doctype(ASSIGNMENT, rows=[assignment_row("ASS-1")])
	bench.add_doctype(SESSION, rows=[session_row("SESS-1")])

	return bench


class LifecycleTestCase(UnitTestCase):
	"""Starts a fake runtime per test and takes it out again afterwards."""

	def setUp(self):
		self._lifecycles = []
		self.bench = None

	def tearDown(self):
		for lifecycle in reversed(self._lifecycles):
			lifecycle.stop()

	def given(self, bench):
		lifecycle = FakeLifecycle(bench)
		self._lifecycles.append(lifecycle)
		self.bench = lifecycle.start()

		return self.bench

	def load_install(self):
		return importlib.import_module("smartprocess_logistics.install")

	def load_setup(self):
		return (
			importlib.import_module("smartprocess_logistics.setup.roles"),
			importlib.import_module("smartprocess_logistics.setup.constraints"),
			importlib.import_module("smartprocess_logistics.setup.seeds"),
		)

	def inserted_roles(self):
		return [doc["role_name"] for doc in self.bench.inserted if doc.get("doctype") == "Role"]

	def added_constraint_names(self):
		return [name for _, _, name in self.bench.added_unique]

	def preservation_directory(self):
		return os.path.join(self.bench.site_path, "private", "files")

	def preservation_files(self):
		directory = self.preservation_directory()

		if not os.path.isdir(directory):
			return []

		return sorted(
			os.path.join(directory, name)
			for name in os.listdir(directory)
			if name.startswith("smartprocess_logistics-uninstall-")
		)

	def read_copy(self):
		files = self.preservation_files()
		self.assertEqual(len(files), 1, f"expected exactly one copy, found {files}")

		with open(files[0], encoding="utf-8") as handle:
			return json.load(handle)

	def durable_state(self):
		"""Everything the two entry points are supposed to leave behind."""
		bench = self.bench

		return (
			sorted(bench.roles),
			[tuple(entry) for entry in bench.added_unique],
			sorted(bench.indexes.items()),
			len(bench.settings.delimiters),
			len(bench.settings.item_markers),
			bench.settings.installed_on,
			len(bench.settings.installed_on_writes),
			bench.settings.saves,
		)


class AfterInstallTest(LifecycleTestCase):
	def test_creates_the_three_roles(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()

		self.assertEqual(self.inserted_roles(), [VIEWER, EDITOR, MANAGER])

	def test_creates_the_unique_constraints(self):
		"""A fresh install never runs `after_migrate`, so the constraints must be created here."""
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()

		self.assertEqual(
			self.bench.added_unique,
			[
				(SERIAL, ("item_code", "normalized_serial"), "unique_item_serial"),
				(ASSIGNMENT, ("sales_invoice", "normalized_serial"), "unique_invoice_serial"),
			],
		)

	def test_seeds_the_defaults_and_records_the_install_timestamp(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()

		self.assertEqual(len(self.bench.settings.delimiters), len(DEFAULT_DELIMITERS))
		self.assertEqual(len(self.bench.settings.item_markers), len(DEFAULT_ITEM_MARKERS))
		self.assertEqual(self.bench.settings.installed_on, self.bench.now)
		self.assertEqual(self.bench.settings.saves, 1)

	def test_a_role_that_already_exists_is_left_alone(self):
		bench = bare_bench()
		bench.roles.add(VIEWER)
		self.given(bench)
		install = self.load_install()

		install.after_install()

		self.assertEqual(self.inserted_roles(), [EDITOR, MANAGER])

	def test_settings_a_failed_install_already_wrote_are_not_seeded_twice(self):
		"""The half-installed case: the tables are filled but the timestamp was never written."""
		bench = bare_bench()
		bench.settings.delimiters.append({"delimiter": "|", "enabled": 1, "label": "typed by hand"})
		self.given(bench)
		install = self.load_install()

		install.after_install()

		self.assertEqual(len(bench.settings.delimiters), 1)
		self.assertEqual(bench.settings.delimiters[0]["label"], "typed by hand")
		self.assertEqual(len(bench.settings.item_markers), len(DEFAULT_ITEM_MARKERS))

	def test_installing_twice_changes_nothing_the_second_time(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()
		once = self.durable_state()
		install.after_install()

		self.assertEqual(self.durable_state(), once)

	def test_installing_twice_records_the_timestamp_once(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()
		install.after_install()

		self.assertEqual(len(self.bench.settings.installed_on_writes), 1)
		self.assertEqual(self.bench.settings.saves, 1)


class AfterMigrateTest(LifecycleTestCase):
	def test_repairs_a_site_whose_install_stopped_halfway(self):
		"""The state `install_app` can leave behind: the app is installed, the work is not done."""
		bench = bare_bench()
		bench.roles.add(VIEWER)
		bench.settings.delimiters.append({"delimiter": "|", "enabled": 1, "label": "typed by hand"})
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(sorted(bench.roles), sorted([VIEWER, EDITOR, MANAGER]))
		self.assertEqual(
			self.added_constraint_names(), ["unique_item_serial", "unique_invoice_serial"]
		)
		self.assertEqual(len(bench.settings.item_markers), len(DEFAULT_ITEM_MARKERS))
		self.assertEqual(bench.settings.installed_on, bench.now)

	def test_re_creates_a_constraint_a_restored_dump_did_not_have(self):
		bench = bare_bench()
		bench.add_index(SERIAL, "unique_item_serial", ["item_code", "normalized_serial"])
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(self.added_constraint_names(), ["unique_invoice_serial"])

	def test_a_constraint_that_already_covers_the_right_columns_is_left_alone(self):
		bench = bare_bench()
		bench.add_index(SERIAL, "unique_item_serial", ["item_code", "normalized_serial"])
		bench.add_index(
			ASSIGNMENT, "unique_invoice_serial", ["sales_invoice", "normalized_serial"]
		)
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(self.bench.added_unique, [])

	def test_a_finished_installation_is_not_seeded_again(self):
		"""Once the timestamp is set, a delimiter an administrator deleted stays deleted."""
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(bench.settings.delimiters, [])
		self.assertEqual(bench.settings.item_markers, [])

	def test_migrating_twice_changes_nothing_the_second_time(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_migrate()
		once = self.durable_state()
		install.after_migrate()

		self.assertEqual(self.durable_state(), once)

	def test_install_and_migrate_reach_the_same_state(self):
		"""The two entry points differ in when they run, not in what they leave behind."""
		self.given(bare_bench())
		install = self.load_install()
		install.after_install()
		after_install = self.durable_state()

		self.given(bare_bench())
		install = self.load_install()
		install.after_migrate()
		after_migrate = self.durable_state()

		self.assertEqual(after_install, after_migrate)


class InstalledOnTest(LifecycleTestCase):
	def test_a_recorded_timestamp_is_never_overwritten(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()

		install.after_install()
		install.after_migrate()

		self.assertEqual(bench.settings.installed_on, RECORDED_AT)
		self.assertEqual(bench.settings.installed_on_writes, [])
		self.assertEqual(bench.settings.saves, 0)

	def test_the_timestamp_is_not_the_first_thing_written(self):
		"""It marks a finished install, so it is written last: a failure above leaves it empty."""
		bench = bare_bench()
		self.given(bench)
		install = self.load_install()

		install.after_install()

		self.assertEqual(len(bench.settings.installed_on_writes), 1)
		self.assertEqual(bench.settings.saves, 1)

	def test_a_failed_install_leaves_the_timestamp_empty_and_is_repaired_by_a_migrate(self):
		bench = bare_bench()
		# The constraint step throws, which is where an install can stop: the roles are written,
		# the seeds are not, and `installed_on` is therefore still empty.
		bench.add_index(SERIAL, "unique_item_serial", ["item_code"])
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_install()

		self.assertIsNone(bench.settings.installed_on)
		self.assertEqual(bench.settings.saves, 0)

		# The next migrate sees an empty timestamp and finishes the job.
		bench.indexes.pop((f"tab{SERIAL}", "unique_item_serial"))
		install.after_migrate()

		self.assertEqual(bench.settings.installed_on, bench.now)
		self.assertEqual(len(bench.settings.delimiters), len(DEFAULT_DELIMITERS))


class ConstraintGuardTest(LifecycleTestCase):
	def test_a_constraint_name_used_by_a_different_rule_stops_the_work(self):
		bench = bare_bench()
		bench.add_index(SERIAL, "unique_item_serial", ["item_code"])
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_migrate()

		self.assertEqual(bench.added_unique, [])
		self.assertIn("unique_item_serial", bench.throws[0])
		self.assertIn("item_code", bench.throws[0])

	def test_a_name_taken_by_a_plain_index_stops_the_work(self):
		"""`add_unique` looks in `TABLE_CONSTRAINTS`; the guard reads `STATISTICS`. A plain
		index with the right name would look enforced while enforcing nothing."""
		bench = bare_bench()
		bench.add_index(
			SERIAL, "unique_item_serial", ["item_code", "normalized_serial"], unique=False
		)
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_migrate()

		self.assertEqual(bench.added_unique, [])
		self.assertIn("not a unique constraint", bench.throws[0])

	def test_the_guard_reads_only_the_catalogue_of_this_apps_own_tables(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(len(self.bench.sql_calls), len(install.constraints.UNIQUE_CONSTRAINTS))

		for call in self.bench.sql_calls:
			self.assertIn("information_schema.STATISTICS", call["query"])
			self.assertIn(call["params"]["table"], (f"tab{SERIAL}", f"tab{ASSIGNMENT}"))
			self.assertIn(
				call["params"]["constraint"], ("unique_item_serial", "unique_invoice_serial")
			)


class BlastRadiusTest(LifecycleTestCase):
	def test_nothing_outside_this_apps_own_records_is_written(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()
		install.after_migrate()

		for doctype, _, _ in self.bench.added_unique:
			self.assertIn(doctype, (SERIAL, ASSIGNMENT))

		for doc in self.bench.inserted:
			self.assertEqual(doc["doctype"], "Role")
			self.assertIn(doc["role_name"], (VIEWER, EDITOR, MANAGER))

	def test_the_lifecycle_reads_nothing_during_install_or_migrate(self):
		self.given(populated_bench())
		install = self.load_install()

		install.after_install()
		install.after_migrate()

		self.assertEqual(self.bench.get_list_calls, [])

	def test_no_read_or_insert_bypasses_permissions(self):
		self.given(populated_bench())
		install = self.load_install()

		install.after_install()
		install.after_migrate()
		install.before_uninstall()

		for call in self.bench.get_list_calls:
			self.assertNotIn("ignore_permissions", call)


class BeforeUninstallTest(LifecycleTestCase):
	def test_a_site_with_no_records_is_not_blocked_and_writes_nothing(self):
		self.given(bare_bench())
		install = self.load_install()

		install.before_uninstall()

		self.assertEqual(self.bench.throws, [])
		self.assertEqual(self.bench.messages, [])
		self.assertEqual(self.preservation_files(), [])

	def test_records_do_not_block_the_uninstall(self):
		"""The rule is recoverability, not permission: rows are copied, not refused."""
		self.given(populated_bench())
		install = self.load_install()

		install.before_uninstall()

		self.assertEqual(self.bench.throws, [])
		self.assertEqual(len(self.preservation_files()), 1)

	def test_the_copy_holds_every_row_of_every_protected_table(self):
		self.given(populated_bench())
		install = self.load_install()

		install.after_install()
		install.before_uninstall()

		copy = self.read_copy()

		self.assertEqual(copy["app"], "smartprocess_logistics")
		self.assertEqual(copy["site"], self.bench.site)
		self.assertEqual(copy["written_at"], self.bench.now)
		self.assertEqual([row["name"] for row in copy["doctypes"][SERIAL]], ["SER-1", "SER-2"])
		self.assertEqual([row["name"] for row in copy["doctypes"][ASSIGNMENT]], ["ASS-1"])
		self.assertEqual(copy["singles"][SETTINGS]["installed_on"], self.bench.now)
		self.assertEqual(len(copy["singles"][SETTINGS]["delimiters"]), len(DEFAULT_DELIMITERS))
		self.assertEqual(len(copy["singles"][SETTINGS]["item_markers"]), len(DEFAULT_ITEM_MARKERS))

	def test_the_transient_session_rows_are_not_copied(self):
		"""A session carries the hidden `token`; a copy would preserve it and recover nothing."""
		self.given(populated_bench())
		install = self.load_install()

		install.before_uninstall()

		copy = self.read_copy()

		self.assertNotIn(SESSION, copy["doctypes"])
		self.assertNotIn(SESSION_TOKEN, json.dumps(copy))
		self.assertNotIn("payload", json.dumps(copy))

	def test_only_the_tables_that_hold_rows_are_copied(self):
		"""An empty table carries nothing to restore, so it is left out rather than written empty."""
		bench = FakeBench().add_settings()
		bench.add_doctype(SERIAL, rows=[serial_row("SER-1")])
		bench.add_doctype(ASSIGNMENT, rows=[])
		self.given(bench)
		install = self.load_install()

		install.before_uninstall()

		copy = self.read_copy()

		self.assertEqual(sorted(copy["doctypes"]), [SERIAL])
		self.assertNotIn(ASSIGNMENT, copy["doctypes"])
		self.assertNotIn(SESSION, copy["doctypes"])
		self.assertEqual(sorted(copy["singles"]), [SETTINGS])

	def test_the_copy_is_complete_across_page_boundaries(self):
		rows = [serial_row(f"SER-{index:04d}") for index in range(1200)]
		bench = FakeBench().add_settings()
		bench.add_doctype(SERIAL, rows=rows)
		bench.add_doctype(ASSIGNMENT, rows=[])
		self.given(bench)
		install = self.load_install()

		install.before_uninstall()

		copy = self.read_copy()

		self.assertEqual([row["name"] for row in copy["doctypes"][SERIAL]], [r["name"] for r in rows])
		self.assertEqual(
			[call["offset"] for call in self.bench.get_list_calls if call["doctype"] == SERIAL],
			[0, install.EXPORT_PAGE_SIZE, 2 * install.EXPORT_PAGE_SIZE],
		)

	def test_the_uninstall_is_stopped_when_the_copy_cannot_be_written(self):
		"""The one case that blocks: continuing would destroy records with nothing to restore."""
		bench = populated_bench()
		self.given(bench)
		install = self.load_install()

		# A file where the private files directory has to go, which is what a full disk, a
		# read-only mount or a misconfigured site looks like from here.
		os.makedirs(os.path.join(bench.site_path, "private"), exist_ok=True)
		with open(os.path.join(bench.site_path, "private", "files"), "w", encoding="utf-8") as handle:
			handle.write("not a directory")

		with self.assertRaises(FakeThrow):
			install.before_uninstall()

		self.assertEqual(len(bench.errors), 1)
		self.assertIn("could not be written", bench.throws[0])
		self.assertTrue(str(bench.errors[0]["message"]).startswith("Traceback"))
		self.assertEqual(bench.inserted, [])
		self.assertEqual(bench.added_unique, [])

	def test_the_uninstall_is_stopped_when_the_read_is_short_of_the_count(self):
		bench = populated_bench()
		bench.count_overrides[SERIAL] = 5
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.before_uninstall()

		self.assertEqual(len(bench.errors), 1)
		self.assertEqual(self.preservation_files(), [])

	def test_the_uninstall_is_stopped_when_paging_repeats_instead_of_advancing(self):
		"""A query that ignored its offset must fail, not loop, and not pass as complete."""
		bench = populated_bench()
		bench.paging_ignored = True
		bench.count_overrides[SERIAL] = 5
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.before_uninstall()

		self.assertEqual(len(bench.errors), 1)

	def test_the_uninstall_writes_no_database_row(self):
		self.given(populated_bench())
		install = self.load_install()

		install.before_uninstall()

		self.assertEqual(self.bench.inserted, [])
		self.assertEqual(self.bench.added_unique, [])
		self.assertEqual(self.bench.settings.saves, 0)

	def test_the_uninstall_reads_only_the_protected_tables(self):
		self.given(populated_bench())
		install = self.load_install()

		install.before_uninstall()

		self.assertTrue(self.bench.get_list_calls)
		for call in self.bench.get_list_calls:
			self.assertIn(call["doctype"], COPIED_DOCTYPES)

	def test_a_read_that_returns_a_duplicate_row_is_refused(self):
		"""A skipped row and a repeated row keep the count equal; only duplicates reveal it."""
		bench = populated_bench()
		bench.paging_ignored = True
		bench.count_overrides[SERIAL] = 2 * len(bench.tables[SERIAL])
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.before_uninstall()

		self.assertEqual(len(bench.errors), 1)
		self.assertIn("duplicate", str(bench.errors[0]["message"]))
		self.assertEqual(self.preservation_files(), [])

	def test_a_copy_that_fails_its_own_check_is_not_left_on_disk(self):
		"""A file that failed verification is not a copy, and is worse than none: it is removed."""
		bench = populated_bench()
		self.given(bench)
		install = self.load_install()

		def always_fails(path, document):
			raise install.PreservationError("simulated: the file does not match what was prepared")

		install._verify_written_copy = always_fails

		with self.assertRaises(FakeThrow):
			install.before_uninstall()

		self.assertEqual(self.preservation_files(), [])
		self.assertEqual(len(bench.errors), 1)

	def test_the_copy_is_written_under_the_site_not_into_the_repository(self):
		self.given(populated_bench())
		install = self.load_install()

		install.before_uninstall()

		written = self.preservation_files()[0]

		self.assertTrue(written.startswith(self.bench.site_path))
		self.assertIn(os.path.join("private", "files"), written)


class PreservationFileTest(LifecycleTestCase):
	"""The verification step on its own: a file that is not the prepared copy must be refused."""

	def setUp(self):
		super().setUp()
		self.given(bare_bench())
		self.install = self.load_install()
		self.directory = self.bench.site_path

	def write(self, content):
		path = os.path.join(self.directory, "copy.json")

		with open(path, "w", encoding="utf-8") as handle:
			handle.write(content)

		return path

	def test_a_faithful_file_passes(self):
		document = json.dumps({"doctypes": {SERIAL: [{"name": "SER-1"}]}})

		self.install._verify_written_copy(self.write(document), document)

	def test_a_file_that_is_not_json_is_refused(self):
		path = self.write("{ this is not json")

		with self.assertRaises(self.install.PreservationError) as raised:
			self.install._verify_written_copy(path, "{}")

		self.assertIn("not readable JSON", str(raised.exception))

	def test_a_truncated_file_is_refused(self):
		document = json.dumps({"doctypes": {SERIAL: [{"name": "SER-1"}, {"name": "SER-2"}]}})
		path = self.write(json.dumps({"doctypes": {SERIAL: [{"name": "SER-1"}]}}))

		with self.assertRaises(self.install.PreservationError) as raised:
			self.install._verify_written_copy(path, document)

		self.assertIn("does not match", str(raised.exception))

	def test_a_file_missing_a_table_is_refused(self):
		document = json.dumps({"doctypes": {SERIAL: [], ASSIGNMENT: []}})
		path = self.write(json.dumps({"doctypes": {SERIAL: []}}))

		with self.assertRaises(self.install.PreservationError) as raised:
			self.install._verify_written_copy(path, document)

		message = str(raised.exception)

		self.assertIn("does not match", message)
		self.assertIn(ASSIGNMENT, message)


class SourceRuleTest(UnitTestCase):
	"""Static checks. Cheap, and they hold for every line, not just the executed ones."""

	def test_no_lifecycle_module_bypasses_permissions(self):
		for relative in LIFECYCLE_FILES:
			with self.subTest(file=relative):
				self.assertNotIn("ignore_permissions=", source(relative))

	def test_no_lifecycle_module_lists_with_the_unchecked_api(self):
		"""`get_all` does not check permissions and this app never uses it."""
		for relative in LIFECYCLE_FILES:
			with self.subTest(file=relative):
				text = source(relative)
				self.assertNotIn("frappe.get_all(", text)
				self.assertNotIn("db.get_all(", text)

	def test_no_lifecycle_module_writes_with_sql(self):
		"""`db.sql` reads `information_schema` once. It never writes."""
		for relative in LIFECYCLE_FILES:
			with self.subTest(file=relative):
				text = source(relative).lower()
				for statement in (
					"insert into",
					"update tab",
					"delete from",
					"drop table",
					"alter table",
					"truncate ",
				):
					self.assertNotIn(statement, text)

	def test_the_uninstall_hook_is_not_registered_anywhere(self):
		self.assertNotIn("\nbefore_uninstall =", source("smartprocess_logistics/hooks.py"))
		self.assertIn("def before_uninstall():", source("smartprocess_logistics/install.py"))


class HooksRegistrationTest(UnitTestCase):
	"""`hooks.py` imports nothing, so it can be read on a machine with no bench."""

	def hooks(self):
		return importlib.import_module("smartprocess_logistics.hooks")

	def test_install_and_migrate_are_registered(self):
		hooks = self.hooks()

		self.assertEqual(hooks.after_install, "smartprocess_logistics.install.after_install")
		self.assertEqual(hooks.after_migrate, "smartprocess_logistics.install.after_migrate")

	def test_uninstall_is_not_registered(self):
		hooks = self.hooks()

		self.assertFalse(hasattr(hooks, "before_uninstall"))
		self.assertIn("before_uninstall", source("smartprocess_logistics/hooks.py"))

	def test_no_scheduler_events_are_registered(self):
		hooks = self.hooks()

		self.assertFalse(hasattr(hooks, "scheduler_events"))
		self.assertNotIn("\nscheduler_events", source("smartprocess_logistics/hooks.py"))

	def test_no_doctype_class_or_whitelisted_method_is_overridden(self):
		hooks = self.hooks()

		for attribute in ("override_doctype_class", "override_whitelisted_methods", "doc_events"):
			with self.subTest(attribute=attribute):
				self.assertFalse(hasattr(hooks, attribute))

	def test_the_app_requires_frappe_and_erpnext(self):
		self.assertEqual(self.hooks().required_apps, ["frappe", "erpnext"])
