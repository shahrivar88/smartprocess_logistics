"""The registration plan and the configuration checks, at integration level."""

from smartprocess_logistics.tests.base import UnitTestCase

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS, DEFAULT_ITEM_MARKERS
from smartprocess_logistics.core.delimiters import DelimiterError
from smartprocess_logistics.core.normalization import CODE_EMPTY, CODE_HAS_CONTROL, CODE_TOO_LONG
from smartprocess_logistics.core.validation import (
	CODE_ALREADY_REGISTERED,
	CODE_CONFIG_DELIMITER,
	CODE_CONFIG_DELIMITER_DUPLICATE,
	CODE_CONFIG_DELIMITER_MARKER_CONFLICT,
	CODE_CONFIG_DELIMITER_OVERLAP,
	CODE_CONFIG_MARKER,
	CODE_CONFIG_MARKER_AMBIGUOUS,
	CODE_CONTAINS_DELIMITER,
	CODE_DUPLICATE_IN_INVOICE,
	CODE_DUPLICATE_IN_ROW,
	CODE_DUPLICATE_ROW_KEY,
	CODE_EXCESS_QUANTITY,
	CODE_TOO_MANY_ROWS,
	CODE_TOO_MANY_SUBMISSIONS,
	CODE_UNKNOWN_ROW,
	MAX_ROWS,
	MAX_SUBMISSIONS,
	SEVERITY_ERROR,
	SEVERITY_WARNING,
	ExistingAssignment,
	InvoiceRow,
	RegistrationError,
	check_configuration,
	plan_registration,
)

DELIMITERS = (",", "\\r\\n")
ROW_1 = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=3)
ROW_2 = InvoiceRow(row_key="row-2", item_code="ITEM-2", invoiced_qty=1)


def plan(rows, submissions, existing=(), delimiters=DELIMITERS, **kwargs):
	return plan_registration(
		rows=rows,
		submissions=submissions,
		existing=existing,
		delimiters=delimiters,
		**kwargs,
	)


class TestHappyPaths(UnitTestCase):
	def test_a_complete_row(self):
		result = plan([ROW_1], [("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3")])

		self.assertTrue(result.is_valid)
		self.assertEqual(len(result.accepted), 3)
		self.assertEqual(result.coverages[0].shortage, 0)

	def test_a_partial_registration(self):
		result = plan([ROW_1], [("row-1", "A1"), ("row-1", "A2")])

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages[0].registered, 2)
		self.assertEqual(result.coverages[0].shortage, 1)

	def test_the_rest_registered_later_completes_the_row(self):
		existing = (
			ExistingAssignment(row_key="row-1", normalized_serial="a1"),
			ExistingAssignment(row_key="row-1", normalized_serial="a2"),
		)

		result = plan([ROW_1], [("row-1", "A3")], existing=existing)

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages[0].registered, 3)
		self.assertEqual(result.coverages[0].shortage, 0)

	def test_nothing_submitted(self):
		result = plan([ROW_1], [])

		self.assertTrue(result.is_valid)
		self.assertEqual(len(result.accepted), 0)
		self.assertEqual(result.coverages[0].shortage, 3)

	def test_no_rows_at_all(self):
		result = plan([], [])

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages, ())

	def test_accepted_grouped_by_row(self):
		result = plan([ROW_1, ROW_2], [("row-1", "A1"), ("row-2", "B1"), ("row-1", "A2")])

		grouped = result.accepted_by_row()

		self.assertEqual(grouped["row-1"], ["a1", "a2"])
		self.assertEqual(grouped["row-2"], ["b1"])

	def test_submission_order_is_preserved(self):
		result = plan([ROW_1], [("row-1", "C3"), ("row-1", "A1"), ("row-1", "B2")])

		self.assertEqual([item.serial.original for item in result.accepted], ["C3", "A1", "B2"])

	def test_nothing_stored_can_be_used_for_another_invoice(self):
		# The plan only ever sees one invoice, so the same serial is admissible on the next
		# one. Reuse across invoices is exactly what the identity rule permits.
		first = plan([ROW_1], [("row-1", "A1")])
		second = plan([ROW_1], [("row-1", "A1")])

		self.assertTrue(first.is_valid)
		self.assertTrue(second.is_valid)

	def test_a_return_row_is_covered_by_its_absolute_quantity(self):
		return_row = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=-2)

		result = plan([return_row], [("row-1", "A1"), ("row-1", "A2")])

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages[0].required, 2)

	def test_the_original_text_is_kept_on_each_accepted_serial(self):
		result = plan([ROW_1], [("row-1", "  aB-12  ")])

		self.assertEqual(result.accepted[0].serial.original, "  aB-12  ")
		self.assertEqual(result.accepted[0].serial.normalized, "ab-12")
		self.assertEqual(result.accepted[0].item_code, "ITEM-1")


