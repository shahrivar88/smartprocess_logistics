"""Worksheet reading policy, exercised without the XLSX library.

`build_sheet_rows` takes `(value, data_type)` pairs and applies every limit and every refusal,
so the whole policy is testable on a machine with no `openpyxl`. The reader itself is one thin
function and is covered by its own test at the end, which skips when the library is absent.
"""

import pathlib
import tempfile
import unittest

from smartprocess_logistics.core.spreadsheet import (
	CODE_CELL_ERROR_VALUE,
	CODE_CELL_FORMULA,
	CODE_CELL_TOO_LONG,
	CODE_CELL_UNSUPPORTED_TYPE,
	CODE_NO_SHEETS,
	CODE_TOO_MANY_CELLS,
	CODE_TOO_MANY_COLUMNS,
	CODE_TOO_MANY_ROWS,
	CODE_TOO_MANY_SHEETS,
	SheetError,
	SheetLimits,
	build_sheet_rows,
	cell_text,
	column_major_texts,
	number_text,
	read_workbook,
)
from smartprocess_logistics.tests.base import UnitTestCase

try:
	import openpyxl  # noqa: F401

	HAS_OPENPYXL = True
except ImportError:
	HAS_OPENPYXL = False


class TestCellText(UnitTestCase):
	def test_a_plain_string(self):
		self.assertEqual(cell_text("A1", "s"), ("A1", False))

	def test_surrounding_whitespace_is_trimmed(self):
		self.assertEqual(cell_text("  A1  ", "s"), ("A1", False))

	def test_an_empty_cell(self):
		self.assertEqual(cell_text(None, "n"), ("", False))

	def test_a_blank_string(self):
		self.assertEqual(cell_text("   ", "s"), ("", False))

	def test_an_integer(self):
		self.assertEqual(cell_text(123, "n"), ("123", True))

	def test_a_float_that_is_whole_is_read_as_an_integer(self):
		self.assertEqual(cell_text(123.0, "n"), ("123", True))

	def test_a_fractional_number_is_kept(self):
		self.assertEqual(cell_text(12.5, "n"), ("12.5", True))

	def test_a_formula_is_refused(self):
		with self.assertRaises(SheetError) as caught:
			cell_text("=A1&B1", "f")

		self.assertEqual(caught.exception.code, CODE_CELL_FORMULA)

	def test_an_error_value_is_refused(self):
		with self.assertRaises(SheetError) as caught:
			cell_text("#REF!", "e")

		self.assertEqual(caught.exception.code, CODE_CELL_ERROR_VALUE)

	def test_a_boolean_is_refused(self):
		with self.assertRaises(SheetError) as caught:
			cell_text(True, "b")

		self.assertEqual(caught.exception.code, CODE_CELL_UNSUPPORTED_TYPE)

	def test_a_date_is_refused(self):
		with self.assertRaises(SheetError) as caught:
			cell_text("2026-10-12", "d")

		self.assertEqual(caught.exception.code, CODE_CELL_UNSUPPORTED_TYPE)

	def test_an_unknown_type_is_refused_by_name(self):
		with self.assertRaises(SheetError) as caught:
			cell_text("x", "z")

		self.assertEqual(caught.exception.code, CODE_CELL_UNSUPPORTED_TYPE)
		self.assertIn("z", caught.exception.message)

	def test_a_long_cell_is_refused(self):
		with self.assertRaises(SheetError) as caught:
			cell_text("A" * 300, "s", limits=SheetLimits(max_text_length=10))

		self.assertEqual(caught.exception.code, CODE_CELL_TOO_LONG)

	def test_the_error_carries_its_location(self):
		with self.assertRaises(SheetError) as caught:
			cell_text("=1", "f", sheet="Sheet1", row=4, column=2)

		self.assertIn("Sheet1", caught.exception.where())
		self.assertIn("4", caught.exception.where())
		self.assertIn("2", caught.exception.where())


class TestNumberText(UnitTestCase):
	def test_integers(self):
		self.assertEqual(number_text(7), "7")
		self.assertEqual(number_text(-7), "-7")

	def test_whole_floats_lose_the_decimal_point(self):
		self.assertEqual(number_text(7.0), "7")

	def test_fractions_are_kept(self):
		self.assertEqual(number_text(0.5), "0.5")


