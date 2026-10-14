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
"""

import hashlib
import json
from dataclasses import dataclass, field

FORMAT = "smartprocess-logistics-serials"

# Raised when the file's shape changes. An older app refuses a newer file; a newer app reads an
# older one, which is what makes an export a durable artifact rather than a one-version stopgap.
FORMAT_VERSION = 1

SERIAL_DOCTYPE = "Logistics Serial"
ASSIGNMENT_DOCTYPE = "Logistics Serial Assignment"

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

SECTIONS = {
	"serials": (SERIAL_FIELDS, SERIAL_IDENTITY),
	"assignments": (ASSIGNMENT_FIELDS, ASSIGNMENT_IDENTITY),
}

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
REASON_INVOICE_ROW_MISSING = "INVOICE_ROW_MISSING"
REASON_COMPANY_MISMATCH = "COMPANY_MISMATCH"
REASON_SERIAL_UNRESOLVED = "SERIAL_UNRESOLVED"
REASON_LINK_CONFLICT = "LINK_CONFLICT"

REASON_MESSAGES = {
	REASON_ITEM_MISSING: "کالا در این سایت وجود ندارد",
	REASON_COMPANY_MISSING: "شرکت در این سایت وجود ندارد",
	REASON_INVOICE_MISSING: "فاکتور فروش در این سایت وجود ندارد",
	REASON_INVOICE_ROW_MISSING: "ردیف فاکتور در این فاکتور وجود ندارد",
	REASON_COMPANY_MISMATCH: "شرکت ردیف با شرکت فاکتور نمی‌خواند",
	REASON_SERIAL_UNRESOLVED: "سریال این ردیف نه در فایل هست و نه در سایت",
	REASON_LINK_CONFLICT: "این سریال از قبل روی همین فاکتور با سریال دیگری ثبت شده است",
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

	def summary(self):
		return {
			"to_create": {"serials": len(self.serials), "assignments": len(self.assignments)},
			"to_skip": {
				"serials": len(self.skipped_serials),
				"assignments": len(self.skipped_assignments),
			},
			"conflicts": len(self.conflicts),
			"by_reason": count_by_reason(self.conflicts),
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
		"counts": {"serials": len(serials), "assignments": len(assignments)},
		"serials": [project(row, SERIAL_FIELDS) for row in serials],
		"assignments": [project(row, ASSIGNMENT_FIELDS) for row in assignments],
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

	for key in SECTIONS:
		if not isinstance(document.get(key), list):
			raise ExportError(CODE_BAD_SHAPE, f"بخش «{key}» در فایل نیست یا فهرست نیست.")

	_declared_counts_agree(document)

	# The rows are checked here as well as by `plan`, so that every caller of `read` holds rows
	# whose identity fields are present and unique. `plan` re-derives them — it is a pure
	# function over the document — and re-deriving is cheaper than a second place that decides
	# what a valid row is.
	serial_rows(document)
	assignment_rows(document)

	return document


def _declared_counts_agree(document):
	"""The header's counts against the rows actually in the file.

	They cannot disagree in a file this app wrote, so a disagreement means the document was
	edited — and a digest that still matched would mean the digest is not covering what it claims
	to. Checking both is cheap and makes the header usable as a quick sanity read.
	"""
	declared = document.get("counts") or {}

	for key in SECTIONS:
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


def assignment_rows(document):
	return rows_of(document, "assignments")


# --- planning an import ---------------------------------------------------------------


def world_names(document):
	"""The item, company and invoice names a document mentions, for the caller to resolve.

	The plan asks whether *these* exist on the site. A world assembled only from what is already
	stored answers that question about the wrong set: measured in the isolated lab, a five-row
	export imported into a freshly reinstalled site — whose tables are empty by definition —
	reported every row as `ITEM_MISSING`, with the item sitting on the site the whole time. The
	names have to come from the document, because the document is what is asking.
	"""
	serials = serial_rows(document)
	assignments = assignment_rows(document)

	return {
		"item_codes": tuple(
			sorted(
				{row.get("item_code") for row in serials if row.get("item_code")}
				| {row.get("item_code") for row in assignments if row.get("item_code")}
			)
		),
		"company_names": tuple(sorted({row.get("company") for row in assignments if row.get("company")})),
		"invoice_names": tuple(
			sorted({row.get("sales_invoice") for row in assignments if row.get("sales_invoice")})
		),
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
	# (item_code, normalized) -> that live row's name, for mapping an assignment onto it.
	serials_by_identity: dict = field(default_factory=dict)
	items: frozenset = frozenset()
	companies: frozenset = frozenset()
	# invoice name -> its company, for the invoices the caller may see.
	invoices: dict = field(default_factory=dict)
	# (invoice, row name) for every item row of those invoices.
	invoice_rows: frozenset = frozenset()
	# (invoice, normalized) -> the serial row name the live assignment points at.
	assignments_by_identity: dict = field(default_factory=dict)


def plan(document, world):
	"""What to create, what to skip, and what this site cannot place.

	The order of the checks is the order a reader would ask them: is this serial already here, is
	the item here, is the company here, is the invoice here, is that row on that invoice, and does
	the assignment name a serial this site will have. The first answer that applies wins, so one
	row produces one reason rather than a pile of them.
	"""
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

		if row.get("item_code") not in world.items:
			conflicts.append(Conflict(row.get("name"), REASON_ITEM_MISSING, str(row.get("item_code"))))
			continue

		create_serials.append(row)
		arriving.add(identity)

	create_assignments = []
	skip_assignments = []

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

		conflict = _assignment_conflict(row, world, resolved, arriving)

		if conflict:
			conflicts.append(conflict)
			continue

		# The link is re-pointed at the row the serial actually is, when this site already holds
		# that identity under a different name. Without this the assignment would name the
		# document the file came from, which does not exist here — and Frappe's own link
		# validation would refuse the insert and roll the whole import back over one rename.
		if resolved:
			row = {**row, "logistics_serial": resolved}

		create_assignments.append(row)

	return ImportPlan(
		serials=tuple(create_serials),
		assignments=tuple(create_assignments),
		skipped_serials=tuple(skip_serials),
		skipped_assignments=tuple(skip_assignments),
		conflicts=tuple(conflicts),
	)


def _assignment_conflict(row, world, resolved, arriving):
	invoice = row.get("sales_invoice")
	identity = serial_identity(row)

	if row.get("item_code") not in world.items:
		return Conflict(row.get("name"), REASON_ITEM_MISSING, str(row.get("item_code")))

	if row.get("company") not in world.companies:
		return Conflict(row.get("name"), REASON_COMPANY_MISSING, str(row.get("company")))

	if invoice not in world.invoices:
		return Conflict(row.get("name"), REASON_INVOICE_MISSING, str(invoice))

	invoice_company = world.invoices.get(invoice)

	if invoice_company and row.get("company") and invoice_company != row.get("company"):
		return Conflict(row.get("name"), REASON_COMPANY_MISMATCH, f"{row.get('company')} ≠ {invoice_company}")

	if (invoice, row.get("sales_invoice_item")) not in world.invoice_rows:
		return Conflict(row.get("name"), REASON_INVOICE_ROW_MISSING, str(row.get("sales_invoice_item")))

	if resolved is None and identity not in arriving:
		return Conflict(row.get("name"), REASON_SERIAL_UNRESOLVED, f"{identity[0]} / {identity[1]}")

	return None
