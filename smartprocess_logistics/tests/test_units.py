"""A byte ceiling has to read back as the number the operator typed.

The readers hold their ceilings in bytes and the messages used integer megabytes, so a 1 KB cap
was reported as "0 megabytes" — a refusal that told the operator the file was larger than
nothing. The last test here is that case.
"""

from smartprocess_logistics.core.units import KILOBYTE, MEGABYTE, describe_bytes
from smartprocess_logistics.tests.base import UnitTestCase


class TestDescribeBytes(UnitTestCase):
	def test_megabytes_at_and_above_a_megabyte(self):
		self.assertEqual(describe_bytes(MEGABYTE), "1 مگابایت")
		self.assertEqual(describe_bytes(8 * MEGABYTE), "8 مگابایت")

	def test_kilobytes_below_a_megabyte(self):
		self.assertEqual(describe_bytes(512 * KILOBYTE), "512 کیلوبایت")

	def test_a_kilobyte_cap_is_not_reported_as_zero(self):
		"""The defect: `1 * KILOBYTE // MEGABYTE` is 0, so the cap read as "0 megabytes"."""
		self.assertEqual(describe_bytes(KILOBYTE), "1 کیلوبایت")
		self.assertNotIn("0 ", describe_bytes(KILOBYTE))

	def test_bytes_below_a_kilobyte(self):
		self.assertEqual(describe_bytes(512), "512 بایت")
		self.assertEqual(describe_bytes(0), "0 بایت")

	def test_a_fraction_keeps_its_fraction(self):
		self.assertEqual(describe_bytes(MEGABYTE + 512 * KILOBYTE), "1.5 مگابایت")
		self.assertEqual(describe_bytes(KILOBYTE + 512), "1.5 کیلوبایت")