class TestBuildSheetRows(UnitTestCase):
	def rows(self):
		return [
			(
				"Sheet1",
				[
					[("A1", "s"), ("A2", "s")],
					[("B1", "s"), (None, "n")],
				],
			)
		]

	def test_a_plain_sheet(self):
		rows, report = build_sheet_rows(self.rows())

		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0].texts(), ("A1", "A2"))
		self.assertEqual(rows[1].texts(), ("B1", ""))
		self.assertEqual(report.sheets, ("Sheet1",))
		self.assertEqual(report.rows, 2)
		# Three nonempty cells: the blank one beside `B1` is content that is not there.
		self.assertEqual(report.cells, 3)

	def test_row_numbers_start_at_one(self):
		rows, _ = build_sheet_rows(self.rows())

		self.assertEqual([row.number for row in rows], [1, 2])

	def test_numeric_cells_are_counted(self):
		_, report = build_sheet_rows([("Sheet1", [[(1, "n"), ("A", "s"), (2, "n")]])])

		self.assertEqual(report.numeric_cells, 2)

	def test_columns_are_numbered_from_one(self):
		rows, _ = build_sheet_rows(self.rows())

		self.assertEqual([cell.column for cell in rows[0].cells], [1, 2])

	def test_the_first_filled_column_is_found(self):
		rows, _ = build_sheet_rows([("Sheet1", [[("", "s"), ("A1", "s")]])])

		self.assertEqual(rows[0].first_filled_column(), 2)

	def test_a_blank_row_is_not_a_row_at_all(self):
		"""Not "it has no filled column": it is not kept, counted or refused for."""
		rows, report = build_sheet_rows([("Sheet1", [[("", "s")], [("", "s")]])])

		self.assertEqual(rows, ())
		self.assertEqual(report.rows, 0)
		self.assertEqual(report.cells, 0)

	def test_no_sheets_is_refused(self):
		with self.assertRaises(SheetError) as caught:
			build_sheet_rows([])

		self.assertEqual(caught.exception.code, CODE_NO_SHEETS)

	def test_too_many_sheets(self):
		sheets = [(f"S{index}", [[("A", "s")]]) for index in range(5)]

		with self.assertRaises(SheetError) as caught:
			build_sheet_rows(sheets, limits=SheetLimits(max_sheets=2))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_SHEETS)

	def test_too_many_rows(self):
		rows = [[("A", "s")] for _ in range(5)]

		with self.assertRaises(SheetError) as caught:
			build_sheet_rows([("S", rows)], limits=SheetLimits(max_rows=2))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_ROWS)

	def test_too_many_columns(self):
		with self.assertRaises(SheetError) as caught:
			build_sheet_rows([("S", [[("A", "s")] * 5])], limits=SheetLimits(max_columns=2))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_COLUMNS)

	def test_too_many_cells_across_sheets(self):
		sheets = [("S1", [[("A", "s")] * 3]), ("S2", [[("B", "s")] * 3])]

		with self.assertRaises(SheetError) as caught:
			build_sheet_rows(sheets, limits=SheetLimits(max_cells=4))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_CELLS)

	def test_a_formula_anywhere_stops_the_read(self):
		with self.assertRaises(SheetError) as caught:
			build_sheet_rows([("S", [[("A", "s")], [("=SUM(A1)", "f")]])])

		self.assertEqual(caught.exception.code, CODE_CELL_FORMULA)

	def test_a_formula_is_caught_even_though_its_cached_value_is_hidden(self):
		# With `data_only=True` the reader would have handed back the value instead of the
		# formula, and a workbook written by a tool that never calculated would hand back
		# nothing at all. Reading with `data_only=False` is what makes this refusal possible.
		with self.assertRaises(SheetError) as caught:
			build_sheet_rows([("S", [[("=VLOOKUP(...)", "f")]])])

		self.assertEqual(caught.exception.code, CODE_CELL_FORMULA)

	def test_the_limits_are_applied_from_the_arguments(self):
		rows, _ = build_sheet_rows([("S", [[("A", "s")]])], limits=SheetLimits(max_text_length=1))

		self.assertEqual(rows[0].texts(), ("A",))


