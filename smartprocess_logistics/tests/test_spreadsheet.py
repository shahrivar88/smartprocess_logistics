"""Worksheet reading policy, exercised without the XLSX library.

`build_sheet_rows` takes `(value, data_type)` pairs and applies every limit and every refusal,
so the whole policy is testable on a machine with no `openpyxl`. The reader itself is one thin
function and is covered by its own test at the end, which skips when the library is absent.
"""

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
		self.assertEqual(report.cells, 4)

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

	def test_a_blank_row_has_no_filled_column(self):
		rows, _ = build_sheet_rows([("Sheet1", [[("", "s")]])])

		self.assertIsNone(rows[0].first_filled_column())

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
