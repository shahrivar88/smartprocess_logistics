"""Reading worksheet cells, and the policy that decides which cells may be read.

The module is split at one seam so that the policy is testable without the reader:

- `build_sheet_rows(raw_sheets, limits)` takes plain `(value, data_type)` pairs and applies
  every limit and every refusal. It imports nothing and needs no library, so its tests run on
  a machine with no `openpyxl`.
- `iter_workbook_sheets(workbook)` turns a real `openpyxl` workbook into those pairs, and
  `read_workbook(data, limits)` opens one from bytes.

**Formulas are read with `data_only=False`.** That is the opposite of the obvious choice and
it is the safe one. With `data_only=True` a formula cell returns its cached value, and a
workbook that was written by a tool that never calculated returns `None` instead: the formula
disappears and an empty cell takes its place. Reading with `data_only=False` makes a formula
visible as `data_type == 'f'`, which is then refused by name. Nothing this app reads is a
formula, so refusing them costs nothing and hiding them costs correctness.

A numeric cell is accepted, and the row records that it was numeric. A serial stored as a
number has already lost a leading zero inside the file, before this app ever sees it; the
report says so once per import instead of silently reporting a different serial.
"""

from dataclasses import dataclass

from smartprocess_logistics.core.units import describe_bytes

# `data_type` values this app reads. Everything else is refused by name.
READABLE_DATA_TYPES = frozenset({"", "n", "s", "str"})

FORMULA_DATA_TYPES = frozenset({"f"})
ERROR_DATA_TYPES = frozenset({"e"})

CODE_CELL_FORMULA = "CELL_IS_A_FORMULA"
CODE_CELL_ERROR_VALUE = "CELL_HAS_AN_ERROR_VALUE"
CODE_CELL_UNSUPPORTED_TYPE = "CELL_UNSUPPORTED_TYPE"
CODE_CELL_TOO_LONG = "CELL_TEXT_TOO_LONG"
CODE_TOO_MANY_ROWS = "SHEET_TOO_MANY_ROWS"
CODE_TOO_MANY_COLUMNS = "SHEET_TOO_MANY_COLUMNS"
CODE_TOO_MANY_CELLS = "SHEET_TOO_MANY_CELLS"
CODE_NO_SHEETS = "WORKBOOK_HAS_NO_SHEETS"
CODE_TOO_MANY_SHEETS = "WORKBOOK_TOO_MANY_SHEETS"
CODE_READER_MISSING = "WORKBOOK_READER_MISSING"
CODE_TOO_LARGE_DECLARED = "WORKBOOK_TOO_LARGE_DECLARED"


MEGABYTE = 1024 * 1024


@dataclass(frozen=True)
class SheetLimits:
	"""The ceilings a workbook has to stay under."""

	max_sheets: int = 20
	max_rows: int = 5000
	max_columns: int = 64
	max_cells: int = 200_000
	max_text_length: int = 256
	# The archive preflight measures the declared uncompressed size, and the reader refuses to
	# open anything that declares more than this. The row, column and cell limits stop the read
	# as it goes; this one stops it before it starts.
	max_declared_uncompressed_bytes: int = 32 * MEGABYTE


@dataclass(frozen=True)
class CellValue:
	"""One accepted cell."""

	text: str
	column: int
	was_numeric: bool

	@property
	def is_blank(self):
		return not self.text


@dataclass(frozen=True)
class SheetRow:
	"""One accepted row of one sheet."""

	sheet: str
	number: int
	cells: tuple

	def texts(self):
		return tuple(cell.text for cell in self.cells)

	def first_filled_column(self):
		for cell in self.cells:
			if cell.text:
				return cell.column

		return None


@dataclass(frozen=True)
class WorkbookReport:
	"""What an accepted workbook contained."""

	sheets: tuple
	rows: int
	cells: int
	numeric_cells: int


class SheetError(ValueError):
	"""A worksheet that cannot be used. `code` is stable."""

	def __init__(self, code, message, sheet=None, row=None, column=None):
		super().__init__(message)
		self.code = code
		self.message = message
		self.sheet = sheet
		self.row = row
		self.column = column

	def where(self):
		"""A short location, for a preview that lists problems."""
		parts = [part for part in (self.sheet, self.row, self.column) if part not in (None, "")]
		return " · ".join(str(part) for part in parts)


def number_text(value):
	"""A number as the text an operator would have typed for it."""
	if isinstance(value, bool):
		return str(value)
	if isinstance(value, int):
		return str(value)
	if float(value).is_integer():
		return str(int(value))

	return repr(float(value))


def cell_text(value, data_type, *, limits=None, sheet=None, row=None, column=None):
	"""The text of one cell, or raise `SheetError`.

	`value` and `data_type` are the two things an `openpyxl` cell exposes, and the only two
	this function needs. Anything with the same two attributes works, which is what lets the
	policy be tested without the library.
	"""
	limits = limits or SheetLimits()
	data_type = data_type or ""

	if data_type in FORMULA_DATA_TYPES:
		raise SheetError(CODE_CELL_FORMULA, "سلول دارای فرمول است.", sheet, row, column)

	if data_type in ERROR_DATA_TYPES:
		raise SheetError(CODE_CELL_ERROR_VALUE, "سلول دارای مقدار خطاست.", sheet, row, column)

	if data_type not in READABLE_DATA_TYPES:
		raise SheetError(
			CODE_CELL_UNSUPPORTED_TYPE,
			f"نوع دادهٔ سلول پشتیبانی نمی‌شود: {data_type}.",
			sheet,
			row,
			column,
		)

	if value is None:
		return "", False

	was_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
	text = number_text(value) if was_numeric else str(value)
	text = text.strip()

	if len(text) > limits.max_text_length:
		raise SheetError(
			CODE_CELL_TOO_LONG,
			f"متن سلول بیش از {limits.max_text_length} نویسه دارد.",
			sheet,
			row,
			column,
		)

	return text, was_numeric