class TestReader(UnitTestCase):
	def test_a_missing_library_is_reported_as_a_code(self):
		# On a machine without openpyxl the reader must say so rather than raise ImportError.
		# The payload is a real (if empty) archive so that the preflight passes and the import
		# itself is what fails.
		import io
		import zipfile

		if HAS_OPENPYXL:
			self.skipTest("openpyxl is installed here, so the missing-library path is not reachable")

		buffer = io.BytesIO()

		with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
			archive.writestr("xl/worksheets/sheet1.xml", b"<worksheet/>")

		with self.assertRaises(SheetError) as caught:
			read_workbook(buffer.getvalue())

		self.assertEqual(caught.exception.code, "WORKBOOK_READER_MISSING")

	def test_the_preflight_runs_inside_the_reader(self):
		# The limits must apply to the real read, not only to a check a caller might forget.
		with self.assertRaises(Exception) as caught:
			read_workbook(b"this is not a zip at all")

		self.assertEqual(getattr(caught.exception, "code", None), "ARCHIVE_NOT_A_ZIP")

	def test_an_oversized_declared_size_is_refused_before_reading(self):
		import io
		import zipfile

		buffer = io.BytesIO()

		with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
			archive.writestr("xl/worksheets/sheet1.xml", b"<worksheet/>")

		with self.assertRaises(SheetError) as caught:
			read_workbook(
				buffer.getvalue(),
				limits=SheetLimits(max_declared_uncompressed_bytes=10),
			)

		self.assertEqual(caught.exception.code, "WORKBOOK_TOO_LARGE_DECLARED")

	def test_a_passed_in_report_is_used_instead_of_measuring_again(self):
		# The measurement is supplied, so the size guard has to be the thing that fires. The
		# bytes never reach the reader, which is why they do not have to be a real workbook.
		from smartprocess_logistics.core.archives import ArchiveReport

		report = ArchiveReport(
			entries=1,
			uncompressed_bytes=10_000,
			worksheets=("xl/worksheets/sheet1.xml",),
			compressed_bytes=10,
		)

		with self.assertRaises(SheetError) as caught:
			read_workbook(b"unused", limits=SheetLimits(max_declared_uncompressed_bytes=100), archive=report)

		self.assertEqual(caught.exception.code, "WORKBOOK_TOO_LARGE_DECLARED")


class TestColumnMajorTexts(UnitTestCase):
	"""The order a workbook is read in, and what is not read at all.

	Column by column, top to bottom, and only the cells that hold something. This is the order
	the intake sees a file's values in, which is what makes a command in the first cell of a
	column reach the serials beneath it and nothing else.
	"""

	def sheet(self, rows, name="S"):
		built, _report = build_sheet_rows([(name, rows)])

		return built

	def test_columns_are_read_top_to_bottom_before_the_next_column(self):
		rows = self.sheet(
			[
				[("A1", "s"), ("B1", "s")],
				[("A2", "s"), ("B2", "s")],
				[("A3", "s"), (None, "n")],
			]
		)

		self.assertEqual(column_major_texts(rows), ("A1", "A2", "A3", "B1", "B2"))

	def test_adjacent_cells_are_never_joined(self):
		"""Two cells side by side are two values, whatever is in them."""
		rows = self.sheet([[("AB", "s"), ("CD", "s")]])

		self.assertEqual(column_major_texts(rows), ("AB", "CD"))

	def test_a_cell_that_holds_several_tokens_is_still_one_value(self):
		"""Splitting a cell is the intake's business, in the window, with the site's separators."""
		rows = self.sheet([[("AB,CD", "s")]])

		self.assertEqual(column_major_texts(rows), ("AB,CD",))

	def test_blank_cells_and_columns_are_skipped(self):
		rows = self.sheet(
			[
				[(None, "n"), ("#2", "s"), ("", "s")],
				[("", "s"), ("B1", "s"), (None, "n")],
				[("   ", "s"), (None, "n"), (None, "n")],
			]
		)

		self.assertEqual(column_major_texts(rows), ("#2", "B1"))

	def test_unequal_columns_read_their_own_length(self):
		rows = self.sheet(
			[
				[("B1", "s"), ("C1", "s")],
				[(None, "n"), ("C2", "s")],
				[(None, "n"), ("C3", "s")],
			]
		)

		self.assertEqual(column_major_texts(rows), ("B1", "C1", "C2", "C3"))

	def test_two_sheets_are_read_in_order(self):
		built, _report = build_sheet_rows([("S1", [[("A", "s")]]), ("S2", [[("B", "s")]])])

		self.assertEqual(column_major_texts(built), ("A", "B"))


