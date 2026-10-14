"""The portable form of this app's records: what an export holds, and what an import accepts.

Why a file and not a table
--------------------------
This app's records are the warranty serial numbers on a Sales Invoice row and the assignments
that say which serial went on which row. Frappe's uninstaller removes the table behind every
DocType of the app being removed, so nothing this app keeps *inside* its own schema can outlive
its own removal. The answer is the one an ERPNext site already uses for everything else: a **full
site backup** for disaster recovery, and a **portable, lossless export** of this app's records for
the case where the app itself goes away.

The export is a self-describing JSON document, versioned twice on purpose:

- `format_version` describes the shape of *this file*. An import refuses a file written by a
  newer format rather than guessing at fields it does not know.
- `normalization_version` is the rule set that produced the stored identities; it also travels on
  every row, so a serial registered under an older rule set stays interpretable.

Losslessness is not a promise in this docstring; it is a field list that a test compares against
the DocType definitions themselves. If a field is added to `Logistics Serial` and not to
`SERIAL_FIELDS`, that test fails — instead of every export a site takes quietly dropping it.

What an import will not do
--------------------------
It creates `Logistics Serial` and `Logistics Serial Assignment` rows and nothing else. It never
creates an Item, a Company or a Sales Invoice, and never edits one: a row whose item, company,
invoice or invoice row is missing is reported as a conflict and left alone. It never overwrites
what is already stored — a row whose identity is already live is skipped — and it deletes nothing.

It also never fills an invoice row past its quantity. A restored file is not a privileged way
into the database: the cap an operator meets while scanning is the cap an import meets, and an
assignment that would take a row over its invoiced quantity is **reported**, with the reason
`EXCESS_QUANTITY`, rather than written. Reporting rather than refusing the whole file is the
deliberate choice: the rest of the file is still restored, nothing the site already holds is
touched, and the rows that did not fit are named for the operator to decide about. A row that
already holds its quantity here keeps what it holds — an excess this app did not create, and will
not silently undo.
"""

import hashlib
import json
from dataclasses import dataclass, field

FORMAT = "smartprocess-logistics-serials"

# Raised when the file's shape changes. An older app refuses a newer file; a newer app reads an
# older one, which is what makes an export a durable artifact rather than a one-version stopgap.
#
# 2 adds the transfer records — the app's own account of moving a cancelled invoice's serials onto
# its amendment. They are carried so that an uninstall does not lose them; they are never
# re-created by an import, and `plan` says so. A version 1 file has no such section and is read
# exactly as it always was.
FORMAT_VERSION = 2

SERIAL_DOCTYPE = "Logistics Serial"
ASSIGNMENT_DOCTYPE = "Logistics Serial Assignment"
TRANSFER_DOCTYPE = "Logistics Serial Transfer"
TRANSFER_ITEM_DOCTYPE = "Logistics Serial Transfer Item"

# Every field of each DocType, and the reason the export is lossless. `tests/test_export.py`
# reads the DocType definitions from this repository and compares them with these tuples.
SERIAL_FIELDS = (
	"name",
	"serial_no",
	"normalized_serial",
	"item_code",
	"item_name",
	"status",
	"first_registered_on",
	"normalization_version",
	"remarks",
	"creation",
	"modified",
	"owner",
	"modified_by",
)

ASSIGNMENT_FIELDS = (
	"name",
	"logistics_serial",
	"item_code",
	"normalized_serial",
	"sales_invoice",
	"company",
	"sales_invoice_item",
	"invoice_item_idx",
	"remarks",
	"creation",
	"modified",
	"owner",
	"modified_by",
)

# The fields an identity is decided on, mirroring the two composite unique constraints on the
# live tables — and the fields without which a row cannot be placed at all.
SERIAL_IDENTITY = ("item_code", "normalized_serial")
ASSIGNMENT_IDENTITY = ("sales_invoice", "normalized_serial")

# The four columns that say *when and by whom* a row was written. Frappe rewrites all of them on
# an insert — `set_user_and_timestamp` overwrites `modified` and `modified_by` unconditionally, and
# `creation` and `owner` unless one of `in_install`, `in_patch` or `in_migrate` is set. An import
# must not leave them at the importing user's clock, so they are written back afterwards through
# `frappe.db.set_value(..., update_modified=False)`: a supported API, no core change, and no
# validation switched off. `core.export.row_timestamps` decides which of the four a row actually
# carries, and the tests below pin that decision.
TIMESTAMP_FIELDS = ("creation", "owner", "modified", "modified_by")

# The header and child rows of a transfer record. Every field of both DocTypes, for the same
# reason the other two sections carry every field: the export is the copy of record, and a field
# left out is a field an uninstall destroys.
TRANSFER_FIELDS = (
	"name",
	"source_invoice",
	"target_invoice",
	"company",
	"confirmed_token",
	"source_total",
	"moved_count",
	"refused_count",
	"unselected_count",
	"acknowledged_count",
	"docstatus",
	# The restore's own marker. An original record carries these empty and a restored one does not,
	# which is the whole of how the two are told apart; they travel in the file so that a record
	# restored somewhere can be recognised as restored anywhere.
	"restored_from",
	"restored_on",
	"restored_by",
	"remarks",
	"creation",
	"modified",
	"owner",
	"modified_by",
)

TRANSFER_ITEM_FIELDS = (
	"name",
	"parent",
	"logistics_serial",
	"serial_no",
	"item_code",
	"normalized_serial",
	"result",
	"reason",
	"reason_message",
	"source_assignment",
	"source_row",
	"source_index",
	"target_row",
	"target_index",
	"acknowledged",
	"creation",
	"modified",
	"owner",
	"modified_by",
)

