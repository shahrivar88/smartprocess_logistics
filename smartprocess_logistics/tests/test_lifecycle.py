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
import importlib.util
import pathlib
import unittest
from datetime import datetime, timedelta

from smartprocess_logistics.core import authz
from smartprocess_logistics.core.defaults import (
	DEFAULT_DELIMITERS,
	DEFAULT_ITEM_MARKERS,
	DEFAULT_NEXT_ROW_COMMAND,
)
from smartprocess_logistics.core.normalization import NORMALIZATION_VERSION
from smartprocess_logistics.core.session import APPLIED, PREPARED, PURGE_BATCH, RETENTION_DAYS
from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.tests.fake_bench import FakeBench, FakeLifecycle, FakeThrow

SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"
SESSION = "Logistics Import Session"
SETTINGS = "Logistics Settings"

APP_TABLES = (SERIAL, ASSIGNMENT, SESSION)

# A value that must never reach the archive. Distinctive so the assertion cannot pass by accident.
SESSION_TOKEN = "token-that-must-not-be-copied"

VIEWER = "Logistics Serial Viewer"
EDITOR = "Logistics Serial Editor"
MANAGER = "Logistics Serial Manager"
# The fourth role, and the only one that grants nothing on a DocType: it exists so that one
# endpoint can require it in addition to a Manager level. `core.authz.ENDPOINT_ROLES`.
ARCHIVIST = "Logistics Serial Archivist"

RECORDED_AT = "2026-01-01 00:00:00.000000"