class TestBlankCellsAreNotContent(UnitTestCase):
	"""A worksheet with nothing in it is read as nothing, however much of it is formatted.

	The reader is bounded by the *work* of looking, not by the size of the empty area: the
	content limits count nonempty cells, and `max_scanned_cells` bounds the look itself.
	"""

	def test_thousands_of_blank_cells_produce_no_values(self):
		rows = [[(None, "n")] * 20 for _ in range(300)]
		built, report = build_sheet_rows([("S", rows)])

		self.assertEqual(built, ())
		self.assertEqual(column_major_texts(built), ())
		self.assertEqual((report.rows, report.cells), (0, 0))

	def test_whitespace_only_cells_are_empty(self):
		"""A space, a tab and a newline are text that says nothing."""
		rows = [[(" ", "s"), ("\t", "s"), ("\n", "s"), ("A", "s")]]
		built, _report = build_sheet_rows([("S", rows)])

		self.assertEqual(column_major_texts(built), ("A",))

	def test_a_blank_area_beside_data_does_not_count_toward_the_limits(self):
		rows = [[(None, "n"), ("A1", "s")]] + [[(None, "n"), (None, "n")] for _ in range(50)]
		built, _report = build_sheet_rows([("S", rows)], limits=SheetLimits(max_rows=2))

		self.assertEqual(column_major_texts(built), ("A1",))

	def test_looking_at_too_many_cells_is_refused(self):
		rows = [[("A", "s")] + [(None, "n")] * 40]

		with self.assertRaises(SheetError) as caught:
			build_sheet_rows([("S", rows)], limits=SheetLimits(max_scanned_cells=20))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_CELLS)


class TestReaderWithLibrary(UnitTestCase):
	def build(self, sheets):
		import io

		import openpyxl

		workbook = openpyxl.Workbook()
		workbook.remove(workbook.active)

		for name, rows in sheets:
			worksheet = workbook.create_sheet(name)
			for row in rows:
				worksheet.append(row)

		buffer = io.BytesIO()
		workbook.save(buffer)
		workbook.close()

		return buffer.getvalue()

	def test_a_real_workbook_is_read(self):
		if not HAS_OPENPYXL:
			self.skipTest("openpyxl is not installed on this machine")

		rows, report = read_workbook(self.build([("Sheet1", [["A1", "A2"], ["B1", None]])]))

		self.assertEqual(rows[0].texts(), ("A1", "A2"))
		self.assertEqual(rows[1].texts(), ("B1", ""))
		self.assertEqual(report.sheets, ("Sheet1",))

	def test_a_real_formula_is_refused(self):
		if not HAS_OPENPYXL:
			self.skipTest("openpyxl is not installed on this machine")

		with self.assertRaises(SheetError) as caught:
			read_workbook(self.build([("Sheet1", [["A1", "=A1"]])]))

		self.assertEqual(caught.exception.code, CODE_CELL_FORMULA)

	def test_a_real_numeric_cell_is_read_as_text(self):
		if not HAS_OPENPYXL:
			self.skipTest("openpyxl is not installed on this machine")

		rows, report = read_workbook(self.build([("Sheet1", [[12345]])]))

		self.assertEqual(rows[0].texts(), ("12345",))
		self.assertEqual(report.numeric_cells, 1)


def build_synthetic_workbook(path):
	"""Write the one workbook these tests read, from the recipe this module documents.

	Three columns, each beginning with the command for the invoice row its values belong to, plus
	one cell in column A that no command covers — the shape the intake is built for, with values
	that belong to nobody. `openpyxl` is imported here rather than at the top of the module: the
	policy tests above are deliberately runnable without it.
	"""
	from openpyxl import Workbook

	workbook = Workbook()
	sheet = workbook.active
	sheet.title = "Sheet1"
	sheet["A16"] = 99999999
	sheet["B1"] = "*1"

	for index in range(1, 6):
		sheet.cell(row=1 + index, column=2, value=f"SYN-B-{index:02d}")

	sheet["C1"] = "*2"

	for index in range(1, 11):
		sheet.cell(row=1 + index, column=3, value=f"SYN-C-{index:02d}")

	sheet["D1"] = "*3"

	for index in range(1, 21):
		value = (
			10_000_000_000 + index
			if index in (5, 12, 18)
			else ("1.23E+11" if index == 8 else f"SYN-D-{index:02d}")
		)
		sheet.cell(row=1 + index, column=4, value=value)

	path.parent.mkdir(parents=True, exist_ok=True)
	workbook.save(path)


