"""Installation, migration and uninstall entry points for smartprocess_logistics.

`hooks.py` registers `after_install` and `after_migrate`. `before_uninstall` is implemented
here, tested, and deliberately **not registered**: see its docstring for what still has to be
exercised against a real uninstall on a disposable site before it is allowed to run.

`after_install` and `after_migrate` run the same three steps, and that is not duplication.
Frappe v16 calls them at different times and one does not imply the other: `installer.install_app`
commits the app into `Installed Applications` *before* it runs `after_install`, and it **never**
runs `after_migrate`. A failure inside `after_install` therefore leaves a site that reports the
app as installed with the work half done, and the only thing that ever repairs it is a later
`after_migrate`. Both entry points call `_ensure_app_state` so that repair is what the next
migrate does, not a manual cleanup.

Every function is safe to run repeatedly: each step checks for what already exists before it
writes. None of them touches a document this app does not own, and none of them calls
`ignore_permissions` or `frappe.db.get_all`.
"""

import json
import os

import frappe
from frappe import _

from smartprocess_logistics.setup import constraints, roles, seeds

# Records this app owns that a reader would miss if they disappeared. These decide whether an
# uninstall has something at stake, and they are the only tables the copy reads; see
# `before_uninstall`.
PROTECTED_DOCTYPES = ("Logistics Serial", "Logistics Serial Assignment")

# The app's Single. Its rows are configuration an administrator typed, not defaults, so the
# copy carries them even though a later install would seed its own. It holds no secret: a
# switch, a timestamp, four limits and the parsed delimiter and marker tables.
COPIED_SINGLES = ("Logistics Settings",)

# `Logistics Import Session` is deliberately **not** copied. A preview session is transient by
# design — the claim is never persisted and the row expires — so there is nothing in it worth
# recovering. It also carries the two hidden fields `token` and `payload`, and the token is the
# nonce a client uses to confirm its own preview. Copying that into a file on a volume every
# service in the stack can read would widen the exposure of a live capability to preserve
# nothing, so the table is left out rather than filtered field by field.

# Rows read per query while building the copy. The loop pages until the expected count is
# reached, so this bounds memory rather than completeness.
EXPORT_PAGE_SIZE = 500

# How many names to try before giving up on finding a free one for the copy.
PRESERVATION_ATTEMPTS = 100


class PreservationError(Exception):
	"""The copy could not be written, or could not be shown to be a faithful one.

	Both amount to the same thing for the caller: there is no artifact to restore from, so the
	uninstall stops.
	"""


