"""Turning a parsed file into a registration preview.

The engine never writes anything. It reads a file, decides which invoice row each serial
belongs to, hands the result to `plan_registration`, and returns a preview an operator
confirms. Confirmation is a separate step in a later phase, and it re-validates everything
against the database before it writes.

Two questions are decided here, and they are separate:

- **What shape is the file?** A text file is a stream of tokens. A worksheet is read as
  headers that carry markers, as one column whose rows carry markers, or as one flat column.
- **How do the serials reach invoice rows?** Three layouts: `grouped` reads the row from a
  marker, `sequential` fills rows in invoice order up to each row's remaining need, and
  `single_row` puts everything on one named row.

A marker is never stored. `{n}` names the **position** of an item row on the invoice, and only
while the file is being read; the association that is stored uses the row's own name, which is
stable across invoice edits and across one file being read twice.
"""

from dataclasses import dataclass, field

from smartprocess_logistics.core import markers as marker_rules
from smartprocess_logistics.core.delimiters import DelimiterSet, split_tokens
from smartprocess_logistics.core.normalization import NormalizationPolicy
from smartprocess_logistics.core.quantities import required_units, row_coverage
from smartprocess_logistics.core.validation import (
	SEVERITY_ERROR,
	SEVERITY_WARNING,
	SerialIssue,
	plan_registration,
)

LAYOUT_GROUPED = "grouped"
LAYOUT_SEQUENTIAL = "sequential"
LAYOUT_SINGLE_ROW = "single_row"

LAYOUTS = (LAYOUT_GROUPED, LAYOUT_SEQUENTIAL, LAYOUT_SINGLE_ROW)

SHAPE_MARKER_HEADERS = "marker_headers"
SHAPE_GROUPED_COLUMN = "grouped_column"
SHAPE_FLAT_COLUMN = "flat_column"

MAX_TOKENS = 50_000
MAX_CANDIDATES = 50_000

CODE_LAYOUT_UNKNOWN = "IMPORT_LAYOUT_UNKNOWN"
CODE_NO_CANDIDATES = "IMPORT_NO_SERIALS"
CODE_ROW_INDEX_UNKNOWN = "IMPORT_ROW_INDEX_UNKNOWN"
CODE_SERIAL_BEFORE_MARKER = "IMPORT_SERIAL_BEFORE_MARKER"
CODE_MARKER_ROW_MISSING = "IMPORT_MARKER_ROW_MISSING"
CODE_TARGET_ROW_REQUIRED = "IMPORT_TARGET_ROW_REQUIRED"
CODE_TARGET_ROW_UNKNOWN = "IMPORT_TARGET_ROW_UNKNOWN"
CODE_MARKERS_IGNORED = "IMPORT_MARKERS_IGNORED"
CODE_SEQUENTIAL_UNCONFIRMED = "IMPORT_SEQUENTIAL_NOT_CONFIRMED"
CODE_BLANK_TOKENS = "IMPORT_BLANK_TOKENS_SKIPPED"
CODE_NUMERIC_CELLS = "IMPORT_NUMERIC_CELLS"
CODE_TOO_MANY_TOKENS = "IMPORT_TOO_MANY_TOKENS"


@dataclass(frozen=True)
class Candidate:
	"""One serial read from a file, with where it came from.

	`row_index` is the 1-based invoice row position a marker named, or `None` when the layout
	does not use markers.
	"""

	serial: str
	row_index: int | None = None
	sheet: str | None = None
	line: int | None = None

	def where(self):
		parts = [str(part) for part in (self.sheet, self.line) if part not in (None, "")]
		return " · ".join(parts)


@dataclass(frozen=True)
class ImportRequest:
	"""Everything the engine needs that does not come from the file.

	`row_mapping_confirmed` exists for one layout. `sequential` decides which invoice row a
	serial belongs to by counting, so a file whose row order does not match the invoice's
	assigns every serial to the wrong row with nothing in the file to say so. The preview shows
	the mapping it worked out; setting this flag is the operator saying they have read it. The
	engine refuses a sequential file that has not been confirmed rather than guessing on their
	behalf.
	"""

	rows: tuple
	layout: str = LAYOUT_GROUPED
	existing: tuple = ()
	delimiters: tuple = ()
	patterns: tuple = ()
	policy: NormalizationPolicy = field(default_factory=NormalizationPolicy)
	target_row_key: str | None = None
	row_mapping_confirmed: bool = False