class TestTheSyntheticWorkbook(UnitTestCase):
	"""A workbook that belongs to nobody, and the exact order it is read in.

	`tests/fixtures/synthetic-workbook.xlsx` is the operator's own file's **shape** with values
	that are not anybody's data: column A holds one cell no command is over, and columns B, C and
	D begin with `*1`, `*2` and `*3` over five, ten and twenty values. Read column by column, top
	to bottom, that is a command followed by its own serials three times over, with the stray cell
	first because its column comes first — the shape the intake is built for.

	This is the repository's own evidence for that order, so it runs on any checkout with the
	reader installed rather than skipping. The same values are what the window's JavaScript checks
	are built from (`deployment/lab/verify_scan_logic.js`, `preview_scan_header.html`), which is
	what keeps the two languages reading one file the same way.

	The file is built by `build_synthetic_workbook` below, which is the recipe and the only copy of
	it: this class calls it when the file is not on disk, so a checkout that never received the
	binary runs these tests on the same values rather than failing on a missing file.

	`test_the_recipe_builds_the_rows_the_checked_out_file_holds` is what keeps the two in step.
	"""

	WORKBOOK = pathlib.Path(__file__).resolve().parent / "fixtures" / "synthetic-workbook.xlsx"

	B_COLUMN = ("*1", "SYN-B-01", "SYN-B-02", "SYN-B-03", "SYN-B-04", "SYN-B-05")
	C_COLUMN = ("*2",) + tuple(f"SYN-C-{index:02d}" for index in range(1, 11))
	D_COLUMN = ("*3",) + tuple(
		str(10_000_000_000 + index)
		if index in (5, 12, 18)
		else ("1.23E+11" if index == 8 else f"SYN-D-{index:02d}")
		for index in range(1, 21)
	)

	@classmethod
	def setUpClass(cls):
		"""Build the workbook when the checkout does not carry it.

		It belongs in the repository, and it is there — but it is a *binary* there, and the one
		thing a clone can lose without anybody noticing is a binary. Measured on this repository:
		the fixture was the single file the test suite needed that was not tracked, so a clone ran
		1,100 tests and failed on this class with a missing-file error that says nothing about the
		reading order the class exists to check. The recipe is the same one either way, so a built
		file and a checked-out file hold the same values, and the test below keeps them in step.
		"""
		if cls.WORKBOOK.exists():
			return

		if not HAS_OPENPYXL:
			raise unittest.SkipTest("the workbook is missing and openpyxl cannot build it")

		try:
			build_synthetic_workbook(cls.WORKBOOK)
		except OSError as error:
			# A read-only checkout can do neither, and saying so is better than an error that
			# reads like a broken test.
			raise unittest.SkipTest(f"the workbook is missing and cannot be built here: {error}") from error

	def read(self):
		if not HAS_OPENPYXL:
			self.skipTest("openpyxl is not installed on this machine")

		rows, report = read_workbook(self.WORKBOOK.read_bytes())

		return rows, report, column_major_texts(rows)

	def test_it_reads_column_by_column_with_each_command_at_its_own_column(self):
		_rows, report, values = self.read()

		self.assertEqual(values, ("99999999",) + self.B_COLUMN + self.C_COLUMN + self.D_COLUMN)
		self.assertEqual(report.sheets, ("Sheet1",))
		self.assertEqual(len(values), 39)

	def test_the_columns_hold_five_ten_and_twenty_values_under_their_commands(self):
		_rows, _report, values = self.read()

		self.assertEqual([value for value in values if value.startswith("*")], ["*1", "*2", "*3"])
		self.assertEqual(len(self.B_COLUMN) - 1, 5)
		self.assertEqual(len(self.C_COLUMN) - 1, 10)
		self.assertEqual(len(self.D_COLUMN) - 1, 20)

	def test_a_numeric_cell_is_reported_and_kept_whole(self):
		"""Three of column D's values are stored as numbers; their text is the number, not a float's."""
		_rows, report, values = self.read()

		self.assertEqual(report.numeric_cells, 4)
		self.assertIn("10000000005", values)

	def test_a_value_that_looks_like_a_number_is_kept_as_the_text_it_is(self):
		_rows, _report, values = self.read()

		self.assertIn("1.23E+11", values)

	def test_the_recipe_builds_the_rows_the_checked_out_file_holds(self):
		"""A checkout with no binary is not a checkout with no test.

		The two files are compared as **read**, not as bytes: an XLSX is a ZIP whose entries carry
		timestamps, so two builds of one recipe are never byte-identical and a byte comparison
		would fail for a reason that has nothing to do with the reading order. What has to agree is
		what the reader sees, which is what every other test in this class asserts on.
		"""
		_committed_rows, _committed_report, committed = self.read()

		with tempfile.TemporaryDirectory() as folder:
			built = pathlib.Path(folder) / "synthetic-workbook.xlsx"
			build_synthetic_workbook(built)

			self.assertTrue(built.exists(), "the recipe wrote nothing")
			rows, _report = read_workbook(built.read_bytes())
			values = column_major_texts(rows)

		self.assertEqual(values, committed)
		self.assertEqual(values, ("99999999",) + self.B_COLUMN + self.C_COLUMN + self.D_COLUMN)


