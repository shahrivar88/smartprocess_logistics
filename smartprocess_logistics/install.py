"""Installation, migration and uninstall entry points for smartprocess_logistics.

`hooks.py` registers three of these: `after_install`, `after_migrate` and `after_uninstall`. They
are the app's whole lifecycle surface, and each one is idempotent.

`after_install` and `after_migrate` run the same four steps, and that is not duplication. Frappe
v16 calls them at different times and one does not imply the other: `installer.install_app`
commits the app into `Installed Applications` *before* it runs `after_install`, and it **never**
runs `after_migrate`. A failure inside `after_install` therefore leaves a site that reports the
app as installed with the work half done, and the only thing that ever repairs it is a later
`after_migrate`. Both entry points call `_ensure_app_state` so that repair is what the next
migrate does, not a manual cleanup.

There is deliberately **no** `before_uninstall`. An earlier version had one, because the app kept
a copy of its own records in tables of its own so that they would survive the removal — and
Frappe's uninstaller removes the table behind every DocType of the app being removed, so that
copy had to be defended by a hook that wrote and verified it first. That whole mechanism is gone.
What protects the records now is what protects the rest of an ERPNext site: a **full site backup**,
and a **portable export of this app's own records** taken before the removal, both of which an
operator takes on purpose rather than as a side effect of uninstalling. `README.md` states the
order, and the export is two buttons on `Logistics Settings`.

`after_uninstall` remains, because it is not about data at all: it clears up what the framework's
own cleanup cannot see.

Every function is safe to run repeatedly: each step checks for what already exists before it
writes. None of them touches a document this app does not own, none calls `ignore_permissions`,
and none writes a business document with SQL.
"""

import json
import os

import frappe
from frappe import _

from smartprocess_logistics.core import session as session_rules
from smartprocess_logistics.setup import constraints, roles, seeds

# The app's icon lives beside `hooks.py`, at `<app>/desktop_icon/<name>.json`. Its document is
# named after its own label, and the label is Persian, which is exactly why the framework's own
# cleanup misses it: `delete_desktop_icon_and_sidebar` deletes the icons whose name or
# `parent_icon` equals the app's **title**, and this app's title is the English
# `SmartProcess Logistics` while the icon is named `سریال لجستیک`. The hook below reads the name
# from the shipped file rather than repeating it, so the two cannot drift apart.
ICON_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_icon")

# Rows read per page while listing icons. A page size rather than "no limit", so that a site with
# many icons does not turn one cleanup into one enormous read.
NAMES_PAGE_SIZE = 200


def _ensure_app_state():
	"""The five steps that make an installation correct, in a deliberate order.

	Roles first, so the DocPerms that name them resolve. Constraints second, because they are the
	invariant the data must satisfy. The next-column command third, and before anything else saves
	the settings row: the migrate that adds that field leaves it empty, and it is a required value
	whose own rule refuses an empty one — so a settings row without it cannot be saved at all. The
	normalization version fourth, because it describes the code rather than this site and must be
	able to catch up on every migrate. Seeds last, because `seed_settings` writes `installed_on`,
	and that timestamp is this app's own marker that the installation finished. Writing it last
	means a failure anywhere above leaves it empty and the next attempt repairs the site instead
	of finding a settings row that claims to be complete.

	`purge_stale_sessions` is deliberately **not** one of them. It is housekeeping, and an install
	or a migrate must not be able to fail because a sweep lost a race; the daily job in `hooks.py`
	is what runs it.
	"""
	roles.ensure_roles()
	constraints.ensure_unique_constraints()
	seeds.sync_next_row_command()
	seeds.sync_inventory_policy_defaults()
	seeds.sync_normalization_version()
	seeds.seed_settings()


def _gateway():
	"""The real gateway, imported here rather than at module level.

	`install.py` is imported by the database-free lifecycle tests, which run it against a stand-in
	`frappe`; the gateway imports `frappe.permissions` by name and would fail to import there. The
	sweep takes a gateway as an argument for the same reason a test can reach it at all.
	"""
	from smartprocess_logistics.api.gateway import FrappeGateway

	return FrappeGateway()