@dataclass(frozen=True)
class GroupSummary:
	"""How one invoice row stands after the file is applied."""

	row_key: str
	item_code: str
	row_index: int
	required: int
	existing: int
	incoming: int
	shortage: int
	excess: int


@dataclass(frozen=True)
class ImportPreview:
	"""What the operator is asked to confirm."""

	layout: str
	candidates: int
	blank_tokens: int
	numeric_cells: int
	sheets: tuple
	encoding: str | None
	groups: tuple
	accepted: tuple
	issues: tuple
	row_mapping: tuple = ()
	requires_row_mapping_confirmation: bool = False
	delimiters: tuple = ()
	patterns: tuple = ()

	@property
	def errors(self):
		return tuple(issue for issue in self.issues if issue.severity == SEVERITY_ERROR)

	@property
	def warnings(self):
		return tuple(issue for issue in self.issues if issue.severity == SEVERITY_WARNING)

	@property
	def is_valid(self):
		return not self.errors

	def submissions(self):
		"""The `(row_key, serial text)` pairs the write step would apply."""
		return tuple((item.row_key, item.serial.original) for item in self.accepted)


def _issue(code, message, severity=SEVERITY_ERROR, row_key=None, serials=()):
	return SerialIssue(code=code, message=message, row_key=row_key, serials=serials, severity=severity)


def candidates_from_tokens(tokens, patterns, *, source=None):
	"""Annotate a token stream with the invoice row each serial belongs to.

	A token that is a marker changes the current row and is not itself a serial. A token that
	has a marker's shape but an unusable row number is refused rather than read as a serial.
	"""
	parsed_patterns = marker_rules.parse_patterns(patterns)
	candidates = []
	issues = []
	current_row = None
	blank = 0

	for line, raw_token in enumerate(tokens, start=1):
		token = raw_token.strip()

		if not token:
			blank += 1
			continue

		if parsed_patterns:
			match = marker_rules.lookup_marker(token, parsed_patterns)

			if match.pattern and match.row_index is None:
				issues.append(
					_issue(
						CODE_MARKER_ROW_MISSING,
						f"نشانگر «{token}» شمارهٔ ردیف معتبری ندارد.",
						serials=(token,),
					)
				)
				continue

			if match.pattern:
				current_row = match.row_index
				continue

		candidates.append(Candidate(serial=token, row_index=current_row, line=line, sheet=source))

		if len(candidates) > MAX_CANDIDATES:
			raise ValueError(f"تعداد سریال‌های فایل بیش از {MAX_CANDIDATES} است.")

	return tuple(candidates), tuple(issues), blank


def candidates_from_sheet_rows(rows, patterns, shape=None):
	"""Read worksheet rows and return `(candidates, issues, shape)`.

	The shape is decided by the file unless the caller names one:

	- **`marker_headers`** when the first filled row carries **more than one** marker. One
	  marker in the first row is not enough to tell a header row from the first marker of a
	  grouped column, and reading the wrong one turns the second marker into a serial. Two
	  markers settle it, and the single-column case is far more common, so a lone marker is
	  read as a grouped column unless the caller says otherwise.
	- **`grouped_column`** when a marker appears somewhere in the column.
	- **`flat_column`** when no marker appears at all.
	"""
	parsed_patterns = marker_rules.parse_patterns(patterns)
	filled_rows = [row for row in rows if any(cell.text for cell in row.cells)]

	if shape is not None and shape not in (SHAPE_MARKER_HEADERS, SHAPE_GROUPED_COLUMN, SHAPE_FLAT_COLUMN):
		raise ValueError(f"شکل شیت ناشناخته: {shape}")

	if not filled_rows:
		return (), (), shape or SHAPE_FLAT_COLUMN

	def marker_in(text):
		if not parsed_patterns or not text:
			return None
		return marker_rules.lookup_marker(text, parsed_patterns)

	header = filled_rows[0]
	head_markers = [(cell.column, marker_in(cell.text)) for cell in header.cells]
	marked_headers = [(column, match) for column, match in head_markers if match and match.pattern]

	if shape == SHAPE_MARKER_HEADERS or (shape is None and len(marked_headers) > 1):
		if not marked_headers:
			return (), (), SHAPE_MARKER_HEADERS
		return _candidates_from_marked_headers(filled_rows[1:], marked_headers)

	return _candidates_from_columns(filled_rows, marker_in, parsed_patterns)