# A transfer is identified by its name; a row of one by its parent and the serial on it — the same
# pair `UNIQUE(sales_invoice, normalized_serial)` is for an assignment.
TRANSFER_IDENTITY = ("name",)
TRANSFER_ITEM_IDENTITY = ("parent", "normalized_serial")

SECTIONS = {
	"serials": (SERIAL_FIELDS, SERIAL_IDENTITY),
	"assignments": (ASSIGNMENT_FIELDS, ASSIGNMENT_IDENTITY),
	"transfers": (TRANSFER_FIELDS, TRANSFER_IDENTITY),
	"transfer_items": (TRANSFER_ITEM_FIELDS, TRANSFER_ITEM_IDENTITY),
}

# The version each section arrived in, so `read` can tell a section a file is too old to have from a
# section that should be there and is not. A version 1 file has no transfers and is missing
# nothing; a version 2 file that lost them is a file that was edited or truncated. A section the
# file's version does not declare is ignored rather than refused: nothing reads it, so requiring it
# would add a way to fail without removing one.
SECTION_VERSION = {"serials": 1, "assignments": 1, "transfers": 2, "transfer_items": 2}


def sections_of(version):
	"""The sections a file of this version declares, in the format's own order."""
	return tuple(name for name in SECTIONS if SECTION_VERSION[name] <= version)


# How many conflicting rows a preview names. A preview is a summary an operator reads before
# deciding; the whole list belongs in the report of the import that follows.
PREVIEW_SAMPLE = 20

CODE_NOT_JSON = "EXPORT_NOT_JSON"
CODE_NOT_OURS = "EXPORT_NOT_OURS"
CODE_FORMAT_TOO_NEW = "EXPORT_FORMAT_TOO_NEW"
CODE_DIGEST_MISMATCH = "EXPORT_DIGEST_MISMATCH"
CODE_BAD_SHAPE = "EXPORT_BAD_SHAPE"
CODE_ROW_INVALID = "EXPORT_ROW_INVALID"

# Conflict reasons. Each is a decision an operator has to make, which is why they are separate
# rather than one "could not import" bucket.
REASON_ITEM_MISSING = "ITEM_MISSING"
REASON_COMPANY_MISSING = "COMPANY_MISSING"
REASON_INVOICE_MISSING = "INVOICE_MISSING"
REASON_INVOICE_CANCELLED = "INVOICE_CANCELLED"
REASON_INVOICE_ROW_MISSING = "INVOICE_ROW_MISSING"
REASON_COMPANY_MISMATCH = "COMPANY_MISMATCH"
REASON_SERIAL_UNRESOLVED = "SERIAL_UNRESOLVED"
REASON_LINK_CONFLICT = "LINK_CONFLICT"
REASON_EXCESS_QUANTITY = "EXCESS_QUANTITY"
# A row of the file that belongs to a live invoice, reported by the historical restore, which is
# for cancelled invoices only. Not a failure: the sentence names the endpoint that places it.
REASON_NOT_HISTORY = "NOT_HISTORY"
# A record whose name is already used on this site. The naming series is per site, so two sites
# both produce `LST-2026-00001` for different transfers — which is why this is refused rather than
# resolved: overwriting would destroy an audit record, and renaming would lose the identity the
# archive exists to carry.
REASON_RECORD_EXISTS = "RECORD_EXISTS"
# A record whose target invoice is cancelled *now*. `target_invoice` is a Link, and the framework
# refuses to save a link to a cancelled document, so the record cannot be placed at all.
REASON_RECORD_LINK_CANCELLED = "RECORD_LINK_CANCELLED"

# The two outcomes a transfer record row carries, and the vocabulary its `Select` offers. Read here
# rather than imported from `core.transfer`, which imports this module — the two are one vocabulary
# and a test asserts they agree.
TRANSFER_RESULTS = ("Moved", "Refused")

# The one state a stored or restored transfer record may be in. Every record this app writes is
# inserted at this value (`gateway.insert_transfer`), the DocType grants nobody `write`, `submit`,
# `cancel` or `amend`, and the framework refuses to delete or change a submitted document — so a
# record at `0` is a record that can be edited and deleted, which is the state I24 exists to
# prevent. A file claiming it is refused by `check_transfer_records`, and `_plan_records` writes
# this value rather than the file's, so no path can place a draft.
DOCSTATUS_SUBMITTED = 1

REASON_MESSAGES = {
	REASON_ITEM_MISSING: "کالا در این سایت وجود ندارد",
	REASON_COMPANY_MISSING: "شرکت در این سایت وجود ندارد",
	REASON_INVOICE_MISSING: "فاکتور فروش در این سایت وجود ندارد",
	REASON_INVOICE_CANCELLED: (
		"فاکتور فروش لغو شده است و سریال جدید نمی‌پذیرد؛ ثبت‌های تاریخی آن را فقط"
		" «بازآوری تاریخچهٔ فاکتورهای لغو‌شده» می‌تواند برگرداند"
	),
	REASON_INVOICE_ROW_MISSING: "ردیف فاکتور در این فاکتور وجود ندارد",
	REASON_COMPANY_MISMATCH: "شرکت ردیف با شرکت فاکتور نمی‌خواند",
	REASON_SERIAL_UNRESOLVED: "سریال این ردیف نه در فایل هست و نه در سایت",
	REASON_LINK_CONFLICT: "این سریال از قبل روی همین فاکتور با سریال دیگری ثبت شده است",
	REASON_EXCESS_QUANTITY: "ردیف فاکتور به تعداد خودش سریال دارد و سریال بیشتری نمی‌پذیرد",
	REASON_NOT_HISTORY: "این ردیف روی فاکتور لغو‌شده نیست؛ با بازآوری معمولی ثبت می‌شود",
	REASON_RECORD_EXISTS: "سابقه‌ای با همین نام از قبل روی این سایت هست؛ هیچ چیز بازنویسی نمی‌شود",
	REASON_RECORD_LINK_CANCELLED: "فاکتور مقصد این سابقه اکنون لغو شده است و سابقه به آن نمی‌تواند اشاره کند",
}