class TestTheOperatorsWorkbook(UnitTestCase):
	"""The operator's own file, when it happens to be on this machine: a shape, not a dataset.

	The file lives outside the repository, so this is an **optional** check and skips when it is
	not there. It asserts the shape and the reading order rather than the serials themselves: the
	serials belong to the operator, and a published test is not the place for them. The exact
	values are pinned by the synthetic fixture above, which every checkout has.
	"""

	WORKBOOK = pathlib.Path.home() / "Desktop" / "01.xlsx"

	def read(self):
		if not HAS_OPENPYXL:
			self.skipTest("openpyxl is not installed on this machine")

		if not self.WORKBOOK.exists():
			self.skipTest("the operator's workbook is not on this machine")

		rows, report = read_workbook(self.WORKBOOK.read_bytes())

		return rows, report, column_major_texts(rows)

	def test_it_is_read_column_by_column_with_each_command_over_its_own_column(self):
		_rows, report, values = self.read()

		commands = [value for value in values if value.startswith("*")]
		self.assertEqual(commands, ["*1", "*2", "*3"])
		self.assertEqual(report.sheets, ("Sheet1",))

		# The three commanded columns are of different lengths and the first column carries a cell
		# no command is over, so the file is read as: that cell, then a command and its values,
		# three times. Anything else is a reader that reordered the workbook.
		self.assertGreater(len(values), len(commands))
		for index, command in enumerate(commands):
			self.assertEqual(
				values.index(command) < values.index(commands[index + 1])
				if index + 1 < len(commands)
				else True,
				True,
			)

	def test_the_numbers_in_it_are_reported_rather_than_invented(self):
		"""Excel already lost any leading zero in those cells; the count says so rather than guessing."""
		_rows, report, _values = self.read()

		self.assertGreater(report.numeric_cells, 0)


class TestTheReaderSkipsEmptyRows(UnitTestCase):
	"""A sheet with nothing in it, read through the library, is a sheet with nothing in it."""

	def build(self, sheets):
		import io

		import openpyxl

		workbook = openpyxl.Workbook()
		workbook.remove(workbook.active)

		for name, rows in sheets:
			worksheet = workbook.create_sheet(name)

			for row in rows:
				worksheet.append(row)

		buffer = io.BytesIO()
		workbook.save(buffer)
		workbook.close()

		return buffer.getvalue()

	def test_a_formatted_but_empty_sheet_is_not_content(self):
		if not HAS_OPENPYXL:
			self.skipTest("openpyxl is not installed on this machine")

		rows, report = read_workbook(self.build([("S", [[None] * 10 for _ in range(400)])]))

		self.assertEqual(rows, ())
		self.assertEqual(column_major_texts(rows), ())
		self.assertEqual((report.rows, report.cells), (0, 0))