def _candidates_from_marked_headers(data_rows, marked_headers):
	"""One column per marked header cell, each column belonging to that marker's row."""
	issues = []
	columns = []

	for column, match in marked_headers:
		if match.row_index is None:
			issues.append(_issue(CODE_MARKER_ROW_MISSING, f"نشانگر ستون {column} شمارهٔ ردیف معتبری ندارد."))
			continue
		columns.append((column, match.row_index))

	candidates = []

	for row in data_rows:
		for column, row_index in columns:
			if column > len(row.cells):
				continue

			text = row.cells[column - 1].text
			if not text.strip():
				continue

			candidates.append(
				Candidate(serial=text.strip(), row_index=row_index, sheet=row.sheet, line=row.number)
			)

	return tuple(candidates), tuple(issues), SHAPE_MARKER_HEADERS


def _candidates_from_columns(filled_rows, marker_in, parsed_patterns):
	"""One column whose rows are either a marker or a serial."""
	candidates = []
	issues = []
	current_row = None
	found_marker = False

	for row in filled_rows:
		column = row.first_filled_column()
		if column is None:
			continue

		text = row.cells[column - 1].text.strip()
		match = marker_in(text)

		if match and match.pattern:
			found_marker = True
			if match.row_index is None:
				issues.append(
					_issue(
						CODE_MARKER_ROW_MISSING,
						f"نشانگر «{text}» شمارهٔ ردیف معتبری ندارد.",
						serials=(text,),
					)
				)
				continue
			current_row = match.row_index
			continue

		candidates.append(Candidate(serial=text, row_index=current_row, sheet=row.sheet, line=row.number))

	shape = SHAPE_GROUPED_COLUMN if found_marker else SHAPE_FLAT_COLUMN

	return tuple(candidates), tuple(issues), shape


def _row_by_index(rows, index):
	"""The row at a 1-based invoice position, or `None`."""
	if not isinstance(index, int) or index < 1 or index > len(rows):
		return None

	return rows[index - 1]


def _remaining_need(request):
	"""How many labels each row still needs, keyed by row name."""
	existing_counts = {}

	for assignment in request.existing:
		existing_counts[assignment.row_key] = existing_counts.get(assignment.row_key, 0) + 1

	return {
		row.row_key: max(0, required_units(row.invoiced_qty) - existing_counts.get(row.row_key, 0))
		for row in request.rows
	}


