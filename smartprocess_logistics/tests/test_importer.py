"""The import engine: shapes, layouts, marker grouping and the preview."""

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS
from smartprocess_logistics.core.importer import (
	CODE_BLANK_TOKENS,
	CODE_MARKER_ROW_MISSING,
	CODE_MARKERS_IGNORED,
	CODE_NO_CANDIDATES,
	CODE_NUMERIC_CELLS,
	CODE_ROW_INDEX_UNKNOWN,
	CODE_SEQUENTIAL_UNCONFIRMED,
	CODE_SERIAL_BEFORE_MARKER,
	CODE_TARGET_ROW_REQUIRED,
	CODE_TARGET_ROW_UNKNOWN,
	LAYOUT_GROUPED,
	LAYOUT_SEQUENTIAL,
	LAYOUT_SINGLE_ROW,
	SHAPE_FLAT_COLUMN,
	SHAPE_GROUPED_COLUMN,
	SHAPE_MARKER_HEADERS,
	ImportRequest,
	build_preview,
	candidates_from_sheet_rows,
	candidates_from_tokens,
	preview_from_sheet_rows,
	preview_from_text,
)
from smartprocess_logistics.core.spreadsheet import build_sheet_rows
from smartprocess_logistics.core.validation import (
	CODE_DUPLICATE_IN_INVOICE,
	CODE_EXCESS_QUANTITY,
	ExistingAssignment,
	InvoiceRow,
)
from smartprocess_logistics.tests.base import UnitTestCase

PATTERNS = ("{n}*", "*{n}", "{n}#", "#{n}")

ROW_1 = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=3, serial_required=True)
ROW_2 = InvoiceRow(row_key="row-2", item_code="ITEM-2", invoiced_qty=2, serial_required=True)
ROWS = (ROW_1, ROW_2)


def request(**overrides):
	arguments = {
		"rows": ROWS,
		"layout": LAYOUT_GROUPED,
		"delimiters": (",", "\\r\\n", "\\n"),
		"patterns": PATTERNS,
	}
	arguments.update(overrides)

	return ImportRequest(**arguments)


def candidates(tokens, patterns=PATTERNS):
	found, issues, _blank = candidates_from_tokens(tokens, patterns)

	return found, issues


def sheet(rows):
	built, _report = build_sheet_rows([("Sheet1", [[(value, "s") for value in row] for row in rows])])

	return built


class TestTextCandidates(UnitTestCase):
	def test_a_flat_list_has_no_row_until_a_marker_appears(self):
		found, _ = candidates(["A1", "A2"])

		self.assertEqual([candidate.row_index for candidate in found], [None, None])

	def test_a_marker_switches_the_current_row(self):
		found, _ = candidates(["1*", "A1", "A2", "2*", "B1"])

		self.assertEqual([candidate.row_index for candidate in found], [1, 1, 2])
		self.assertEqual([candidate.serial for candidate in found], ["A1", "A2", "B1"])

	def test_the_mirror_marker_shape_works_too(self):
		found, _ = candidates(["*2", "B1"])

		self.assertEqual(found[0].row_index, 2)

	def test_a_marker_is_never_a_serial(self):
		found, _ = candidates(["1*", "A1"])

		self.assertNotIn("1*", [candidate.serial for candidate in found])

	def test_a_marker_with_no_usable_row_number_is_reported(self):
		found, issues = candidates(["0*", "A1"])

		self.assertEqual([issue.code for issue in issues], [CODE_MARKER_ROW_MISSING])
		self.assertEqual([candidate.serial for candidate in found], ["A1"])

	def test_tokens_are_trimmed(self):
		found, _ = candidates(["  A1  ", "1*", "  A2  "])

		self.assertEqual([candidate.serial for candidate in found], ["A1", "A2"])

	def test_blank_tokens_are_counted_not_reported_one_by_one(self):
		found, issues, blank = candidates_from_tokens(["A1", "", "  ", "A2"], PATTERNS)

		self.assertEqual(len(found), 2)
		self.assertEqual(issues, ())
		self.assertEqual(blank, 2)

	def test_a_source_name_is_attached(self):
		found, _, _ = candidates_from_tokens(["A1"], PATTERNS, source="data.txt")

		self.assertEqual(found[0].sheet, "data.txt")

	def test_without_patterns_every_token_is_a_serial(self):
		found, _ = candidates(["1*", "A1"], patterns=())

		self.assertEqual([candidate.serial for candidate in found], ["1*", "A1"])
		self.assertEqual([candidate.row_index for candidate in found], [None, None])