def purge_stale_sessions(gateway=None):
	"""Expire the previews that expired on their own, and remove the finished ones that are old.

	Run by the daily job in `hooks.py`, and by nothing else: housekeeping must not be able to fail
	an install or a migrate, so `_ensure_app_state` deliberately does not call it.

	Both steps are bounded by `core.session.PURGE_BATCH` and ordered by `core.session`, so a site
	that fell behind catches up over several runs instead of emptying the table in one long
	transaction. The table is read **once** for both steps: the two questions are asked of the
	same rows, and the rows are the app's own transient ones, kept small by this very job.

	The returned names mean two different things, and the docstring says so because the keys do
	not: `expired` are the rows the conditional update was aimed at — it reports no count, and a
	row that a confirmation applied while the sweep was reading is deliberately left alone — while
	`purged` are the rows this run actually removed. `contended` says a run lost a race and did
	nothing at all.

	Contention is caught rather than raised. This is housekeeping: two requests and this sweep can
	touch the same session row, and MariaDB aborts one of them. A run that loses that race does
	nothing and tries again tomorrow, which is what `core.orchestration.is_retryable` already says
	about the class — and it means a sweep can never be the reason an install or a migrate fails.
	"""
	gateway = gateway or _gateway()
	now = frappe.utils.now_datetime()

	try:
		sessions = gateway.stored_sessions()

		stale = session_rules.select_expirable(sessions, now)
		given_up = gateway.expire_sessions(stale, _("این پیش‌نمایش منقضی شد.")) if stale else ()

		finished = session_rules.select_purgeable(sessions, now)
		removed = gateway.purge_sessions(finished) if finished else ()
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		frappe.db.rollback()

		return {"expired": (), "purged": (), "contended": True}

	return {"expired": given_up, "purged": removed, "contended": False}


def after_install():
	"""Make a freshly installed site correct.

	The constraints belong here as much as in `after_migrate`. `installer.install_app` runs
	`sync_for` (which creates the tables) and then this hook, and it never runs `after_migrate`,
	so a constraint declared only there would be absent on a site that has never been migrated.
	"""
	_ensure_app_state()


def after_migrate():
	"""Re-assert everything, and repair a site whose install stopped halfway.

	The constraints are asserted again on every migrate, not because v16's schema sync drops
	them — it does not: `build_for_alter_table` only takes the `drop_unique` and `drop_index`
	paths for a unique or indexed column that the DocType metadata no longer declares, and these
	columns are declared with `search_index`. The reason is that the check is a single
	parameterized read of `information_schema.STATISTICS`, which makes re-asserting free, and
	the cases it covers are real: a site restored from a dump taken before the constraints
	existed, a table created by a `sync_for` that ran while this app was mid-upgrade, and a
	half-finished install. `frappe.db.add_unique` reads the catalogue before it issues an ALTER,
	so on a healthy site this is a read and nothing more.

	Note that `frappe.db.add_unique` commits before its ALTER, and `after_migrate` runs inside
	`SiteMigration.post_schema_updates`'s `@atomic` block. That commit is the framework's, it is
	why this hook must never be called from a request handler, and it is harmless here: all
	steps are idempotent, so a partially applied run is repaired by the next one.
	"""
	_ensure_app_state()


def after_uninstall():
	"""Take away what the framework's own cleanup cannot see, and leave the rest alone.

	By the time this runs the app is no longer installed: the DocTypes, the module, the workspace,
	the report and both sidebars are gone, and `frappe.utils.install.delete_desktop_icon_and_sidebar`
	has already tried to remove the navigation. It matches a desktop icon by name or `parent_icon`
	against the app's **title**, and this app's icon is named in Persian, so the icon survives the
	removal pointing at a sidebar that no longer exists. That orphan is removed here.

	The three roles are the other residue: a `Role` has no link to a module, so nothing about the
	removal reaches them. A role is deleted only when nothing else in the site refers to it — no
	user holds it, and no permission row does. A role a user still holds is left in place and
	reported: taking it away would change what that user can do everywhere else, which is a
	decision for an administrator rather than a side effect of a removal.

	This hook **never raises**. By the time it runs, the app is already gone: an exception here
	would end the command with a failure after the removal had actually succeeded, and would leave
	an operator believing a removal that is complete had gone wrong. Anything it cannot remove is
	logged and returned instead.
	"""
	removed_icons, kept_icons = _retire_desktop_icons()
	retired_roles, kept_roles = _retire_roles()

	return {
		"desktop_icons": removed_icons,
		"desktop_icons_kept": kept_icons,
		"roles_removed": retired_roles,
		"roles_kept": kept_roles,
	}