class TestDuplicates(UnitTestCase):
	def test_the_same_serial_twice_in_one_row_is_refused(self):
		result = plan([ROW_1], [("row-1", "A1"), ("row-1", "A1")])

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_DUPLICATE_IN_ROW,))
		self.assertEqual(len(result.accepted), 1)

	def test_the_same_serial_on_two_rows_of_one_invoice_is_refused(self):
		result = plan([ROW_1, ROW_2], [("row-1", "A1"), ("row-2", "A1")])

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_DUPLICATE_IN_INVOICE,))

	def test_duplicate_detection_ignores_case_and_digits(self):
		result = plan([ROW_1], [("row-1", "AB-1"), ("row-1", "ab-۱")])

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_DUPLICATE_IN_ROW,))

	def test_a_serial_already_on_the_invoice_is_refused(self):
		existing = (ExistingAssignment(row_key="row-1", normalized_serial="a1"),)

		result = plan([ROW_1], [("row-1", "A1")], existing=existing)

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_ALREADY_REGISTERED,))

	def test_every_repeat_after_the_first_is_reported(self):
		result = plan([ROW_1], [("row-1", "A1"), ("row-1", "A1"), ("row-1", "A1")])

		self.assertEqual(result.issue_codes(), (CODE_DUPLICATE_IN_ROW, CODE_DUPLICATE_IN_ROW))
		self.assertEqual(len(result.accepted), 1)


class TestReplacedRows(UnitTestCase):
	"""A row whose whole set is written again is judged on the set being written.

	`set_row_serials` names one row and replaces it: the assignments it already holds are
	removed before the new ones are inserted. Counting them as still present made a correction
	impossible — re-sending a serial the row already had was reported as a serial already on the
	invoice, and a shorter list was reported as an excess instead of the shortage it is.
	"""

	EXISTING = (
		ExistingAssignment(row_key="row-1", normalized_serial="a1"),
		ExistingAssignment(row_key="row-1", normalized_serial="a2"),
		ExistingAssignment(row_key="row-1", normalized_serial="a3"),
	)

	def test_a_replaced_row_may_keep_two_serials_and_add_one(self):
		result = plan(
			[ROW_1],
			[("row-1", "A1"), ("row-1", "A2"), ("row-1", "A9")],
			existing=self.EXISTING,
			replaced_rows=("row-1",),
		)

		self.assertTrue(result.is_valid)
		self.assertEqual(result.issue_codes(), ())
		self.assertEqual(result.coverages[0].registered, 3)
		self.assertEqual(result.coverages[0].shortage, 0)

	def test_a_replaced_row_may_be_shortened(self):
		result = plan(
			[ROW_1], [("row-1", "A1"), ("row-1", "A2")], existing=self.EXISTING, replaced_rows=("row-1",)
		)

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages[0].registered, 2)
		self.assertEqual(result.coverages[0].shortage, 1)

	def test_a_replaced_row_may_be_emptied(self):
		result = plan([ROW_1], [], existing=self.EXISTING, replaced_rows=("row-1",))

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages[0].registered, 0)
		self.assertEqual(result.coverages[0].shortage, 3)

	def test_a_replaced_row_may_not_exceed_its_quantity(self):
		result = plan(
			[ROW_1],
			[("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3"), ("row-1", "A4")],
			existing=self.EXISTING,
			replaced_rows=("row-1",),
		)

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_EXCESS_QUANTITY,))

	def test_a_serial_on_a_row_that_is_not_replaced_is_still_refused(self):
		# The other row keeps its assignment, so the serial is on the invoice either way.
		result = plan(
			[ROW_1, ROW_2],
			[("row-1", "B1")],
			existing=(ExistingAssignment(row_key="row-2", normalized_serial="b1"),),
			replaced_rows=("row-1",),
		)

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_ALREADY_REGISTERED,))

	def test_an_import_names_no_replaced_rows_and_still_refuses_a_repeat(self):
		result = plan([ROW_1], [("row-1", "A1")], existing=self.EXISTING)

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_ALREADY_REGISTERED,))

	def test_one_serial_stored_on_two_rows_is_refused_in_either_order(self):
		# The database keeps one serial per invoice, but a direct write can leave it on two
		# rows. Filtering the already-registered set through a serial-to-row map made the answer
		# depend on which row the map recorded last, so this pins both orders.
		for first, second in (("row-1", "row-2"), ("row-2", "row-1")):
			with self.subTest(order=(first, second)):
				existing = (
					ExistingAssignment(row_key=first, normalized_serial="x1"),
					ExistingAssignment(row_key=second, normalized_serial="x1"),
				)

				result = plan(
					[ROW_1, ROW_2], [("row-1", "X1")], existing=existing, replaced_rows=("row-1",)
				)

				self.assertFalse(result.is_valid)
				self.assertEqual(result.issue_codes(), (CODE_ALREADY_REGISTERED,))