class TestSheetShapes(UnitTestCase):
	def test_markers_in_the_header_give_one_column_per_row(self):
		rows = sheet([["1*", "2*"], ["A1", "B1"], ["A2", "B2"]])
		found, issues, shape = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual(shape, SHAPE_MARKER_HEADERS)
		self.assertEqual(issues, ())
		self.assertEqual(
			[(candidate.row_index, candidate.serial) for candidate in found],
			[(1, "A1"), (2, "B1"), (1, "A2"), (2, "B2")],
		)

	def test_an_unmarked_column_is_ignored(self):
		rows = sheet([["1*", "comment"], ["A1", "ignored"]])
		found, _, _ = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual([candidate.serial for candidate in found], ["A1"])

	def test_a_header_marker_with_no_row_number_is_reported(self):
		rows = sheet([["0*", "2*"], ["A1", "B1"]])
		found, issues, _ = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual([issue.code for issue in issues], [CODE_MARKER_ROW_MISSING])
		self.assertEqual([candidate.serial for candidate in found], ["B1"])

	def test_markers_in_a_column_group_the_blocks(self):
		rows = sheet([["1*"], ["A1"], ["A2"], ["2*"], ["B1"]])
		found, _, shape = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual(shape, SHAPE_GROUPED_COLUMN)
		self.assertEqual(
			[(candidate.row_index, candidate.serial) for candidate in found],
			[(1, "A1"), (1, "A2"), (2, "B1")],
		)

	def test_no_markers_means_a_flat_column(self):
		rows = sheet([["A1"], ["A2"]])
		found, _, shape = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual(shape, SHAPE_FLAT_COLUMN)
		self.assertEqual([candidate.row_index for candidate in found], [None, None])

	def test_an_empty_sheet(self):
		rows = sheet([[""]])
		found, issues, shape = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual((found, issues), ((), ()))
		self.assertEqual(shape, SHAPE_FLAT_COLUMN)

	def test_a_blank_row_contributes_nothing(self):
		rows = sheet([["1*"], ["A1"], [""], ["A2"]])
		found, _, _ = candidates_from_sheet_rows(rows, PATTERNS)

		self.assertEqual([candidate.serial for candidate in found], ["A1", "A2"])


class TestGroupedLayout(UnitTestCase):
	def test_a_grouped_text_file(self):
		preview = preview_from_text("1*\r\nA1\r\nA2\r\n2*\r\nB1\r\nB2", request())

		self.assertTrue(preview.is_valid)
		self.assertEqual(
			preview.submissions(),
			(("row-1", "A1"), ("row-1", "A2"), ("row-2", "B1"), ("row-2", "B2")),
		)

	def test_a_serial_before_the_first_marker_is_refused(self):
		preview = preview_from_text("A1\r\n1*\r\nA2", request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_SERIAL_BEFORE_MARKER])

	def test_a_marker_naming_a_row_that_does_not_exist(self):
		preview = preview_from_text("9*\r\nZ1", request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_ROW_INDEX_UNKNOWN])

	def test_a_file_with_no_serials(self):
		preview = preview_from_text("1*\r\n\r\n", request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_NO_CANDIDATES])

	def test_an_empty_file(self):
		preview = preview_from_text("", request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_NO_CANDIDATES])

	def test_blank_tokens_produce_one_warning(self):
		preview = preview_from_text("1*\r\nA1\r\n\r\nA2", request())

		self.assertIn(CODE_BLANK_TOKENS, [issue.code for issue in preview.warnings])
		self.assertEqual(len([issue for issue in preview.warnings if issue.code == CODE_BLANK_TOKENS]), 1)