def _to_submissions(candidates, request):
	"""Apply the layout and return `(submissions, issues)`."""
	issues = []
	submissions = []

	if request.layout not in LAYOUTS:
		raise ValueError(f"چیدمان ناشناخته: {request.layout}")

	marked = sum(1 for candidate in candidates if candidate.row_index is not None)

	if request.layout == LAYOUT_GROUPED:
		for candidate in candidates:
			if candidate.row_index is None:
				issues.append(
					_issue(
						CODE_SERIAL_BEFORE_MARKER,
						"سریال پیش از نخستین نشانگر آمده و ردیف آن مشخص نیست.",
						serials=(candidate.serial,),
					)
				)
				continue

			row = _row_by_index(request.rows, candidate.row_index)

			if row is None:
				issues.append(
					_issue(
						CODE_ROW_INDEX_UNKNOWN,
						f"نشانگر به ردیف {candidate.row_index} اشاره می‌کند که در فاکتور وجود ندارد.",
						serials=(candidate.serial,),
					)
				)
				continue

			submissions.append((row.row_key, candidate.serial))

		return submissions, issues

	if request.layout == LAYOUT_SINGLE_ROW:
		target = request.target_row_key

		if target is None and len(request.rows) == 1:
			target = request.rows[0].row_key

		if target is None:
			issues.append(_issue(CODE_TARGET_ROW_REQUIRED, "برای این چیدمان باید ردیف فاکتور انتخاب شود."))
			return submissions, issues

		if not any(row.row_key == target for row in request.rows):
			issues.append(_issue(CODE_TARGET_ROW_UNKNOWN, f"ردیف انتخابی در فاکتور نیست: {target}"))
			return submissions, issues

		if marked:
			issues.append(
				_issue(
					CODE_MARKERS_IGNORED,
					f"{marked} نشانگر در فایل نادیده گرفته شد، چون چیدمان تک‌ردیفی انتخاب شده است.",
					severity=SEVERITY_WARNING,
				)
			)

		return [(target, candidate.serial) for candidate in candidates], issues

	# Sequential: fill the rows in invoice order, each one up to what it still needs.
	if not request.row_mapping_confirmed:
		issues.append(
			_issue(
				CODE_SEQUENTIAL_UNCONFIRMED,
				"چیدمان ترتیبی ردیف هر سریال را با شمارش تعیین می‌کند."
				" نقشهٔ ردیف‌ها را در پیش‌نمایش ببینید و سپس تأیید کنید.",
			)
		)

	if marked:
		issues.append(
			_issue(
				CODE_MARKERS_IGNORED,
				f"{marked} نشانگر در فایل نادیده گرفته شد، چون چیدمان ترتیبی انتخاب شده است.",
				severity=SEVERITY_WARNING,
			)
		)

	remaining = _remaining_need(request)
	position = 0

	for candidate in candidates:
		while position < len(request.rows) and remaining.get(request.rows[position].row_key, 0) <= 0:
			position += 1

		if position >= len(request.rows):
			# Past the last row. The excess is reported by the plan against the last row.
			submissions.append((request.rows[-1].row_key, candidate.serial))
			continue

		row = request.rows[position]
		submissions.append((row.row_key, candidate.serial))
		remaining[row.row_key] = remaining.get(row.row_key, 0) - 1

	return submissions, issues


def build_preview(
	candidates, request, *, source_issues=(), blank_tokens=0, numeric_cells=0, sheets=(), encoding=None
):
	"""Validate a file's contents and return the preview an operator confirms."""
	if request.layout not in LAYOUTS:
		raise ValueError(f"چیدمان ناشناخته: {request.layout}")

	if not candidates:
		return ImportPreview(
			layout=request.layout,
			candidates=0,
			blank_tokens=blank_tokens,
			numeric_cells=numeric_cells,
			sheets=tuple(sheets),
			encoding=encoding,
			groups=(),
			accepted=(),
			issues=tuple(source_issues) + (_issue(CODE_NO_CANDIDATES, "هیچ سریالی در فایل پیدا نشد."),),
		)

	submissions, layout_issues = _to_submissions(candidates, request)

	plan = plan_registration(
		rows=request.rows,
		submissions=submissions,
		existing=request.existing,
		delimiters=request.delimiters,
		policy=request.policy,
	)

	issues = list(source_issues) + list(layout_issues)

	if blank_tokens:
		issues.append(
			_issue(
				CODE_BLANK_TOKENS,
				f"{blank_tokens} توکن خالی نادیده گرفته شد.",
				severity=SEVERITY_WARNING,
			)
		)

	if numeric_cells:
		issues.append(
			_issue(
				CODE_NUMERIC_CELLS,
				f"{numeric_cells} سلول به‌صورت عدد ذخیره شده بود؛ صفر ابتدایی آن‌ها قابل بازیابی نیست.",
				severity=SEVERITY_WARNING,
			)
		)

	issues += list(plan.issues)

	incoming = {}

	for item in plan.accepted:
		incoming[item.row_key] = incoming.get(item.row_key, 0) + 1

	existing_counts = {}

	for assignment in request.existing:
		existing_counts[assignment.row_key] = existing_counts.get(assignment.row_key, 0) + 1

	groups = []

	for position, row in enumerate(request.rows, start=1):
		coverage = row_coverage(
			row.row_key,
			row.item_code,
			row.invoiced_qty,
			existing_counts.get(row.row_key, 0) + incoming.get(row.row_key, 0),
		)
		groups.append(
			GroupSummary(
				row_key=row.row_key,
				item_code=row.item_code,
				row_index=position,
				required=coverage.required,
				existing=existing_counts.get(row.row_key, 0),
				incoming=incoming.get(row.row_key, 0),
				shortage=coverage.shortage,
				excess=coverage.excess,
			)
		)

	return ImportPreview(
		layout=request.layout,
		candidates=len(candidates),
		blank_tokens=blank_tokens,
		numeric_cells=numeric_cells,
		sheets=tuple(sheets),
		encoding=encoding,
		groups=tuple(groups),
		accepted=plan.accepted,
		issues=tuple(issues),
		row_mapping=_row_mapping(request.rows, submissions),
		requires_row_mapping_confirmation=request.layout == LAYOUT_SEQUENTIAL,
		delimiters=tuple(request.delimiters),
		patterns=tuple(request.patterns),
	)


