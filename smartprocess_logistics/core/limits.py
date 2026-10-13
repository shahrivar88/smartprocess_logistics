"""What the Settings row is allowed to change about a payload's ceilings.

Four limits on `Logistics Settings` are documented as server behaviour — the file size, the
row count, the sheet count and the page size — and until this module existed nothing read them.
An administrator could lower the file cap to 100 KB and a 4 MB workbook would still be read.

Two rules make the settings safe to expose:

1. **A setting may tighten a ceiling, never raise it.** The reader's own default is the ceiling
   it was built and tested against; a settings row that asked for more would be asking for
   behaviour nothing has measured. So the effective limit is the smaller of the two, and a
   missing, zero or negative setting keeps the default.
2. **A limit the reader already enforces is enforced by the reader.** `inspect_archive`,
   `read_workbook` and `decode_text` all take their ceiling as an argument and raise their own
   coded error when it is passed. Tightening the argument is all that is needed; adding a second
   check here would be a second place for the rule to disagree with itself.
"""

from dataclasses import dataclass, replace

from smartprocess_logistics.core.archives import ArchiveLimits
from smartprocess_logistics.core.delimiters import MAX_TOKENS
from smartprocess_logistics.core.spreadsheet import SheetLimits
from smartprocess_logistics.core.textfile import MAX_TEXT_BYTES

KILOBYTE = 1024

# The page size the API falls back to and the hard cap it applies, matching the field defaults.
DEFAULT_PAGE_LENGTH = 50
MAX_PAGE_LENGTH = 500


def _capped(setting, default):
	"""The setting, when it is a usable number below the default; the default otherwise."""
	try:
		value = int(setting)
	except (TypeError, ValueError):
		return default

	if value <= 0:
		return default

	return min(value, default)


@dataclass(frozen=True)
class PayloadLimits:
	"""The ceilings one request reads a payload under."""

	max_bytes: int
	max_tokens: int
	archive: ArchiveLimits
	sheets: SheetLimits
	default_page_length: int
	max_page_length: int


def payload_limits(*, file_kb=None, rows=None, sheets=None, default_page=None, max_page=None):
	"""Build the ceilings from the Settings values, each one capped by its own default.

	Every ceiling keeps **its own** default rather than a shared one. The text reader's ceiling
	is 4 MB and the workbook reader's is 8 MB; deriving both from the smaller number would refuse
	a 6 MB workbook that nothing asked to refuse.
	"""
	archive = ArchiveLimits()
	sheet_limits = SheetLimits()

	text_bytes = _capped(file_kb, MAX_TEXT_BYTES // KILOBYTE) * KILOBYTE
	archive_bytes = _capped(file_kb, archive.max_file_bytes // KILOBYTE) * KILOBYTE

	max_rows = _capped(rows, sheet_limits.max_rows)
	max_sheets = _capped(sheets, sheet_limits.max_sheets)

	return PayloadLimits(
		max_bytes=text_bytes,
		# A text file has no rows, only tokens, and a token is at most one row's worth of
		# serial. The same setting bounds both, so an administrator who lowers the row cap
		# lowers what a text file may contain too.
		max_tokens=_capped(rows, MAX_TOKENS),
		archive=replace(
			archive,
			max_file_bytes=archive_bytes,
			max_worksheets=min(max_sheets, archive.max_worksheets),
		),
		# The row cap is the one the operator asked for. `max_cells` stays at the reader's own
		# default: deriving it from the rows and columns would refuse a legitimately wide sheet
		# that fits under every cap the operator actually set.
		sheets=replace(
			sheet_limits,
			max_sheets=min(max_sheets, sheet_limits.max_sheets),
			max_rows=max_rows,
		),
		default_page_length=_capped(default_page, DEFAULT_PAGE_LENGTH),
		max_page_length=_capped(max_page, MAX_PAGE_LENGTH),
	)