class TestSingleRowLayout(UnitTestCase):
	def test_a_single_row_invoice_needs_no_target(self):
		preview = preview_from_text("A1\r\nA2", request(rows=(ROW_2,), layout=LAYOUT_SINGLE_ROW))

		self.assertTrue(preview.is_valid)
		self.assertEqual(preview.submissions(), (("row-2", "A1"), ("row-2", "A2")))

	def test_a_multi_row_invoice_needs_a_target(self):
		preview = preview_from_text("A1", request(layout=LAYOUT_SINGLE_ROW))

		self.assertEqual([issue.code for issue in preview.errors], [CODE_TARGET_ROW_REQUIRED])

	def test_a_named_target_is_used(self):
		preview = preview_from_text("B1", request(layout=LAYOUT_SINGLE_ROW, target_row_key="row-2"))

		self.assertEqual(preview.submissions(), (("row-2", "B1"),))

	def test_an_unknown_target_is_refused(self):
		preview = preview_from_text("A1", request(layout=LAYOUT_SINGLE_ROW, target_row_key="row-9"))

		self.assertEqual([issue.code for issue in preview.errors], [CODE_TARGET_ROW_UNKNOWN])

	def test_markers_are_reported_as_ignored_not_silently_dropped(self):
		preview = preview_from_text("1*\r\nA1", request(rows=(ROW_2,), layout=LAYOUT_SINGLE_ROW))

		self.assertTrue(preview.is_valid)
		self.assertIn(CODE_MARKERS_IGNORED, [issue.code for issue in preview.warnings])


class TestSequentialLayout(UnitTestCase):
	def test_serials_fill_rows_in_invoice_order(self):
		preview = preview_from_text(
			"A1\r\nA2\r\nA3\r\nB1\r\nB2",
			request(layout=LAYOUT_SEQUENTIAL, row_mapping_confirmed=True),
		)

		self.assertTrue(preview.is_valid)
		self.assertEqual(
			preview.submissions(),
			(("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3"), ("row-2", "B1"), ("row-2", "B2")),
		)

	def test_a_partial_file_fills_what_it_covers(self):
		preview = preview_from_text("A1\r\nA2", request(layout=LAYOUT_SEQUENTIAL, row_mapping_confirmed=True))

		self.assertTrue(preview.is_valid)
		self.assertEqual(preview.submissions(), (("row-1", "A1"), ("row-1", "A2")))

	def test_a_row_that_already_has_what_it_needs_is_skipped(self):
		existing = (
			ExistingAssignment(row_key="row-1", normalized_serial="z1"),
			ExistingAssignment(row_key="row-1", normalized_serial="z2"),
			ExistingAssignment(row_key="row-1", normalized_serial="z3"),
		)
		preview = preview_from_text(
			"A1",
			request(layout=LAYOUT_SEQUENTIAL, existing=existing, row_mapping_confirmed=True),
		)

		self.assertTrue(preview.is_valid)
		self.assertEqual(preview.submissions(), (("row-2", "A1"),))

	def test_more_serials_than_the_invoice_needs_is_reported_as_excess(self):
		preview = preview_from_text(
			"A1\r\nA2\r\nA3\r\nB1\r\nB2\r\nB3",
			request(layout=LAYOUT_SEQUENTIAL, row_mapping_confirmed=True),
		)

		self.assertEqual([issue.code for issue in preview.errors], [CODE_EXCESS_QUANTITY])

	def test_an_unconfirmed_row_mapping_is_refused(self):
		# Sequential decides the row for every serial by counting. A file whose order does not
		# match the invoice would assign every serial to the wrong row with nothing in the file
		# to say so, so the engine refuses until the operator confirms the mapping it shows.
		preview = preview_from_text("A1\r\nA2", request(layout=LAYOUT_SEQUENTIAL))

		self.assertFalse(preview.is_valid)
		self.assertIn(CODE_SEQUENTIAL_UNCONFIRMED, [issue.code for issue in preview.errors])
		self.assertTrue(preview.requires_row_mapping_confirmation)

	def test_the_preview_shows_the_mapping_that_has_to_be_confirmed(self):
		preview = preview_from_text(
			"A1\r\nA2\r\nB1",
			request(layout=LAYOUT_SEQUENTIAL, row_mapping_confirmed=True),
		)

		# row-1 invoices three units, so the first three serials are its own and row-2 gets none.
		self.assertEqual(
			preview.row_mapping,
			(("row-1", "ITEM-1", 1, 3), ("row-2", "ITEM-2", 2, 0)),
		)

	def test_a_grouped_mapping_needs_no_confirmation(self):
		preview = preview_from_text("1*\r\nA1", request())

		self.assertFalse(preview.requires_row_mapping_confirmation)
		self.assertTrue(preview.is_valid)