CODE_VALUE_NOT_ALLOWED = "EXPORT_VALUE_NOT_ALLOWED"
CODE_TOO_LARGE = "EXPORT_TOO_LARGE"

# The largest export this app will read back, as a code constant rather than a `Logistics
# Settings` field, for the reason `core.session` gives about its retention window: a ceiling on
# nonsense is not a decision an operator makes, and a number nothing reads is a number that goes
# stale. Sixteen megabytes is far above a site's serial history — a serial row is a few hundred
# bytes, so this is tens of thousands of rows — and far below anything that would exhaust the
# process that parses it.
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024

# What an import is for, as three named values rather than a growing set of switches. Each is one
# act with its own endpoint, its own authorization and its own blast radius, and they are **disjoint**:
# no mode places something another mode owns, so "which restore is this" is answered by the mode and
# not by reading a combination of flags.
#
# - `MODE_LIVE` is the ordinary restore. It places assignments on live invoices and refuses a row on
#   a cancelled one, because a cancelled invoice's accounting has been reversed and a registration
#   added to it records a claim nothing supports (I22). The row is reported as `INVOICE_CANCELLED`.
# - `MODE_HISTORY` is the historical restore: assignments on **cancelled** invoices only, because a
#   cancelled invoice's registrations are a faithful account of what a warehouse scanned and losing
#   them across a reinstall loses history that cannot be reconstructed. A row on a live invoice is
#   reported as `NOT_HISTORY` and left to the ordinary restore.
# - `MODE_RECORDS` places the **transfer records** and their rows, and nothing else. Not the serials
#   and not an assignment: a record whose serial master is absent from both the file and the site is
#   reported as `SERIAL_UNRESOLVED`, so the ordinary restore is what puts the serials there first.
MODE_LIVE = "live"
MODE_HISTORY = "history"
MODE_RECORDS = "records"

MODES = (MODE_LIVE, MODE_HISTORY, MODE_RECORDS)

# The three columns only the restore path writes. Named here so the plan and the tests share one
# spelling of "this document was made from an archive".
RESTORE_FIELDS = ("restored_from", "restored_on", "restored_by")


def row_timestamps(row):
	"""The provenance of one row, as the values an import writes back after inserting it.

	Only the fields the row actually carries are returned, so an older export — written before a
	field existed — writes nothing rather than writing a blank over what Frappe just set. An empty
	answer means the insert's own values stand, which is the honest outcome for a row that never
	said when it was created.
	"""
	return {field: row.get(field) for field in TIMESTAMP_FIELDS if row.get(field) not in (None, "")}


def misplaced_choices(choices, values):
	"""Stored values a `Select` field of this site would not accept.

	`frappe.flags.in_import` and `in_install` are both switched on around an import — the first
	keeps the original name, the second keeps the original `creation` and `owner` — and between
	them they suppress `_validate_selects`. So the check that would have refused a value the
	DocType no longer allows is made here instead, from the site's own field definition, rather
	than left to a validation the import has switched off.
	"""
	misplaced = []

	for fieldname, options in (choices or {}).items():
		value = values.get(fieldname)

		if not value or not options:
			continue

		allowed = [option for option in options.split("\n") if option]

		if allowed and value not in allowed:
			misplaced.append((fieldname, value))

	return tuple(misplaced)