def _ensure_app_state():
	"""The three steps that make an installation correct, in a deliberate order.

	Roles first, so the DocPerms that name them resolve. Constraints second, because they are
	the invariant the data must satisfy. Seeds last, because `seed_settings` writes
	`installed_on`, and that timestamp is this app's own marker that the installation finished.
	Writing it last means a failure anywhere above leaves it empty and the next attempt repairs
	the site instead of finding a settings row that claims to be complete.
	"""
	roles.ensure_roles()
	constraints.ensure_unique_constraints()
	seeds.seed_settings()


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
	three steps are idempotent, so a partially applied run is repaired by the next one.
	"""
	_ensure_app_state()


def before_uninstall():
	"""Write a verified copy of this app's records, and stop the uninstall only if that fails.

	This does **not** refuse an uninstall because records exist. `installer.remove_app` deletes
	every record linked to this app's modules, so refusing was the old behaviour and it made a
	normal uninstall impossible without hand-deleting data first. The rule now is about
	recoverability, not permission: the uninstall proceeds, and it proceeds only after a copy
	of the records exists and has been read back and checked.

	So the one thing that stops an uninstall is being unable to preserve. When the copy cannot be
	written, or the file that was written does not contain the rows that were read, the uninstall
	is stopped with the tables still intact, because that is the single case where continuing
	destroys business records with nothing to restore from.

	`remove_app` runs this hook before it deletes anything (verified: the hook loop is above
	`_delete_modules`), so the DocTypes and their rows are still readable.

	Three things a reader should know before registering this:

	- **It writes a file on a dry run too.** `remove_app(dry_run=True)` runs the hooks exactly
	  like a real uninstall; nothing in `frappe.flags` distinguishes the two, so a dry run leaves
	  a copy behind. It is a file, not a database write, but it is not obvious.
	- **The copies are never deleted by the app.** There is no retention sweep, on purpose:
	  quietly removing the only artifact of records that no longer exist is the opposite of what
	  this hook is for. An operator removes them from `<site>/private/files`.
	- **Nothing was copied before this hook was registered.** While `hooks.py` leaves it
	  commented, `bench uninstall-app` drops the app's tables with no copy at all. That is the
	  current state, and it is why registering this is a step of its own.
	"""
	populated = _populated_doctypes()

	if not populated:
		return

	try:
		path = _write_preservation_copy(populated)
	except Exception:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="smartprocess_logistics: uninstall copy could not be written",
		)
		frappe.throw(
			_(
				"A complete copy of this app's records could not be written, so the uninstall was"
				" stopped and nothing has been deleted yet. The two DocTypes still hold {0}."
				" The cause is in the Error Log. Take a backup and export the data by hand before"
				" uninstalling."
			).format(_describe(populated)),
			title=_("Records could not be preserved"),
		)

	frappe.msgprint(
		_("A copy of this app's records was written to {0} before the uninstall dropped their tables.").format(
			frappe.bold(path)
		),
		title=_("Records were preserved"),
		indicator="green",
	)


def _describe(populated):
	return ", ".join(f"{doctype}: {count}" for doctype, count in populated)


def _populated_doctypes():
	"""(doctype, count) for each protected DocType that exists and holds rows."""
	counted = []

	for doctype in PROTECTED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue

		count = frappe.db.count(doctype)

		if count:
			counted.append((doctype, count))

	return counted


def _write_preservation_copy(populated):
	"""Write the JSON copy, verify it by reading it back, and return its path.

	Verification is the point: an export nobody has checked is not a recovery plan. The file is
	re-read, parsed, and compared against what was read from the database, row for row per
	DocType. Anything short of an exact match raises `PreservationError`, which the caller turns
	into a stopped uninstall.
	"""
	payload = {
		"app": "smartprocess_logistics",
		"site": frappe.local.site,
		"written_at": frappe.utils.now(),
		"doctypes": {},
		"singles": {},
	}

	for doctype, expected in populated:
		rows = _read_all_rows(doctype, expected)
		payload["doctypes"][doctype] = rows

	for doctype in COPIED_SINGLES:
		if frappe.db.exists("DocType", doctype):
			payload["singles"][doctype] = frappe.get_single(doctype).as_dict()

	# `default=str` is what makes this total rather than lucky: `get_list` hands back `datetime`
	# objects and `Decimal` values, which have no JSON form, and a copy that refused to write
	# because a cell held a decimal would be a copy that never happens.
	document = json.dumps(payload, ensure_ascii=False, indent=1, default=str)

	path = _write_document(document)

	try:
		_verify_written_copy(path, document)
	except Exception:
		# A file that failed its own check is not a copy, and leaving a raw dump of business
		# rows on the site's disk would be a worse outcome than not having it. The cause is
		# logged by the caller, which also stops the uninstall.
		_remove_quietly(path)
		raise

	return path


def _write_document(document):
	"""Create the copy with owner-only permissions and write it. Returns the path it used.

	Two deliberate choices:

	- `O_EXCL`, so a second uninstall cannot overwrite the first one's copy, and a name that is
	  somehow taken is stepped past rather than clobbered. The name carries microseconds, so
	  this only ever matters if the clock repeats itself.
	- mode `0o600`, because the file holds business rows and the site's `private/files` sits on
	  the volume the whole stack shares. `os.open`'s mode is masked by the process umask, so the
	  file is chmodded afterwards too — the `os.open` mode is what makes the window between
	  creation and chmod not a world-readable one.
	"""
	base = _preservation_base()

	for attempt in range(1, PRESERVATION_ATTEMPTS + 1):
		path = f"{base}.json" if attempt == 1 else f"{base}-{attempt}.json"

		try:
			descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
		except FileExistsError:
			continue

		with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
			handle.write(document)

		os.chmod(path, 0o600)

		return path

	raise PreservationError(f"no free name for the copy under {base}.json")


def _remove_quietly(path):
	try:
		os.remove(path)
	except OSError:
		pass


def _read_all_rows(doctype, expected):
	"""Read every row of a DocType, a page at a time, and prove the read was not lossy.

	Paged rather than one unlimited query so the copy does not have to fit in memory, and
	ordered by `name` as well as `creation` so the order is total — `name` is the primary key,
	so it breaks every tie and the pages cannot shuffle between each other.

	Three things are checked, because offset paging over a table nobody has locked can skip a
	row or repeat one if the table changes underneath it:

	1. The loop is bounded by `expected` as well as by a short page, so it terminates even if
	   paging were ignored.
	2. The count must equal `frappe.db.count`. A count that is *short* means the read could not
	   see every row — usually a permission scope, since the count is unfiltered and the read is
	   not.
	3. No `name` may appear twice. This is the check that catches a shifting offset window: a
	   row skipped at one boundary and a row repeated at the next keep the count equal, so only
	   looking for duplicates proves the copy is neither short nor doubled.

	`fields=["*"]` expands to every column the reader may see, which for the two DocTypes read
	here holds no hidden or secret field.
	"""
	rows = []
	offset = 0

	while len(rows) < expected:
		page = frappe.get_list(
			doctype,
			fields=["*"],
			order_by="creation asc, name asc",
			limit=EXPORT_PAGE_SIZE,
			offset=offset,
		)

		if not page:
			break

		rows.extend(page)
		offset += len(page)

	if len(rows) != expected:
		raise PreservationError(
			f"{doctype}: read {len(rows)} rows, but the database reports {expected}. Either the"
			" read was incomplete or the reader may not see every row."
		)

	names = [row.get("name") for row in rows]
	repeated = len(names) - len(set(names))

	if repeated:
		raise PreservationError(
			f"{doctype}: the read returned {repeated} duplicate rows, so the pages overlapped."
			" The copy would not be a faithful one."
		)

	return rows


def _preservation_base():
	directory = frappe.get_site_path("private", "files")
	os.makedirs(directory, exist_ok=True)

	stamp = frappe.utils.now().replace(" ", "-").replace(":", "-").replace(".", "-")

	return os.path.join(directory, f"smartprocess_logistics-uninstall-{stamp}")


def _verify_written_copy(path, document):
	"""Read the file back and check it is the copy that was prepared.

	Two separate things are being trusted, so two separate checks. That the *content* is
	complete is settled before this point, by `_read_all_rows` comparing its own count against
	`frappe.db.count`. What is left is the *file*: a write that was truncated, interleaved with
	another writer, or made in the wrong encoding produces a file that parses or does not, and
	in neither case is it the copy that was prepared.

	The comparison is between the file's parse and the prepared document's parse, not between
	the file and the raw rows, because the rows hold `datetime` objects that only exist as
	strings once serialized.
	"""
	with open(path, encoding="utf-8") as handle:
		written = handle.read()

	try:
		parsed = json.loads(written)
	except ValueError as exc:
		raise PreservationError(f"the copy at {path} is not readable JSON: {exc}") from exc

	prepared = json.loads(document)

	if parsed != prepared:
		# One comparison, and the table lists in the message. A separate check for a missing
		# table would be unreachable: it would only fire when the documents already differ.
		raise PreservationError(
			f"the copy at {path} does not match the copy that was prepared. Tables in the file:"
			f" {sorted(parsed.get('doctypes', {}))}; tables prepared:"
			f" {sorted(prepared.get('doctypes', {}))}"
		)