class TestDuplicatesAndExisting(UnitTestCase):
	def test_the_same_serial_twice_in_the_file_is_refused(self):
		preview = preview_from_text("1*\r\nA1\r\nA1", request())

		self.assertEqual([issue.code for issue in preview.errors], ["DUPLICATE_IN_ROW"])

	def test_the_same_serial_on_two_rows_of_the_file_is_refused(self):
		preview = preview_from_text("1*\r\nA1\r\n2*\r\nA1", request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_DUPLICATE_IN_INVOICE])

	def test_a_serial_already_on_the_invoice_is_refused(self):
		existing = (ExistingAssignment(row_key="row-1", normalized_serial="a1"),)
		preview = preview_from_text("1*\r\nA1", request(existing=existing))

		self.assertEqual([issue.code for issue in preview.errors], ["ALREADY_REGISTERED"])

	def test_incremental_import_adds_the_rest(self):
		existing = (
			ExistingAssignment(row_key="row-1", normalized_serial="a1"),
			ExistingAssignment(row_key="row-1", normalized_serial="a2"),
		)
		preview = preview_from_text("1*\r\nA3", request(existing=existing))

		self.assertTrue(preview.is_valid)
		group = preview.groups[0]

		self.assertEqual((group.required, group.existing, group.incoming, group.shortage), (3, 2, 1, 0))

	def test_a_serial_used_on_another_invoice_is_not_in_this_invoice_s_existing_set(self):
		preview = preview_from_text("1*\r\nA1", request(existing=()))

		self.assertTrue(preview.is_valid)


class TestPreviewShape(UnitTestCase):
	def test_the_groups_describe_every_row(self):
		preview = preview_from_text("1*\r\nA1", request())

		self.assertEqual(len(preview.groups), 2)
		self.assertEqual([group.row_index for group in preview.groups], [1, 2])
		self.assertEqual([group.row_key for group in preview.groups], ["row-1", "row-2"])

	def test_the_group_totals_are_right(self):
		preview = preview_from_text("1*\r\nA1\r\nA2\r\n2*\r\nB1", request())
		first, second = preview.groups

		self.assertEqual((first.required, first.incoming, first.shortage), (3, 2, 1))
		self.assertEqual((second.required, second.incoming, second.shortage), (2, 1, 1))

	def test_the_candidate_count_is_reported(self):
		preview = preview_from_text("1*\r\nA1\r\nA2", request())

		self.assertEqual(preview.candidates, 2)

	def test_an_unknown_row_from_a_sequential_file(self):
		# A row reference that does not exist can only arrive through a marker, and the
		# grouped layout is the one that reads markers.
		preview = preview_from_text("5*\r\nX", request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_ROW_INDEX_UNKNOWN])

	def test_an_unknown_layout_is_a_programming_error(self):
		with self.assertRaises(ValueError):
			build_preview((), request(layout="nonsense"))

	def test_a_delimiter_configuration_error_still_reaches_the_caller(self):
		from smartprocess_logistics.core.delimiters import DelimiterError

		with self.assertRaises(DelimiterError):
			preview_from_text("A1", request(delimiters=("A",)))