class TestPerSerialFailures(UnitTestCase):
	def test_an_unknown_row_is_refused(self):
		result = plan([ROW_1], [("row-9", "A1")])

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_UNKNOWN_ROW,))
		self.assertEqual(result.issues[0].row_key, "row-9")

	def test_an_empty_serial_is_refused(self):
		result = plan([ROW_1], [("row-1", "   ")])

		self.assertEqual(result.issue_codes(), (CODE_EMPTY,))

	def test_an_over_long_serial_is_refused(self):
		result = plan([ROW_1], [("row-1", "A" * 61)])

		self.assertEqual(result.issue_codes(), (CODE_TOO_LONG,))

	def test_a_serial_containing_an_active_delimiter_is_refused(self):
		result = plan([ROW_1], [("row-1", "AB,12")])

		self.assertEqual(result.issue_codes(), (CODE_CONTAINS_DELIMITER,))

	def test_a_full_width_comma_becomes_the_active_delimiter(self):
		# NFKC folds the full-width comma, so the serial is refused even though the raw text
		# did not contain the ASCII delimiter.
		result = plan([ROW_1], [("row-1", "AB，12")])

		self.assertEqual(result.issue_codes(), (CODE_CONTAINS_DELIMITER,))

	def test_a_control_character_inside_a_serial_is_refused_not_merged(self):
		# Rule set 1 deleted control characters, so `AB` + tab + `12` became `ab12` and was
		# indistinguishable from a serial typed as `AB12`. Two fragments silently became one
		# serial. They are refused now, whichever way the tab arrived, and whether or not tab
		# is an active delimiter.
		for delimiters in (("\\t",), (";",)):
			with self.subTest(delimiters=delimiters):
				result = plan([ROW_1], [("row-1", "AB\t12")], delimiters=delimiters)

				self.assertFalse(result.is_valid)
				self.assertEqual(result.issue_codes(), (CODE_HAS_CONTROL,))
				self.assertEqual(len(result.accepted), 0)

	def test_a_serial_containing_a_comma_is_accepted_when_comma_is_not_active(self):
		result = plan([ROW_1], [("row-1", "AB,12")], delimiters=(";",))

		self.assertTrue(result.is_valid)

	def test_several_problems_are_all_reported(self):
		result = plan([ROW_1], [("row-9", "A1"), ("row-1", "  "), ("row-1", "A,B")])

		self.assertEqual(result.issue_codes(), (CODE_UNKNOWN_ROW, CODE_EMPTY, CODE_CONTAINS_DELIMITER))
		self.assertEqual(len(result.accepted), 0)


class TestExcessQuantity(UnitTestCase):
	def test_more_serials_than_the_quantity_is_refused(self):
		row = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=2)

		result = plan([row], [("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3")])

		self.assertFalse(result.is_valid)
		self.assertEqual(result.issue_codes(), (CODE_EXCESS_QUANTITY,))
		self.assertEqual(result.issues[0].serials, ("A3",))

	def test_the_excess_serials_are_the_last_ones_submitted(self):
		row = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=1)

		result = plan([row], [("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3")])

		self.assertEqual(result.issues[0].serials, ("A2", "A3"))

	def test_excess_can_be_allowed(self):
		row = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=2)

		result = plan(
			[row],
			[("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3")],
			allow_over_quantity=True,
		)

		self.assertTrue(result.is_valid)
		self.assertEqual(result.coverages[0].excess, 1)

	def test_a_row_already_full_reports_every_new_serial_as_excess(self):
		row = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=2)
		existing = (
			ExistingAssignment(row_key="row-1", normalized_serial="z1"),
			ExistingAssignment(row_key="row-1", normalized_serial="z2"),
		)

		result = plan([row], [("row-1", "A1")], existing=existing)

		self.assertEqual(result.issue_codes(), (CODE_EXCESS_QUANTITY,))
		self.assertEqual(result.issues[0].serials, ("A1",))

	def test_filling_a_row_exactly_is_not_excess(self):
		row = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=2)
		existing = (ExistingAssignment(row_key="row-1", normalized_serial="z1"),)

		result = plan([row], [("row-1", "A1")], existing=existing)

		self.assertTrue(result.is_valid)

	def test_excess_on_one_row_does_not_affect_another(self):
		rows = [ROW_2, InvoiceRow(row_key="row-3", item_code="ITEM-3", invoiced_qty=1)]

		result = plan(rows, [("row-2", "B1"), ("row-3", "C1"), ("row-3", "C2")])

		self.assertEqual([issue.row_key for issue in result.issues], ["row-3"])