class ExportError(ValueError):
	"""A file this app will not import. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class Conflict:
	"""One row an import cannot place, and why."""

	row: str
	reason: str
	detail: str = ""

	def describe(self):
		return REASON_MESSAGES.get(self.reason, self.reason)


@dataclass(frozen=True)
class ImportPlan:
	"""What an import will create, what it will skip, and what it refuses to guess at."""

	serials: tuple = ()
	assignments: tuple = ()
	skipped_serials: tuple = ()
	skipped_assignments: tuple = ()
	conflicts: tuple = field(default=())
	# The transfer records this import will place, and their rows — empty in every mode but
	# `MODE_RECORDS`.
	transfers: tuple = ()
	transfer_items: tuple = ()
	# How many transfer records the file carries, whether or not this mode places them. Reported as
	# a count rather than as rows: an operator is told the file holds history, not handed a list of
	# conflicts about rows the mode they chose never attempts.
	records_in_file: int = 0

	def summary(self):
		return {
			"to_create": {"serials": len(self.serials), "assignments": len(self.assignments)},
			"to_skip": {
				"serials": len(self.skipped_serials),
				"assignments": len(self.skipped_assignments),
			},
			"conflicts": len(self.conflicts),
			"by_reason": count_by_reason(self.conflicts),
			"records_in_file": self.records_in_file,
			"to_create_records": {"transfers": len(self.transfers), "items": len(self.transfer_items)},
		}


def count_by_reason(conflicts):
	counts = {}

	for conflict in conflicts:
		counts[conflict.reason] = counts.get(conflict.reason, 0) + 1

	return counts


def sample(conflicts, limit=PREVIEW_SAMPLE):
	"""The first few conflicts, as rows an operator can read and act on."""
	return tuple(
		{
			"row": conflict.row,
			"reason": conflict.reason,
			"message": conflict.describe(),
			"detail": conflict.detail,
		}
		for conflict in conflicts[:limit]
	)


# --- the document ---------------------------------------------------------------------


def canonical(document):
	"""The text a digest is computed over: keys sorted, no incidental whitespace."""
	return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def document_digest(document):
	"""The digest of everything in the document except the digest itself."""
	body = {key: value for key, value in document.items() if key != "digest"}

	return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def project(row, fields):
	"""One row, reduced to the fields the format defines and nothing else.

	`str()` on the way out is what makes a `datetime` a string that survives the round trip; the
	value comes back as the same text it was stored with.
	"""
	return {field: _plain(row.get(field)) for field in fields}


def _plain(value):
	if value is None or isinstance(value, (str, int, float, bool)):
		return value

	return str(value)


def build(
	serials,
	assignments,
	transfers=(),
	transfer_items=(),
	*,
	app_version=None,
	site=None,
	exported_by=None,
	exported_on=None,
	normalization_version=None,
):
	"""The export document: a header, the rows, and the digest that seals them."""
	document = {
		"format": FORMAT,
		"format_version": FORMAT_VERSION,
		"app_version": app_version,
		"site": site,
		"exported_on": exported_on,
		"exported_by": exported_by,
		"normalization_version": normalization_version,
		"counts": {
			"serials": len(serials),
			"assignments": len(assignments),
			"transfers": len(transfers),
			"transfer_items": len(transfer_items),
		},
		"serials": [project(row, SERIAL_FIELDS) for row in serials],
		"assignments": [project(row, ASSIGNMENT_FIELDS) for row in assignments],
		"transfers": [project(row, TRANSFER_FIELDS) for row in transfers],
		"transfer_items": [project(row, TRANSFER_ITEM_FIELDS) for row in transfer_items],
	}

	document["digest"] = document_digest(document)

	return document


def as_json(document):
	return json.dumps(document, ensure_ascii=False, indent=1, default=str)


def read(raw):
	"""Parse and check a file, and answer with the document it claims to be.

	Every check is a refusal rather than a repair: a file that is not this app's, that was written
	by a newer format, or whose contents do not match the digest sealed into it is not something
	to import part of.
	"""
	if isinstance(raw, (bytes, bytearray)):
		raw = raw.decode("utf-8", errors="strict")

	if isinstance(raw, str):
		try:
			document = json.loads(raw)
		except ValueError as error:
			raise ExportError(CODE_NOT_JSON, f"فایل JSON خوانده نشد: {error}") from error
	elif isinstance(raw, dict):
		document = raw
	else:
		raise ExportError(CODE_NOT_JSON, "فایل باید JSON باشد.")

	if not isinstance(document, dict) or document.get("format") != FORMAT:
		raise ExportError(CODE_NOT_OURS, "این فایل، خروجی سریال‌های این برنامه نیست.")

	version = document.get("format_version")

	if not isinstance(version, int) or version < 1:
		raise ExportError(CODE_BAD_SHAPE, "نسخهٔ قالب فایل مشخص نیست.")

	if version > FORMAT_VERSION:
		raise ExportError(
			CODE_FORMAT_TOO_NEW,
			f"نسخهٔ قالب فایل «{version}» از نسخهٔ این برنامه «{FORMAT_VERSION}» جدیدتر است."
			" برنامه را به‌روز کنید.",
		)

	declared = document.get("digest")

	if not isinstance(declared, str) or declared != document_digest(document):
		raise ExportError(
			CODE_DIGEST_MISMATCH,
			"محتوای فایل با اثر انگشت ثبت‌شده در آن نمی‌خواند؛ فایل تغییر کرده یا ناقص است.",
		)

	for key in sections_of(version):
		if not isinstance(document.get(key), list):
			raise ExportError(CODE_BAD_SHAPE, f"بخش «{key}» در فایل نیست یا فهرست نیست.")

	_declared_counts_agree(document, version)

	# The rows are checked here as well as by `plan`, so that every caller of `read` holds rows
	# whose identity fields are present and unique. `plan` re-derives them — it is a pure
	# function over the document — and re-deriving is cheaper than a second place that decides
	# what a valid row is.
	serial_rows(document)
	assignment_rows(document)

	# Not read here, and deliberately: a transfer record is history this app keeps, not data an
	# import places, so `plan` counts them and never attempts one. They are checked as rows all the
	# same — a file whose transfer section is malformed is a file this app cannot vouch for.
	transfer_rows(document)
	transfer_item_rows(document)

	# The sections a version 1 file does not have are not checked: there is nothing to disagree
	# with itself. A version 2 file is checked whole, because a record is only worth archiving if
	# it is the record it says it is.
	if "transfers" in sections_of(version):
		check_transfer_records(document)

	return document


def _declared_counts_agree(document, version):
	"""The header's counts against the rows actually in the file.

	They cannot disagree in a file this app wrote, so a disagreement means the document was
	edited — and a digest that still matched would mean the digest is not covering what it claims
	to. Checking both is cheap and makes the header usable as a quick sanity read.

	Only the sections this file's version declares are checked. A version 1 file has no transfer
	count to compare and is not missing one.
	"""
	declared = document.get("counts") or {}

	for key in sections_of(version):
		if int(declared.get(key, -1)) != len(document.get(key) or []):
			raise ExportError(
				CODE_BAD_SHAPE,
				f"شمارش اعلام‌شدهٔ «{key}» با ردیف‌های فایل نمی‌خواند.",
			)


def rows_of(document, section):
	"""The rows of one section, with the format's fields, each checked for its identity.

	A row without its identity fields cannot be placed, and placing it under a guessed name is
	worse than refusing the file: it would put a serial into the site under an identity nobody can
	reproduce. A duplicated identity inside one file is refused for the same reason.
	"""
	fields, identity = SECTIONS[section]
	rows = []
	seen = set()

	for index, row in enumerate(document.get(section) or []):
		if not isinstance(row, dict):
			raise ExportError(CODE_ROW_INVALID, f"ردیف {index + 1} در «{section}» یک شیء JSON نیست.")

		values = project(row, fields)

		for column in identity:
			if values.get(column) in (None, ""):
				raise ExportError(
					CODE_ROW_INVALID,
					f"ردیف {index + 1} در «{section}» مقدار «{column}» را ندارد.",
				)

		key = tuple(values.get(column) for column in identity)

		if key in seen:
			raise ExportError(
				CODE_ROW_INVALID,
				f"ردیف {index + 1} در «{section}» تکراری است: {key}.",
			)

		seen.add(key)
		rows.append(values)

	return tuple(rows)


def serial_rows(document):
	return rows_of(document, "serials")


def check_transfer_records(document):
	"""Every way an archive of transfer records can disagree with itself, refused.

	A digest proves a file was not edited *by accident*; it proves nothing about a file that was
	edited and then digested again, because the digest is computed from the file. What cannot be
	forged by recomputing it is **consistency**: a record whose header counts its rows, and whose
	rows name it, either agrees with itself or it does not whatever the digest says.

	Five rules, each of which an archive of records actually breaks in practice:

	- a child row must name a record that is in the same file — the "missing parent" case, which
	  otherwise reaches the database as a row attached to nothing;
	- a record's own counts must equal what its rows say, so a restored record cannot claim ten
	  moves and hold three;
	- its outcomes must add up to its source total, because every serial the cancelled invoice held
	  came out placed, refused, or unselected, and a file that says otherwise is a file made by hand;
	- a refused row must carry a reason and a moved row must not, because the reason is what the
	  operator is shown and a `Moved` row with one is a row nobody can read;
	- **a record must be submitted.** Every record this app writes is inserted at `docstatus = 1`,
	  and I24's promise — a record nobody can change or delete — is the promise of a *submitted*
	  document. A file that claims otherwise is a file somebody made, and a draft placed here would
	  be a record that can be edited and deleted like any other draft: the one state that must not
	  exist. Refused whole, like the rest.

	Read-only and pure: it takes the document and raises, so both previews and both restores refuse
	the same files for the same reasons before anything is planned.
	"""
	records = {row.get("name") for row in transfer_rows(document)}
	rows = []

	for index, row in enumerate(transfer_item_rows(document)):
		parent = row.get("parent")

		if parent not in records:
			raise ExportError(
				CODE_ROW_INVALID,
				f"ردیف {index + 1} به سابقهٔ «{parent}» اشاره می‌کند که در همین فایل نیست.",
			)

		result = row.get("result")

		if result not in TRANSFER_RESULTS:
			raise ExportError(
				CODE_ROW_INVALID,
				f"نتیجهٔ «{result}» در ردیف {index + 1} در این قالب شناخته‌شده نیست.",
			)

		if result == "Refused" and not row.get("reason"):
			raise ExportError(
				CODE_ROW_INVALID,
				f"ردیف {index + 1} رد شده است و دلیلی ندارد.",
			)

		if result == "Moved" and row.get("reason"):
			raise ExportError(
				CODE_ROW_INVALID,
				f"ردیف {index + 1} منتقل شده است و دلیل رد دارد.",
			)

		rows.append(row)

	for record in transfer_rows(document):
		mine = [row for row in rows if row.get("parent") == record.get("name")]
		moved = len([row for row in mine if row.get("result") == "Moved"])
		refused = len([row for row in mine if row.get("result") == "Refused"])
		acknowledged = len([row for row in mine if int(row.get("acknowledged") or 0)])
		name = record.get("name")

		if int(record.get("docstatus") or 0) != DOCSTATUS_SUBMITTED:
			raise ExportError(
				CODE_ROW_INVALID,
				f"سابقهٔ «{name}» ثبت‌شده نیست؛ سابقه‌ای که بازآوری می‌شود باید ثبت‌شده باشد.",
			)

		if int(record.get("moved_count") or 0) != moved:
			raise ExportError(
				CODE_ROW_INVALID,
				f"سابقهٔ «{name}» می‌گوید {record.get('moved_count')} سریال منتقل شده و ردیف‌هایش {moved} تا هستند.",
			)

		if int(record.get("refused_count") or 0) != refused:
			raise ExportError(
				CODE_ROW_INVALID,
				f"سابقهٔ «{name}» می‌گوید {record.get('refused_count')} سریال رد شده و ردیف‌هایش {refused} تا هستند.",
			)

		total = moved + refused + int(record.get("unselected_count") or 0)

		if total != int(record.get("source_total") or 0):
			raise ExportError(
				CODE_ROW_INVALID,
				f"سابقهٔ «{name}»: منتقل‌شده، رد‌شده و انتخاب‌نشده با سریال‌های مبدأ جمع نمی‌شود.",
			)

		if acknowledged != int(record.get("acknowledged_count") or 0):
			raise ExportError(
				CODE_ROW_INVALID,
				f"سابقهٔ «{name}» می‌گوید {record.get('acknowledged_count')} سریال تأییدشده دارد و ردیف‌هایش {acknowledged} تا هستند.",
			)

	return True


def assignment_rows(document):
	return rows_of(document, "assignments")


def transfer_rows(document):
	return rows_of(document, "transfers")


def transfer_item_rows(document):
	return rows_of(document, "transfer_items")


# --- planning an import ---------------------------------------------------------------


def world_names(document):
	"""The item, company and invoice names a document mentions, for the caller to resolve.

	The plan asks whether *these* exist on the site. A world assembled only from what is already
	stored answers that question about the wrong set: measured in the isolated lab, a five-row
	export imported into a freshly reinstalled site — whose tables are empty by definition —
	reported every row as `ITEM_MISSING`, with the item sitting on the site the whole time. The
	names have to come from the document, because the document is what is asking.

	**Every section's names**, and the transfer records' were the one section left out: a record
	names the invoice it moved serials *to*, and the record mode asks whether that invoice is here.
	Measured in the lab — a recovery rehearsal, on a file whose assignment section was empty
	because the registrations had since been removed — every record came back `INVOICE_MISSING`
	with its amendment sitting on the site, which is the same failure the paragraph above records
	for items, one section further on.
	"""
	serials = serial_rows(document)
	assignments = assignment_rows(document)
	records = transfer_rows(document)

	return {
		"item_codes": tuple(
			sorted(
				{row.get("item_code") for row in serials if row.get("item_code")}
				| {row.get("item_code") for row in assignments if row.get("item_code")}
			)
		),
		"company_names": tuple(
			sorted(
				{row.get("company") for row in assignments if row.get("company")}
				| {row.get("company") for row in records if row.get("company")}
			)
		),
		"invoice_names": tuple(
			sorted(
				{row.get("sales_invoice") for row in assignments if row.get("sales_invoice")}
				| {row.get("source_invoice") for row in records if row.get("source_invoice")}
				| {row.get("target_invoice") for row in records if row.get("target_invoice")}
			)
		),
		# The serial masters the document names, for the caller to look up by name. A serial
		# master's name is unique over the site and cannot be scoped to a company, so this is the
		# one question about a serial a restricted caller is always answered.
		"serial_names": tuple(sorted({row.get("name") for row in serials if row.get("name")})),
	}


def serial_identity(row):
	return tuple(row.get(column) for column in SERIAL_IDENTITY)


def assignment_identity(row):
	return tuple(row.get(column) for column in ASSIGNMENT_IDENTITY)


@dataclass(frozen=True)
class World:
	"""What the site looks like, as the planner needs to see it.

	Assembled by the caller from permission-scoped reads. Kept as plain data so that every rule
	below is a pure function over it, and every rule is testable without a database.
	"""

	# (item_code, normalized) for every serial this site already has.
	serial_identities: frozenset = frozenset()
	# The **names** of the serial masters this site already has, for the names the document
	# mentions. Asked beside the identity above because the two are scoped differently: an
	# identity is only visible through an assignment in a company the caller can reach, while a
	# name cannot be scoped at all — `Logistics Serial` has no company column — and is unique over
	# the whole site. So a name that is taken is a master that exists, whatever this caller's
	# world shows, and creating it again collides on the primary key. Measured in the lab: a
	# restricted caller restoring a file that named another company's serial lost the whole
	# request to `DuplicateEntryError`, reported as a concurrent request by somebody else.
	serial_names: frozenset = frozenset()
	# (item_code, normalized) -> that live row's name, for mapping an assignment onto it.
	serials_by_identity: dict = field(default_factory=dict)
	items: frozenset = frozenset()
	companies: frozenset = frozenset()
	# invoice name -> its company, for the invoices the caller may see.
	invoices: dict = field(default_factory=dict)
	# Invoices that are **cancelled**. A cancelled invoice takes no registration — the rule
	# `core.authz.require_live_invoice` applies to every other write path in this app — and a
	# restored file is not a way around it. It is a set of names rather than a flag on
	# `invoices`, because the plan asks the two questions in different places and a reader should
	# be able to see which one is being asked. Kept beside the company rather than instead of it:
	# a cancelled invoice in a company the caller cannot reach is still refused, by whichever
	# check comes first.
	cancelled_invoices: frozenset = frozenset()
	# (invoice, row name) for every item row of those invoices.
	invoice_rows: frozenset = frozenset()
	# The names of the transfer records and their rows that already exist here. The naming series
	# is per site, so a name is not an identity across sites — which is why this is asked at all.
	transfers: frozenset = frozenset()
	transfer_items: frozenset = frozenset()
	# (invoice, normalized) -> the serial row name the live assignment points at.
	assignments_by_identity: dict = field(default_factory=dict)
	# (invoice, row name) -> how many serials that row is allowed to carry, which is its invoiced
	# quantity: the same number `core.validation` refuses a registration past. A row this site
	# holds but the caller did not describe has no entry, and is then not checked — the two reads
	# come from one query (`gateway.transfer_world`), so an entry is missing only when the row is.
	row_capacity: dict = field(default_factory=dict)
	# (invoice, row name) -> how many serials that row already carries here, counted from the same
	# read of stored assignments the duplicate check uses. Without it a file could place a row's
	# whole capacity again on a row that is already full.
	row_registered: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RestoreStamp:
	"""Who brought a record here, and when. The *where* is the file's own and read from it.

	Passed in rather than read, because `plan` is pure and a clock and a session user are neither.
	It is required by `MODE_RECORDS` and refused if absent: a record that cannot say it was
	restored is a record that claims to have been performed here, and that is the one thing this
	path must never produce.

	`from_site` is deliberately **not** here. It is the site the archive was exported from, which
	the document itself carries, and a caller that passed it in could pass the wrong one — the site
	being restored *into* is the confusion this field exists to prevent.
	"""

	by: str = ""
	on: str = ""


def plan(document, world, *, mode=MODE_LIVE, stamp=None):
	"""What to create, what to skip, and what this site cannot place.

	The order of the checks is the order a reader would ask them: is this serial already here, is
	the item here, is the company here, is the invoice here, is that row on that invoice, and does
	the assignment name a serial this site will have. The first answer that applies wins, so one
	row produces one reason rather than a pile of them.

	`mode` says which of the three acts this is, and the three are disjoint: an assignment on a live
	invoice, an assignment on a cancelled one, or a transfer record. See `MODES` for what each
	places and what it reports instead. A mode outside the three is refused rather than treated as a
	default, because the default here is the one that permits the least and a typo must not become
	permission.
	"""
	if mode not in MODES:
		raise ExportError(CODE_BAD_SHAPE, f"حالت ناشناخته برای بازآوری: {mode!r}")

	if mode == MODE_RECORDS:
		return _plan_records(document, world, stamp)

	# Both assignment modes create the serials the file carries: a serial is the registry of what a
	# serial *is*, not a claim about an invoice, and a restore that refused to create one would
	# leave every row of the file unresolvable.
	create_serials = []
	skip_serials = []
	conflicts = []
	# Serials this import will create, so an assignment may name one of them.
	arriving = set()

	for row in serial_rows(document):
		identity = serial_identity(row)

		if identity in world.serial_identities:
			skip_serials.append(row)
			continue

		# The same serial seen from the other side: this caller's world does not show the identity,
		# because the only assignment that carries it is in a company they cannot see, but the name
		# is taken and a name is unique. Creating it would collide on the primary key and take the
		# whole file down with it — including the rows this caller *may* place. Skipped, which is
		# what a serial this site already has is; the assignment that names it is refused a few
		# lines below, by its invoice or its company.
		if row.get("name") in world.serial_names:
			skip_serials.append(row)
			continue

		if row.get("item_code") not in world.items:
			conflicts.append(Conflict(row.get("name"), REASON_ITEM_MISSING, str(row.get("item_code"))))
			continue

		create_serials.append(row)
		arriving.add(identity)

	create_assignments = []
	skip_assignments = []
	# What this document itself puts on each row, as it is read: the capacity check below has to
	# count the rows of the same file, or a file naming one row ten times would pass ten times.
	placed = {}

	for row in assignment_rows(document):
		identity = assignment_identity(row)
		existing = world.assignments_by_identity.get(identity)
		resolved = world.serials_by_identity.get(serial_identity(row))

		if existing is not None:
			# The invoice already carries this serial. That is the state the import is trying to
			# reach, so it is skipped — unless the row that carries it is a *different* serial
			# row, which is a link this import must not silently bless.
			if existing == row.get("logistics_serial") or (resolved is not None and existing == resolved):
				skip_assignments.append(row)
			else:
				conflicts.append(
					Conflict(
						row.get("name"),
						REASON_LINK_CONFLICT,
						f"{row.get('sales_invoice')} / {row.get('normalized_serial')} → {existing}",
					)
				)

			continue

		conflict = _assignment_conflict(row, world, resolved, arriving, placed, mode)

		if conflict:
			conflicts.append(conflict)
			continue

		# The link is re-pointed at the row the serial actually is, when this site already holds
		# that identity under a different name. Without this the assignment would name the
		# document the file came from, which does not exist here — and Frappe's own link
		# validation would refuse the insert and roll the whole import back over one rename.
		if resolved:
			row = {**row, "logistics_serial": resolved}

		row_key = (row.get("sales_invoice"), row.get("sales_invoice_item"))
		placed[row_key] = placed.get(row_key, 0) + 1

		create_assignments.append(row)

	return ImportPlan(
		serials=tuple(create_serials),
		assignments=tuple(create_assignments),
		skipped_serials=tuple(skip_serials),
		skipped_assignments=tuple(skip_assignments),
		conflicts=tuple(conflicts),
		records_in_file=len(transfer_rows(document)),
	)


def _plan_records(document, world, stamp):
	"""The transfer records the file carries, if this site can hold them — and as whole records.

	Two passes, and the second is why: a record and its rows are **one artefact**, and a record
	placed without all of its rows is a record that lies about what happened. So a record is refused
	whole when any of its rows cannot be placed, and the reason is reported against the record rather
	than against the row — a refused record's rows are not reported again, because the record carries
	the reason and eighteen lines about its rows are the same fact eighteen times.

	What a record needs to be placeable:

	- **its name free.** The naming series is per site (`LST-.YYYY.-.#####`), so two sites both
	  produce `LST-2026-00001` for different transfers. A collision is refused and reported, never
	  resolved: overwriting would destroy an audit record and renaming would lose the identity the
	  archive exists to carry.
	- **its target invoice here and not cancelled.** `target_invoice` is a Link, and the framework
	  refuses to save a link to a cancelled document — so a record whose amendment was itself
	  cancelled since cannot be placed at all, which is a fact the operator is told rather than a
	  traceback.
	- **every serial its rows name present on this site.** Not "in the file": this mode places no
	  serials, so one that exists only in the file would leave the row pointing at nothing. The
	  message names the identity, and the ordinary restore is what puts it there first.

	The **record** is stamped with where the archive came from, when it arrived here and who brought
	it, overwriting whatever the file carried for the same three fields. The marker describes *this*
	document's arrival, and a chain of restores is a structure this format does not have. Its child
	rows are not stamped: they are the record's own table, they describe the transfer rather than
	its arrival, and the child DocType has no such fields to stamp.
	"""
	if stamp is None:
		raise ExportError(
			CODE_BAD_SHAPE,
			"بازآوری سابقه‌ها بدون نشانهٔ بازآوری ممکن نیست: سابقه‌ای که نتواند بگوید بازآوری شده است،"
			" ادعا می‌کند همین‌جا انجام شده.",
		)

	conflicts = []
	held = []
	source_site = str(document.get("site") or "")

	for row in transfer_rows(document):
		name = row.get("name")
		target = row.get("target_invoice")

		if name in world.transfers:
			conflicts.append(Conflict(name, REASON_RECORD_EXISTS, str(name)))
			continue

		if target not in world.invoices:
			conflicts.append(Conflict(name, REASON_INVOICE_MISSING, str(target)))
			continue

		if target in world.cancelled_invoices:
			conflicts.append(Conflict(name, REASON_RECORD_LINK_CANCELLED, str(target)))
			continue

		held.append(row)

	# Every row of a held record, so a record is refused whole when one of its rows cannot come.
	mine = {}

	for row in transfer_item_rows(document):
		mine.setdefault(row.get("parent"), []).append(row)

	placed = []
	placed_items = []

	for row in held:
		name = row.get("name")
		rows = mine.get(name, [])
		blocked = None

		for item in rows:
			if item.get("name") in world.transfer_items:
				blocked = Conflict(name, REASON_RECORD_EXISTS, str(item.get("name")))
				break

			identity = serial_identity(item)

			if identity not in world.serial_identities:
				blocked = Conflict(name, REASON_SERIAL_UNRESOLVED, f"{identity[0]} / {identity[1]}")
				break

		if blocked:
			conflicts.append(blocked)
			continue

		placed.append(
			{
				**row,
				# Written rather than carried, like the marker beside it: a restored record is
				# submitted because a record is submitted, and `check_transfer_records` has already
				# refused a file that says otherwise. Two answers to the same question would be one
				# too many, and this is the one that decides what the database holds.
				"docstatus": DOCSTATUS_SUBMITTED,
				"restored_from": source_site,
				"restored_on": stamp.on,
				"restored_by": stamp.by,
			}
		)
		placed_items.extend(rows)

	return ImportPlan(
		transfers=tuple(placed),
		transfer_items=tuple(placed_items),
		conflicts=tuple(conflicts),
		records_in_file=len(transfer_rows(document)),
	)


def _assignment_conflict(row, world, resolved, arriving, placed, mode):
	invoice = row.get("sales_invoice")
	identity = serial_identity(row)

	if row.get("item_code") not in world.items:
		return Conflict(row.get("name"), REASON_ITEM_MISSING, str(row.get("item_code")))

	if row.get("company") not in world.companies:
		return Conflict(row.get("name"), REASON_COMPANY_MISSING, str(row.get("company")))

	if invoice not in world.invoices:
		return Conflict(row.get("name"), REASON_INVOICE_MISSING, str(invoice))

	# A cancelled invoice, before every other question about the row: which mode this is decides
	# the whole of what may be added to one, so it is answered before the row is measured against
	# anything else.
	#
	# In the ordinary restore nothing may be added to it, whatever the rest of the file says — the
	# same rule the station's own endpoints apply, applied to the one write path that does not go
	# through `core.validation`. The row is reported and left alone, so a file that mixes a
	# cancelled invoice with live ones restores the live rows and names the others, exactly as any
	# other conflict does.
	#
	# In the historical restore it is the only thing that may be added to it, and a row on a live
	# invoice is sent back to the endpoint that owns it.
	if mode == MODE_HISTORY:
		if invoice not in world.cancelled_invoices:
			return Conflict(row.get("name"), REASON_NOT_HISTORY, str(invoice))
	elif invoice in world.cancelled_invoices:
		return Conflict(row.get("name"), REASON_INVOICE_CANCELLED, str(invoice))

	invoice_company = world.invoices.get(invoice)

	if invoice_company and row.get("company") and invoice_company != row.get("company"):
		return Conflict(row.get("name"), REASON_COMPANY_MISMATCH, f"{row.get('company')} ≠ {invoice_company}")

	row_key = (invoice, row.get("sales_invoice_item"))

	if row_key not in world.invoice_rows:
		return Conflict(row.get("name"), REASON_INVOICE_ROW_MISSING, str(row.get("sales_invoice_item")))

	if resolved is None and identity not in arriving:
		return Conflict(row.get("name"), REASON_SERIAL_UNRESOLVED, f"{identity[0]} / {identity[1]}")

	# Last, because it is the last question a reader asks: the row exists, so may it hold one
	# more? A row may never carry more serials than its invoiced quantity — the rule
	# `core.validation` enforces on every registration — and an import is a registration too.
	# What the row will hold is what is already stored on it here, what this file has already
	# placed on it, and this one; a file that names the same row five times is measured as it
	# goes, not five times against an empty row.
	capacity = world.row_capacity.get(row_key)

	if capacity is not None:
		held = world.row_registered.get(row_key, 0) + placed.get(row_key, 0) + 1

		if held > capacity:
			return Conflict(row.get("name"), REASON_EXCESS_QUANTITY, f"{held} > {capacity}")

	return None