class TestSheetPreview(UnitTestCase):
	def test_marked_headers_end_to_end(self):
		rows = sheet([["1*", "2*"], ["A1", "B1"], ["A2", "B2"]])
		preview = preview_from_sheet_rows(rows, request())

		self.assertTrue(preview.is_valid)
		self.assertEqual(
			preview.submissions(),
			(("row-1", "A1"), ("row-2", "B1"), ("row-1", "A2"), ("row-2", "B2")),
		)

	def test_the_sheet_names_are_reported(self):
		rows = sheet([["1*"], ["A1"]])
		preview = preview_from_sheet_rows(rows, request())

		self.assertEqual(preview.sheets, ("Sheet1",))

	def test_a_numeric_cell_produces_one_warning(self):
		built, report = build_sheet_rows([("Sheet1", [[("1*", "s")], [(12345, "n")]])])
		preview = preview_from_sheet_rows(built, request(), report=report)

		self.assertIn(CODE_NUMERIC_CELLS, [issue.code for issue in preview.warnings])
		self.assertEqual(preview.numeric_cells, 1)

	def test_an_unknown_row_key_cannot_arrive_from_a_sheet_without_markers(self):
		rows = sheet([["A1"]])
		preview = preview_from_sheet_rows(rows, request(layout=LAYOUT_SEQUENTIAL, row_mapping_confirmed=True))

		self.assertTrue(preview.is_valid)
		self.assertEqual(preview.submissions(), (("row-1", "A1"),))

	def test_a_sheet_unknown_row_reference_is_refused(self):
		rows = sheet([["9*"], ["A1"]])
		preview = preview_from_sheet_rows(rows, request())

		self.assertEqual([issue.code for issue in preview.errors], [CODE_ROW_INDEX_UNKNOWN])


class TestNothingIsWritten(UnitTestCase):
	def test_the_engine_only_ever_returns_data(self):
		# Every return value is a frozen dataclass or a tuple, and the module imports no
		# database client. This test records the property rather than proving it.
		preview = preview_from_text("1*\r\nA1", request())

		self.assertTrue(preview.is_valid)
		self.assertNotIn("save", dir(preview))
		self.assertNotIn("insert", dir(preview))
		self.assertNotIn("commit", dir(preview))


class TestTheSpaceDelimiter(UnitTestCase):
	"""A space-separated file, through the entry point the Desk uses for decoded text.

	`preview_from_text` builds its delimiter set from stored text with
	`DelimiterSet.of(request.delimiters)`, so a stored `\\s` has to reach the splitter as one
	space for any of this to work.
	"""

	def test_serials_separated_by_spaces_and_lines(self):
		preview = preview_from_text("1*\r\nA1 A2 A3", request(delimiters=("\\s", "\\r\\n", "\\n")))

		self.assertTrue(preview.is_valid)
		self.assertEqual(
			preview.submissions(),
			(("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3")),
		)

	def test_the_shipped_defaults_parse_the_same_file(self):
		preview = preview_from_text(
			"1*\r\nA1 A2\r\n2*\r\nB1",
			request(delimiters=[stored for _, stored in DEFAULT_DELIMITERS]),
		)

		self.assertTrue(preview.is_valid)
		self.assertEqual(preview.submissions(), (("row-1", "A1"), ("row-1", "A2"), ("row-2", "B1")))

	def test_a_literal_space_delimiter_still_parses(self):
		"""Compatibility with a delimiter configured as a bare space before this change."""
		preview = preview_from_text("1*\r\nA1 A2", request(delimiters=(" ", "\\r\\n", "\\n")))

		self.assertTrue(preview.is_valid)
		self.assertEqual(preview.submissions(), (("row-1", "A1"), ("row-1", "A2")))