def build_sheet_rows(raw_sheets, limits=None):
	"""Turn `(sheet_name, rows)` pairs into accepted sheet rows.

	Each row is an iterable of `(value, data_type)` pairs. Every limit and every refusal is
	applied here, so a caller that has an `openpyxl` workbook only has to produce those pairs.
	"""
	limits = limits or SheetLimits()
	raw_sheets = tuple(raw_sheets)

	if not raw_sheets:
		raise SheetError(CODE_NO_SHEETS, "کاربرگ در فایل وجود ندارد.")

	if len(raw_sheets) > limits.max_sheets:
		raise SheetError(CODE_TOO_MANY_SHEETS, f"تعداد شیت‌ها بیش از {limits.max_sheets} است.")

	rows = []
	total_cells = 0
	numeric_cells = 0

	for sheet_name, sheet_rows in raw_sheets:
		for row_number, raw_cells in enumerate(sheet_rows, start=1):
			if row_number > limits.max_rows:
				raise SheetError(
					CODE_TOO_MANY_ROWS,
					f"تعداد ردیف‌های شیت بیش از {limits.max_rows} است.",
					sheet_name,
					row_number,
				)

			raw_cells = tuple(raw_cells)

			if len(raw_cells) > limits.max_columns:
				raise SheetError(
					CODE_TOO_MANY_COLUMNS,
					f"تعداد ستون‌های شیت بیش از {limits.max_columns} است.",
					sheet_name,
					row_number,
				)

			total_cells += len(raw_cells)

			if total_cells > limits.max_cells:
				raise SheetError(
					CODE_TOO_MANY_CELLS,
					f"تعداد کل سلول‌ها بیش از {limits.max_cells} است.",
					sheet_name,
					row_number,
				)

			cells = []

			for column, (value, data_type) in enumerate(raw_cells, start=1):
				text, was_numeric = cell_text(
					value,
					data_type,
					limits=limits,
					sheet=sheet_name,
					row=row_number,
					column=column,
				)
				numeric_cells += 1 if was_numeric else 0
				cells.append(CellValue(text=text, column=column, was_numeric=was_numeric))

			rows.append(SheetRow(sheet=sheet_name, number=row_number, cells=tuple(cells)))

	return tuple(rows), WorkbookReport(
		sheets=tuple(sheet_name for sheet_name, _ in raw_sheets),
		rows=len(rows),
		cells=total_cells,
		numeric_cells=numeric_cells,
	)


def iter_workbook_sheets(workbook):
	"""Yield `(sheet_name, rows)` from an `openpyxl` workbook.

	Every cell is taken with `data_only=False`, so a formula arrives as a formula.
	"""
	for worksheet in workbook.worksheets:
		yield (
			worksheet.title,
			(tuple((cell.value, cell.data_type) for cell in row) for row in worksheet.iter_rows()),
		)


def read_workbook(data, limits=None, archive=None):
	"""Open an accepted `.xlsx` payload and return `(rows, report)`.

	The archive preflight runs **here** as well as in the caller, so the limits apply to the
	real read and not only to a separate check somebody might forget to call. A preflight
	report may be passed in to avoid measuring the same bytes twice; the size guard applies
	either way.

	The row, column and cell limits are enforced by `build_sheet_rows` while the rows are
	consumed, and `read_only` makes the reader hand them over one at a time, so an oversized
	worksheet is stopped partway rather than materialised. The declared size is checked first
	because the reader allocates the shared string pool in full before it returns anything.

	Importing `openpyxl` is deferred to here so that nothing else in this package depends on
	it, and a missing library is reported as a code rather than an `ImportError`.
	"""
	from smartprocess_logistics.core.archives import inspect_archive

	limits = limits or SheetLimits()
	archive = archive or inspect_archive(data)

	if archive.uncompressed_bytes > limits.max_declared_uncompressed_bytes:
		raise SheetError(
			CODE_TOO_LARGE_DECLARED,
			f"حجم باز‌شدهٔ اعلام‌شدهٔ فایل بیش از {describe_bytes(limits.max_declared_uncompressed_bytes)} است.",
		)

	try:
		from openpyxl import load_workbook
	except ImportError as error:  # pragma: no cover - depends on the machine
		raise SheetError(
			CODE_READER_MISSING,
			"کتابخانهٔ openpyxl نصب نیست و خواندن XLSX ممکن نیست.",
		) from error

	import io

	workbook = load_workbook(
		io.BytesIO(bytes(data)),
		read_only=True,
		data_only=False,
		keep_links=False,
		keep_vba=False,
	)

	try:
		return build_sheet_rows(iter_workbook_sheets(workbook), limits)
	finally:
		workbook.close()