class TestStructuralLimits(UnitTestCase):
	def test_duplicate_row_keys_are_refused(self):
		with self.assertRaises(RegistrationError) as caught:
			plan([ROW_1, ROW_1], [])

		self.assertEqual(caught.exception.code, CODE_DUPLICATE_ROW_KEY)

	def test_too_many_rows_are_refused(self):
		rows = [
			InvoiceRow(row_key=f"row-{index}", item_code="ITEM-1", invoiced_qty=1)
			for index in range(MAX_ROWS + 1)
		]

		with self.assertRaises(RegistrationError) as caught:
			plan(rows, [])

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_ROWS)

	def test_too_many_submissions_are_refused(self):
		submissions = [("row-1", f"A{index}") for index in range(MAX_SUBMISSIONS + 1)]

		with self.assertRaises(RegistrationError) as caught:
			plan([ROW_1], submissions)

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_SUBMISSIONS)

	def test_a_broken_delimiter_configuration_stops_the_plan(self):
		with self.assertRaises(DelimiterError):
			plan([ROW_1], [], delimiters=("A",))


class TestConfigurationChecks(UnitTestCase):
	def test_the_shipped_defaults_have_no_errors(self):
		issues = check_configuration(
			delimiters=[stored for _, stored in DEFAULT_DELIMITERS],
			patterns=[pattern for _, pattern in DEFAULT_ITEM_MARKERS],
		)

		self.assertEqual(tuple(issue for issue in issues if issue.severity == SEVERITY_ERROR), ())

	def test_the_shipped_defaults_warn_about_the_line_break_overlap(self):
		issues = check_configuration(
			delimiters=[stored for _, stored in DEFAULT_DELIMITERS],
			patterns=[pattern for _, pattern in DEFAULT_ITEM_MARKERS],
		)

		overlaps = [issue for issue in issues if issue.code == CODE_CONFIG_DELIMITER_OVERLAP]

		self.assertEqual(len(overlaps), 1)
		self.assertEqual(overlaps[0].severity, SEVERITY_WARNING)

	def test_an_unusable_delimiter_is_reported(self):
		issues = check_configuration(delimiters=["A"])

		self.assertEqual([issue.code for issue in issues], [CODE_CONFIG_DELIMITER])

	def test_a_duplicate_delimiter_is_reported(self):
		issues = check_configuration(delimiters=[",", ","])

		self.assertEqual([issue.code for issue in issues], [CODE_CONFIG_DELIMITER_DUPLICATE])

	def test_an_overlapping_delimiter_is_only_a_warning(self):
		issues = check_configuration(delimiters=[",", ",,"])

		self.assertEqual([issue.code for issue in issues], [CODE_CONFIG_DELIMITER_OVERLAP])
		self.assertEqual(issues[0].severity, SEVERITY_WARNING)

	def test_a_broken_marker_pattern_is_reported(self):
		issues = check_configuration(patterns=["**"])

		self.assertEqual([issue.code for issue in issues], [CODE_CONFIG_MARKER])

	def test_ambiguous_markers_are_reported(self):
		issues = check_configuration(patterns=["{n}*", "{n}*"])

		self.assertEqual([issue.code for issue in issues], [CODE_CONFIG_MARKER_AMBIGUOUS])

	def test_a_delimiter_inside_a_marker_is_reported(self):
		# With `*` active as a separator the marker `{n}*` is split before it is recognised,
		# so the grouped import would silently become a flat list of serials.
		issues = check_configuration(delimiters=["*"], patterns=["{n}*"])

		self.assertEqual([issue.code for issue in issues], [CODE_CONFIG_DELIMITER_MARKER_CONFLICT])
		self.assertEqual(issues[0].severity, SEVERITY_ERROR)

	def test_a_clean_configuration_reports_nothing(self):
		issues = check_configuration(delimiters=[",", ";"], patterns=["{n}*", "#{n}"])

		self.assertEqual(issues, ())