def _shipped_icon_names():
	"""The names of the desktop icons this app ships, read from the files that define them."""
	if not os.path.isdir(ICON_DIRECTORY):
		return ()

	names = []

	for entry in sorted(os.listdir(ICON_DIRECTORY)):
		if not entry.endswith(".json"):
			continue

		with open(os.path.join(ICON_DIRECTORY, entry), encoding="utf-8") as handle:
			document = json.load(handle)

		name = document.get("name") or document.get("label")

		if name:
			names.append(name)

	return tuple(names)


def _paged(doctype, filters, fields=None):
	"""Every row of one query, a page at a time, until a short page says there are no more."""
	rows = []
	offset = 0

	while True:
		page = frappe.get_list(doctype, filters=filters, fields=fields, limit=NAMES_PAGE_SIZE, offset=offset)

		rows.extend(page)

		if len(page) < NAMES_PAGE_SIZE:
			return rows

		offset += len(page)


def _retire_desktop_icons():
	"""Delete this app's desktop icons, and their children, reporting what would not go."""
	removed = []
	kept = []

	for name in _shipped_icon_names():
		children = [row.get("name") for row in _paged("Desktop Icon", {"parent_icon": name}, ["name"])]

		for icon in (name, *children):
			if not frappe.db.exists("Desktop Icon", icon):
				continue

			try:
				frappe.delete_doc("Desktop Icon", icon)
			except Exception:
				frappe.log_error(
					message=frappe.get_traceback(),
					title="smartprocess_logistics: a desktop icon could not be removed",
				)
				kept.append(icon)
				continue

			removed.append(icon)

	return tuple(removed), tuple(kept)


def _role_linking_fields():
	"""Every `(doctype, fieldname)` in this site that can name a Role.

	Read from the site's own metadata rather than listed here, and that is not thoroughness for
	its own sake: a hardcoded list is wrong on a site whose apps differ from this one's. Measured
	on the site this app was built on, `Role Profile Item` — the child table one would name from
	memory — **does not exist** in this build, so counting rows on it raises a SQL error about a
	missing table, inside the one hook that must not raise. `Has Role`, `Custom DocPerm` and
	`DocPerm` all exist; a `Role Profile`'s children are reached through metadata instead.
	"""
	fields = []

	for doctype in ("DocField", "Custom Field"):
		if not frappe.db.exists("DocType", doctype):
			continue

		fields.extend(
			(row.get("parent"), row.get("fieldname"))
			for row in _paged(doctype, {"fieldtype": "Link", "options": "Role"}, ["parent", "fieldname"])
			if row.get("parent") and row.get("fieldname")
		)

	return tuple(dict.fromkeys(fields))


def _retire_roles():
	"""Delete this app's roles where nothing in the site refers to them, and report the rest.

	The check is made before the delete, because the framework's own check *is* the delete: it
	refuses by raising, and this hook must not raise. A role that anything refers to is reported
	and left alone. So is a role whose deletion the framework refuses for a reason this check did
	not foresee: the goal is to tidy up, not to argue with the site.
	"""
	retired = []
	kept = []
	linking = _role_linking_fields()

	for role in tuple(roles.ROLES):
		if not frappe.db.exists("Role", role):
			continue

		try:
			references = sum(
				frappe.db.count(doctype, {field: role})
				for doctype, field in linking
				if frappe.db.exists("DocType", doctype)
			)

			if references:
				kept.append(role)
				continue

			frappe.delete_doc("Role", role)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="smartprocess_logistics: a role could not be removed",
			)
			kept.append(role)
			continue

		retired.append(role)

	return tuple(retired), tuple(kept)