def _row_mapping(rows, submissions):
	"""Which invoice row each submitted serial went to, and how many.

	The sequential layout decides this by counting, so the count is what the operator has to
	read before confirming.
	"""
	counts = {}

	for row_key, _serial in submissions:
		counts[row_key] = counts.get(row_key, 0) + 1

	return tuple(
		(row.row_key, row.item_code, position, counts.get(row.row_key, 0))
		for position, row in enumerate(rows, start=1)
	)


def preview_from_text(text, request, *, max_tokens=MAX_TOKENS):
	"""Read a decoded text file into a preview.

	`max_tokens` comes from the operator's row limit; see `core.limits`.
	"""
	if len(text) > max_tokens * 40:
		raise ValueError("متن ورودی برای درج بسیار بزرگ است.")

	tokens = split_tokens(text, DelimiterSet.of(request.delimiters), max_tokens=max_tokens)
	candidates, issues, blank = candidates_from_tokens(tokens, request.patterns)

	return build_preview(candidates, request, source_issues=issues, blank_tokens=blank)


def preview_from_sheet_rows(rows, request, *, report=None, encoding=None, shape=None):
	"""Read already-built worksheet rows into a preview."""
	candidates, issues, _shape = candidates_from_sheet_rows(rows, request.patterns, shape=shape)
	sheets = tuple(sorted({row.sheet for row in rows})) if rows else ()

	return build_preview(
		candidates,
		request,
		source_issues=issues,
		numeric_cells=report.numeric_cells if report else 0,
		sheets=sheets,
		encoding=encoding,
	)


def values_from_workbook(data, *, archive_limits=None, sheet_limits=None):
	"""Every nonempty cell of an `.xlsx` payload, in the order the intake reads it.

	Column by column, top to bottom, as texts. Nothing is interpreted, no row is chosen and no
	serial is validated: the values go to the window, which runs them through the same intake a
	paste goes through, and the invoice is written by `add_row_serials` when the operator saves.

	The archive preflight runs here, so a payload that is refused never reaches the reader.
	"""
	from smartprocess_logistics.core.archives import inspect_archive
	from smartprocess_logistics.core.spreadsheet import column_major_texts, read_workbook

	archive = inspect_archive(data, limits=archive_limits)
	rows, report = read_workbook(data, limits=sheet_limits, archive=archive)

	return column_major_texts(rows), report


def preview_from_workbook(data, request, *, archive_limits=None, sheet_limits=None, shape=None):
	"""Preflight, read and preview an `.xlsx` payload. Nothing is written anywhere."""
	from smartprocess_logistics.core.archives import inspect_archive
	from smartprocess_logistics.core.spreadsheet import read_workbook

	archive = inspect_archive(data, limits=archive_limits)
	rows, report = read_workbook(data, limits=sheet_limits, archive=archive)

	return preview_from_sheet_rows(rows, request, report=report, shape=shape)
