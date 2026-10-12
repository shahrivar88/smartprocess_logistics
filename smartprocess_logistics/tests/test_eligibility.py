"""Which invoices the missing-serial report may cover."""

from datetime import datetime, timedelta, timezone

from smartprocess_logistics.tests.base import UnitTestCase

from smartprocess_logistics.core.eligibility import (
	CODE_NOT_A_DOCSTATUS,
	CODE_NOT_A_TIMESTAMP,
	CODE_TIMESTAMP_MISMATCH,
	DOCSTATUS_CANCELLED,
	DOCSTATUS_DRAFT,
	DOCSTATUS_SUBMITTED,
	REASON_CANCELLED,
	REASON_CREATED_BEFORE_INSTALL,
	REASON_NOT_INSTALLED,
	EligibilityError,
	ineligibility_reason,
	is_report_eligible,
	parse_timestamp,
)

INSTALLED = datetime(2026, 10, 12, 18, 30)
BEFORE = INSTALLED - timedelta(seconds=1)
AFTER = INSTALLED + timedelta(seconds=1)


class TestReportEligibility(UnitTestCase):
	def test_an_invoice_created_after_installation_is_eligible(self):
		self.assertTrue(
			is_report_eligible(
				invoice_creation=AFTER,
				installed_on=INSTALLED,
				docstatus=DOCSTATUS_SUBMITTED,
			)
		)

	def test_the_installation_instant_itself_is_eligible(self):
		self.assertTrue(
			is_report_eligible(
				invoice_creation=INSTALLED,
				installed_on=INSTALLED,
				docstatus=DOCSTATUS_DRAFT,
			)
		)

	def test_one_second_before_installation_is_not_eligible(self):
		reason = ineligibility_reason(
			invoice_creation=BEFORE,
			installed_on=INSTALLED,
			docstatus=DOCSTATUS_SUBMITTED,
		)

		self.assertEqual(reason, REASON_CREATED_BEFORE_INSTALL)

	def test_an_old_invoice_submitted_after_installation_stays_out(self):
		# Eligibility follows creation, not submission, so the submission state cannot rescue
		# an invoice that predates the app.
		self.assertFalse(
			is_report_eligible(
				invoice_creation=BEFORE,
				installed_on=INSTALLED,
				docstatus=DOCSTATUS_SUBMITTED,
			)
		)

	def test_a_cancelled_invoice_is_out_by_default(self):
		reason = ineligibility_reason(
			invoice_creation=AFTER,
			installed_on=INSTALLED,
			docstatus=DOCSTATUS_CANCELLED,
		)

		self.assertEqual(reason, REASON_CANCELLED)

	def test_a_cancelled_invoice_can_be_included_explicitly(self):
		self.assertTrue(
			is_report_eligible(
				invoice_creation=AFTER,
				installed_on=INSTALLED,
				docstatus=DOCSTATUS_CANCELLED,
				include_cancelled=True,
			)
		)

	def test_a_cancelled_invoice_from_before_installation_stays_out_even_when_included(self):
		reason = ineligibility_reason(
			invoice_creation=BEFORE,
			installed_on=INSTALLED,
			docstatus=DOCSTATUS_CANCELLED,
			include_cancelled=True,
		)

		self.assertEqual(reason, REASON_CREATED_BEFORE_INSTALL)

	def test_no_installation_timestamp_means_nothing_is_eligible(self):
		for installed_on in (None, ""):
			with self.subTest(installed_on=installed_on):
				reason = ineligibility_reason(
					invoice_creation=AFTER,
					installed_on=installed_on,
					docstatus=DOCSTATUS_SUBMITTED,
				)

				self.assertEqual(reason, REASON_NOT_INSTALLED)

	def test_a_missing_timestamp_outranks_the_cancelled_reason(self):
		reason = ineligibility_reason(
			invoice_creation=AFTER,
			installed_on=None,
			docstatus=DOCSTATUS_CANCELLED,
		)

		self.assertEqual(reason, REASON_NOT_INSTALLED)

	def test_iso_strings_are_accepted(self):
		self.assertTrue(
			is_report_eligible(
				invoice_creation="2026-10-13 09:00:00",
				installed_on="2026-10-12 18:30:00",
				docstatus=DOCSTATUS_SUBMITTED,
			)
		)

	def test_aware_timestamps_are_compared_with_each_other(self):
		zone = timezone(timedelta(hours=3, minutes=30))

		self.assertTrue(
			is_report_eligible(
				invoice_creation=datetime(2026, 10, 13, 9, 0, tzinfo=zone),
				installed_on=datetime(2026, 10, 12, 18, 30, tzinfo=zone),
				docstatus=DOCSTATUS_SUBMITTED,
			)
		)

	def test_mixing_aware_and_naive_timestamps_is_refused(self):
		with self.assertRaises(EligibilityError) as caught:
			ineligibility_reason(
				invoice_creation=datetime(2026, 10, 13, tzinfo=timezone.utc),
				installed_on=INSTALLED,
				docstatus=DOCSTATUS_SUBMITTED,
			)

		self.assertEqual(caught.exception.code, CODE_TIMESTAMP_MISMATCH)

	def test_an_unknown_docstatus_is_refused(self):
		for docstatus in (3, -1, "1", None, True):
			with self.subTest(docstatus=docstatus):
				with self.assertRaises(EligibilityError) as caught:
					ineligibility_reason(
						invoice_creation=AFTER,
						installed_on=INSTALLED,
						docstatus=docstatus,
					)

				self.assertEqual(caught.exception.code, CODE_NOT_A_DOCSTATUS)

	def test_an_unparsable_timestamp_is_refused(self):
		with self.assertRaises(EligibilityError) as caught:
			parse_timestamp("not a date")

		self.assertEqual(caught.exception.code, CODE_NOT_A_TIMESTAMP)

	def test_a_non_timestamp_type_is_refused(self):
		for value in (12, 12.5, [], {}, object()):
			with self.subTest(value=value):
				with self.assertRaises(EligibilityError) as caught:
					parse_timestamp(value)

				self.assertEqual(caught.exception.code, CODE_NOT_A_TIMESTAMP)

	def test_a_datetime_passes_through_unchanged(self):
		self.assertIs(parse_timestamp(INSTALLED), INSTALLED)