# The fake runtime's clock, as a `datetime`, for the tests that compare instants.
CLEANUP_CLOCK = datetime(2026, 10, 12, 10, 0)

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
	def test_creates_the_four_roles(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()

		self.assertEqual(self.inserted_roles(), [VIEWER, EDITOR, MANAGER, ARCHIVIST])

	def test_the_roles_it_creates_are_the_roles_the_app_checks_for(self):
		"""`setup.roles` reads the names from `core.authz` rather than spelling them again, because
		that module is what decides with them: a second spelling is a role the app requires and
		never creates, and the site would be told to grant a role that does not exist."""
		self.given(bare_bench())
		roles, _constraints, _seeds = self.load_setup()

		self.assertEqual(
			tuple(roles.ROLES),
			(authz.ROLE_VIEWER, authz.ROLE_EDITOR, authz.ROLE_MANAGER, authz.ROLE_ARCHIVIST),
		)

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

		self.assertEqual(self.inserted_roles(), [EDITOR, MANAGER, ARCHIVIST])

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
	def test_the_migrate_that_adds_the_command_column_gives_the_site_the_default(self):
		"""The one moment the seed exists for: a settings row that predates the field.

		The column arrives empty on a site that already has a settings row — Frappe does not write
		a field's default into rows that already exist — and the command is required, with a rule
		that refuses an empty one. So without this step the migrate would leave a row its own
		validation refuses to save, on the very next change an administrator makes.
		"""
		bench = bare_bench(installed_on=RECORDED_AT, next_row_command=None)
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(bench.settings.next_row_command, DEFAULT_NEXT_ROW_COMMAND)

	def test_a_site_that_chose_its_own_command_keeps_it(self):
		"""A migrate is not a way to put the default back over a decision somebody made."""
		bench = bare_bench(installed_on=RECORDED_AT, next_row_command="§§")
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(bench.settings.next_row_command, "§§")
		self.assertEqual(bench.settings.saves, 0, "nothing needed writing")

	def test_repairs_a_site_whose_install_stopped_halfway(self):
		"""The state `install_app` can leave behind: the app is installed, the work is not done."""
		bench = bare_bench()
		bench.roles.add(VIEWER)
		bench.settings.delimiters.append({"delimiter": "|", "enabled": 1, "label": "typed by hand"})
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(sorted(bench.roles), sorted([VIEWER, EDITOR, MANAGER, ARCHIVIST]))
		self.assertEqual(self.added_constraint_names(), ["unique_item_serial", "unique_invoice_serial"])
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
		bench.add_index(ASSIGNMENT, "unique_invoice_serial", ["sales_invoice", "normalized_serial"])
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


class NormalizationVersionTest(LifecycleTestCase):
	"""The one settings value that follows the code instead of the install.

	`normalization_version` was written once and then went stale: the field described the rule
	set the app applied, the code moved from 1 to 2, and the settings screen went on saying 1.
	It is repaired on every install and migrate, and the repair has to be narrow — it may not
	touch the timestamp, the seeded tables, or anything a serial holds.
	"""

	def test_a_row_from_an_older_rule_set_is_brought_up_to_date(self):
		bench = bare_bench(installed_on=RECORDED_AT, normalization_version=NORMALIZATION_VERSION - 1)
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(bench.settings.normalization_version, NORMALIZATION_VERSION)

	def test_the_repair_leaves_the_timestamp_and_the_seeds_alone(self):
		bench = bare_bench(
			installed_on=RECORDED_AT,
			delimiters=[{"delimiter": "|", "enabled": 1, "label": "typed by hand"}],
			normalization_version=NORMALIZATION_VERSION - 1,
		)
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(bench.settings.installed_on, RECORDED_AT)
		self.assertEqual(bench.settings.installed_on_writes, [])
		self.assertEqual([row["label"] for row in bench.settings.delimiters], ["typed by hand"])

	def test_a_row_that_already_agrees_is_not_written(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(bench.settings.saves, 0)

	def test_the_timestamp_is_written_when_the_single_row_already_exists(self):
		"""The real order: `installer.init_singles` inserts the row before `after_install` runs.

		That makes the seed's write an update to an existing document, which is what
		`set_only_once` refused. The fake always starts with the row already stored, so this test
		pins the ordering the field must tolerate rather than the value.
		"""
		bench = bare_bench()
		self.assertIsNotNone(bench.settings.persisted, "the row exists before the seed runs")
		self.given(bench)
		install = self.load_install()

		install.after_install()

		self.assertEqual(bench.settings.installed_on, bench.now)
		self.assertEqual(len(bench.settings.installed_on_writes), 1)
		self.assertEqual(bench.settings.saves, 1)

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


class SeedIdempotencyTest(LifecycleTestCase):
	"""What the seed does on a second run after an install that stopped inside `save()`.

	The attempt on SmartProcessERP failed exactly there: `settings.save()` was refused because a
	shipped delimiter was blank, and Frappe validates before it writes. Measured on the site
	afterwards, the child tables held nothing and `installed_on` was empty, so a retry has to
	append the defaults once and only once.
	"""

	def test_a_refused_save_persists_nothing(self):
		"""The state the real site was left in: empty tables and an empty timestamp."""
		bench = bare_bench()
		bench.settings.save_failures = 1
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_install()

		self.assertEqual(bench.settings.delimiters, [])
		self.assertEqual(bench.settings.item_markers, [])
		self.assertIsNone(bench.settings.installed_on)
		self.assertEqual(bench.settings.saves, 0)
		self.assertEqual(bench.settings.save_failures, 0)

	def test_a_retry_writes_one_copy_of_every_default(self):
		bench = bare_bench()
		bench.settings.save_failures = 1
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_install()

		install.after_install()

		self.assertEqual(
			[row["delimiter"] for row in bench.settings.delimiters],
			[stored for _, stored in DEFAULT_DELIMITERS],
		)
		self.assertEqual(
			[row["pattern"] for row in bench.settings.item_markers],
			[pattern for _, pattern in DEFAULT_ITEM_MARKERS],
		)
		self.assertEqual(bench.settings.saves, 1)

	def test_the_retry_does_not_double_a_default(self):
		bench = bare_bench()
		bench.settings.save_failures = 1
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_install()

		install.after_install()
		install.after_install()

		stored = [row["delimiter"] for row in bench.settings.delimiters]

		self.assertEqual(len(stored), len(DEFAULT_DELIMITERS))
		self.assertEqual(len(set(stored)), len(stored))
		self.assertEqual(bench.settings.saves, 1)

	def test_a_seed_that_already_succeeded_is_not_repeated(self):
		self.given(bare_bench())
		install = self.load_install()

		install.after_install()
		first = (len(self.bench.settings.delimiters), len(self.bench.settings.item_markers))
		install.after_migrate()

		self.assertEqual((len(self.bench.settings.delimiters), len(self.bench.settings.item_markers)), first)
		self.assertEqual(self.bench.settings.saves, 1)


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
		bench.add_index(SERIAL, "unique_item_serial", ["item_code", "normalized_serial"], unique=False)
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

		# The reads this test is about are the ones with a dictionary of parameters: the guard,
		# and the look-back that confirms what the guard just created is there. `add_unique` can
		# return without altering anything when the name exists in another schema of the same
		# server, and only a read can tell. The archive also runs statements during a migrate,
		# and they are counted in `test_archive` rather than here.
		catalogue_reads = [call for call in self.bench.sql_calls if isinstance(call["params"], dict)]

		self.assertEqual(len(catalogue_reads), 2 * len(install.constraints.UNIQUE_CONSTRAINTS))

		for call in catalogue_reads:
			self.assertIn("information_schema.STATISTICS", call["query"])
			self.assertIn(call["params"]["table"], (f"tab{SERIAL}", f"tab{ASSIGNMENT}"))
			self.assertIn(call["params"]["constraint"], ("unique_item_serial", "unique_invoice_serial"))


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
			self.assertIn(doc["role_name"], (VIEWER, EDITOR, MANAGER, ARCHIVIST))

	def test_the_lifecycle_reads_nothing_during_install_or_migrate(self):
		self.given(populated_bench())
		install = self.load_install()

		install.after_install()
		install.after_migrate()

		self.assertEqual(self.bench.get_list_calls, [])

	def test_no_read_or_insert_bypasses_permissions(self):
		bench = populated_bench()
		# The removal hook reads its own archive back to verify what it wrote; these are those
		self.given(bench)
		install = self.load_install()

		install.after_install()
		install.after_migrate()

		for call in self.bench.get_list_calls:
			self.assertNotIn("ignore_permissions", call)

		for doctype, _fields, _name in self.bench.added_unique:
			self.assertIn(doctype, (SERIAL, ASSIGNMENT))


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

	def test_nothing_is_registered_for_the_moment_before_a_removal(self):
		"""Registered on purpose: it is what stands between a removal and the app's records."""
		self.assertNotIn("\nbefore_uninstall =", source("smartprocess_logistics/hooks.py"))
		self.assertNotIn("def before_uninstall(", source("smartprocess_logistics/install.py"))


HAS_COMMITMENT_COMPOSITION = importlib.util.find_spec("smartprocess_logistics.hooks_commitment") is not None


class HooksRegistrationTest(UnitTestCase):
	"""`hooks.py` imports only the pure composition module, so it needs no bench."""

	def hooks(self):
		return importlib.import_module("smartprocess_logistics.hooks")

	def test_install_and_migrate_are_registered(self):
		hooks = self.hooks()

		self.assertEqual(hooks.after_install, "smartprocess_logistics.install.after_install")
		self.assertEqual(hooks.after_migrate, "smartprocess_logistics.install.after_migrate")

	def test_the_uninstall_cleanup_is_registered(self):
		hooks = self.hooks()

		self.assertEqual(hooks.after_uninstall, "smartprocess_logistics.install.after_uninstall")

	def test_no_hook_is_registered_for_the_moment_before_a_removal(self):
		self.assertFalse(getattr(self.hooks(), "before_uninstall", None))

	def test_the_only_scheduler_event_is_the_session_sweep(self):
		"""One job, and it is named here so a second one cannot arrive unnoticed.

		`Logistics Import Session` is transient by design: a preview nobody confirmed stays until
		something removes it, and an applied one keeps its payload — every serial the preview
		accepted — for as long as its row exists. The sweep is the only thing that removes either,
		so it is the one thing this app schedules. Anything else scheduled here would be new
		behaviour on somebody's site and is refused by this test until somebody says otherwise.
		"""
		hooks = self.hooks()

		self.assertEqual(
			hooks.scheduler_events,
			{"daily": ["smartprocess_logistics.install.purge_stale_sessions"]},
		)

	def test_no_doctype_class_or_whitelisted_method_is_overridden(self):
		hooks = self.hooks()

		for attribute in ("override_doctype_class", "override_whitelisted_methods"):
			with self.subTest(attribute=attribute):
				self.assertFalse(hasattr(hooks, attribute))

	@unittest.skipUnless(
		HAS_COMMITMENT_COMPOSITION,
		"the development commitment composition is not part of this build",
	)
	def test_doc_events_are_the_guards_and_the_managed_lifecycle(self):
		"""The validation handlers guard; the two Sales Invoice events are the commitment.

		`tests/test_integrity.py` owns the rules; this test owns the registration shape. Every
		other document may register `validate` and nothing else. The Sales Invoice's two lifecycle
		events are the one place this app writes an ERPNext document, and they are named here so a
		new hook cannot arrive unnoticed. The approved release subset has no
		`hooks_commitment.py`, so this test is skipped there and the approved composition test
		below is the one that runs.
		"""
		doc_events = getattr(self.hooks(), "doc_events", {})

		self.assertEqual(
			set(doc_events),
			{
				"Sales Invoice",
				"Delivery Note",
				"Item",
				"Purchase Order",
				"Purchase Invoice",
				"Purchase Receipt",
				"Stock Entry",
				"Stock Reservation Entry",
				"Logistics Serial Assignment",
			},
		)
		self.assertEqual(
			set(doc_events["Sales Invoice"]),
			{"validate", "before_submit", "before_cancel"},
		)
		for doctype in ("Delivery Note", "Stock Entry", "Stock Reservation Entry"):
			with self.subTest(doctype=doctype):
				self.assertEqual(set(doc_events[doctype]), {"validate"})
		for doctype in ("Purchase Order", "Purchase Invoice", "Purchase Receipt"):
			with self.subTest(doctype=doctype):
				self.assertIn("before_submit", doc_events[doctype])

	def test_the_approved_composition_has_no_paused_hooks(self):
		"""The release composition is importable and complete without the commitment module."""
		from smartprocess_logistics import hooks_composition

		doc_events = hooks_composition.approved_doc_events()
		doctype_js = hooks_composition.approved_doctype_js()

		self.assertEqual(set(doc_events["Sales Invoice"]), {"validate"})
		self.assertEqual(
			doc_events["Delivery Note"]["validate"],
			[
				"smartprocess_logistics.integrity.validate_delivery_note",
				"smartprocess_logistics.warehouse.sales.validate_delivery_note",
			],
		)
		self.assertNotIn("public/js/sales_invoice_committed_stock.js", doctype_js["Sales Invoice"])
		self.assertEqual(
			set(doc_events),
			{
				"Sales Invoice",
				"Delivery Note",
				"Item",
				"Purchase Order",
				"Purchase Invoice",
				"Purchase Receipt",
				"Stock Entry",
				"Stock Reservation Entry",
				"Logistics Serial Assignment",
			},
		)
		self.assertEqual(
			set(doc_events["Logistics Serial Assignment"]),
			{"validate", "on_trash"},
		)

	def test_the_app_requires_frappe_and_erpnext(self):
		self.assertEqual(self.hooks().required_apps, ["frappe", "erpnext"])


class FakeSessionStore:
	"""A gateway that holds sessions in a list, so the sweep can be tested without a database."""

	def __init__(self, sessions=(), contended=False):
		self.sessions = list(sessions)
		self.contended = contended
		self.expire_calls = []
		self.purge_calls = []
		self.reads = 0

	def stored_sessions(self):
		self.reads += 1

		# The sweep reads the table once for both steps, so a contended store fails on that read:
		# a run that dies there has done nothing at all, which is the outcome being tested.
		if self.contended and self.reads > 0:
			from smartprocess_logistics.tests.fake_bench import QueryDeadlockError

			raise QueryDeadlockError("simulated: the row changed under the sweep")

		return tuple(self.sessions)

	def expire_sessions(self, names, summary):
		self.expire_calls.append((tuple(names), summary))

		return tuple(names)

	def purge_sessions(self, names):
		self.purge_calls.append(tuple(names))

		return tuple(names)


def stored_session(name, status, *, expires_at=None, applied_at=None):
	from smartprocess_logistics.core.session import ImportSession

	return ImportSession(
		name=name,
		token=f"token-{name}",
		status=status,
		expires_at=expires_at,
		applied_at=applied_at,
	)


class PurgeStaleSessionsTest(LifecycleTestCase):
	"""The daily sweep: what it expires, what it removes, and what it leaves alone.

	The two steps answer two questions. A preview nobody confirmed is **expired** — the row stays,
	because a reader should see "expired" rather than "prepared for ever". A session that finished
	is **removed**, but only after `core.session.RETENTION_DAYS`, because its row is the only
	record of what an import applied and carries the payload it applied.

	The sweep is deliberately not part of the install or the migrate: housekeeping must not be
	able to fail either of them, and it is bounded so that a site which fell behind catches up
	over several runs rather than in one long transaction.
	"""

	def test_a_stale_preview_is_expired_and_an_old_one_is_removed(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()
		store = FakeSessionStore(
			sessions=(
				stored_session(
					"LIS-old", APPLIED, applied_at=CLEANUP_CLOCK - timedelta(days=RETENTION_DAYS + 1)
				),
				stored_session("LIS-stale", PREPARED, expires_at=CLEANUP_CLOCK - timedelta(minutes=5)),
				stored_session("LIS-fresh", PREPARED, expires_at=CLEANUP_CLOCK + timedelta(minutes=5)),
			)
		)

		outcome = install.purge_stale_sessions(gateway=store)

		self.assertEqual(outcome["expired"], ("LIS-stale",))
		self.assertEqual(outcome["purged"], ("LIS-old",))
		self.assertFalse(outcome["contended"])
		self.assertEqual([names for names, _summary in store.expire_calls], [("LIS-stale",)])

	def test_an_applied_session_inside_the_retention_is_kept(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()
		store = FakeSessionStore(
			sessions=(stored_session("LIS-yesterday", APPLIED, applied_at=CLEANUP_CLOCK - timedelta(days=1)),)
		)

		outcome = install.purge_stale_sessions(gateway=store)

		self.assertEqual(outcome["purged"], ())
		self.assertEqual(store.purge_calls, [])

	def test_a_run_that_loses_a_race_does_nothing_and_says_so(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()
		store = FakeSessionStore(
			sessions=(
				stored_session("LIS-stale", PREPARED, expires_at=CLEANUP_CLOCK - timedelta(minutes=5)),
			),
			contended=True,
		)

		outcome = install.purge_stale_sessions(gateway=store)

		self.assertTrue(outcome["contended"])
		self.assertEqual(outcome["expired"], ())
		self.assertEqual(outcome["purged"], ())
		self.assertEqual(store.purge_calls, [])
		self.assertEqual(bench.rollbacks, 1)

	def test_the_batch_is_bounded(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()
		store = FakeSessionStore(
			sessions=tuple(
				stored_session(
					f"LIS-{index:04d}", APPLIED, applied_at=CLEANUP_CLOCK - timedelta(days=RETENTION_DAYS + 1)
				)
				for index in range(PURGE_BATCH + 25)
			)
		)

		outcome = install.purge_stale_sessions(gateway=store)

		self.assertEqual(len(outcome["purged"]), PURGE_BATCH)
		self.assertEqual(outcome["purged"][0], "LIS-0000")

	def test_an_empty_table_is_not_written_to_at_all(self):
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()
		store = FakeSessionStore()

		outcome = install.purge_stale_sessions(gateway=store)

		self.assertEqual((outcome["expired"], outcome["purged"]), ((), ()))
		self.assertEqual((store.expire_calls, store.purge_calls), ([], []))

	def test_install_and_migrate_do_not_run_it(self):
		# The sweep is housekeeping: an install or a migrate must not be able to fail because of it.
		bench = bare_bench(installed_on=RECORDED_AT)
		self.given(bench)
		install = self.load_install()
		install.after_migrate()
		install.after_install()

		source = pathlib.Path(install.__file__).read_text(encoding="utf-8")
		body = source.split("def _ensure_app_state", 1)[1].split("def _gateway", 1)[0]

		self.assertNotIn("purge_stale_sessions()", body)


class ConstraintLookBackTest(LifecycleTestCase):
	"""What the guard does when `add_unique` returns without creating anything.

	`frappe.db.add_unique` looks its name up in `TABLE_CONSTRAINTS` filtered by table name **only**,
	while the guard reads `information_schema.STATISTICS` filtered by schema and table. On a bench
	where a second site runs this app, both schemas hold a table with the same name: the guard sees
	no constraint here, `add_unique` finds the other site's row, treats the name as taken and
	returns. The table then has no unique rule, nothing reports it, and every later migrate repeats
	the same silent no-op. The look-back is the only thing that can tell.
	"""

	def test_a_constraint_that_was_not_created_stops_the_migration(self):
		bench = bare_bench()
		bench.silent_add_unique = True
		self.given(bench)
		install = self.load_install()

		with self.assertRaises(FakeThrow):
			install.after_migrate()

		self.assertTrue(
			any("could not be created" in message for message in bench.throws),
			f"the guard accepted a constraint that is not there: {bench.throws}",
		)

	def test_a_constraint_that_was_created_is_accepted(self):
		bench = bare_bench()
		self.given(bench)
		install = self.load_install()

		install.after_migrate()

		self.assertEqual(len(bench.added_unique), 2)
		self.assertEqual(bench.throws, [])
