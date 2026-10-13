"""The Settings row may tighten a ceiling, never raise it.

Four limits on `Logistics Settings` are documented as server behaviour and, until `core.limits`
existed, nothing read them: an administrator could lower the file cap to 100 KB and a 4 MB
workbook would still be read. These tests pin the derivation, including the case that matters
most — a settings row asking for *more* than the reader was built for.
"""

from smartprocess_logistics.core.archives import ArchiveLimits
from smartprocess_logistics.core.delimiters import MAX_TOKENS
from smartprocess_logistics.core.limits import (
	DEFAULT_PAGE_LENGTH,
	KILOBYTE,
	MAX_PAGE_LENGTH,
	payload_limits,
)
from smartprocess_logistics.core.spreadsheet import SheetLimits
from smartprocess_logistics.core.textfile import MAX_TEXT_BYTES
from smartprocess_logistics.tests.base import UnitTestCase


class TestNothingConfigured(UnitTestCase):
	def test_no_setting_keeps_every_readers_own_ceiling(self):
		limits = payload_limits()

		self.assertEqual(limits.max_bytes, MAX_TEXT_BYTES)
		self.assertEqual(limits.max_tokens, MAX_TOKENS)
		self.assertEqual(limits.archive, ArchiveLimits())
		self.assertEqual(limits.sheets, SheetLimits())
		self.assertEqual(limits.default_page_length, DEFAULT_PAGE_LENGTH)
		self.assertEqual(limits.max_page_length, MAX_PAGE_LENGTH)

	def test_a_blank_field_is_treated_as_no_setting(self):
		for blank in (None, "", 0, "0", -1, "abc"):
			with self.subTest(value=blank):
				limits = payload_limits(file_kb=blank, rows=blank, sheets=blank, max_page=blank)

				self.assertEqual(limits.max_bytes, MAX_TEXT_BYTES)
				self.assertEqual(limits.archive, ArchiveLimits())
				self.assertEqual(limits.sheets, SheetLimits())
				self.assertEqual(limits.max_page_length, MAX_PAGE_LENGTH)


class TestShippedSettings(UnitTestCase):
	"""The values a fresh install writes: 512 KB, 5000 rows, 20 sheets, 50 and 500 page sizes."""

	def limits(self):
		return payload_limits(file_kb=512, rows=5000, sheets=20, default_page=50, max_page=500)

	def test_the_file_cap_is_applied_to_both_readers(self):
		limits = self.limits()

		self.assertEqual(limits.max_bytes, 512 * KILOBYTE)
		self.assertEqual(limits.archive.max_file_bytes, 512 * KILOBYTE)

	def test_the_row_cap_reaches_the_sheet_reader_and_the_text_splitter(self):
		limits = self.limits()

		self.assertEqual(limits.sheets.max_rows, 5000)
		self.assertEqual(limits.max_tokens, 5000)

	def test_the_sheet_cap_matches_the_readers_own_default(self):
		limits = self.limits()

		self.assertEqual(limits.sheets.max_sheets, 20)
		self.assertEqual(limits.archive.max_worksheets, 20)

	def test_the_page_limits_match_the_fields(self):
		limits = self.limits()

		self.assertEqual(limits.default_page_length, 50)
		self.assertEqual(limits.max_page_length, 500)


class TestRaisingACeiling(UnitTestCase):
	def test_a_bigger_file_cap_cannot_lift_a_readers_own_ceiling(self):
		"""100 MB asked for; the readers keep the ceilings they were built and tested against."""
		limits = payload_limits(file_kb=100_000)

		self.assertEqual(limits.max_bytes, MAX_TEXT_BYTES)
		self.assertEqual(limits.archive.max_file_bytes, ArchiveLimits().max_file_bytes)

	def test_more_rows_than_the_reader_allows_is_capped(self):
		limits = payload_limits(rows=10_000_000)

		self.assertEqual(limits.sheets.max_rows, SheetLimits().max_rows)
		self.assertEqual(limits.max_tokens, MAX_TOKENS)

	def test_the_workbook_ceiling_is_not_derived_from_the_text_ceiling(self):
		"""A 6 MB workbook is under the workbook reader's 8 MB and over the text reader's 4 MB."""
		limits = payload_limits()

		self.assertLess(limits.max_bytes, limits.archive.max_file_bytes)


class TestPageLimits(UnitTestCase):
	def test_a_smaller_page_cap_is_applied(self):
		limits = payload_limits(default_page=10, max_page=25)

		self.assertEqual(limits.default_page_length, 10)
		self.assertEqual(limits.max_page_length, 25)

	def test_a_page_cap_above_the_shipped_one_is_capped(self):
		limits = payload_limits(max_page=100_000)

		self.assertEqual(limits.max_page_length, MAX_PAGE_LENGTH)
