"""Pure tests for the two Sales Invoice view filters.

Both decide one row at a time and nothing else: an item survives on its physical stock, a
warehouse survives on that item's stock in it. Company isolation and permissions are not here —
they belong to the query that supplies the rows, and the endpoints keep them.
"""

from decimal import Decimal

from smartprocess_logistics.core import selection_filters
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


class TestWants(UnitTestCase):
	def test_the_link_forms_are_read_as_flags(self):
		for value in ("1", 1, True, "true", "TRUE", "yes", "on"):
			with self.subTest(value=value):
				self.assertTrue(selection_filters.wants(value))

	def test_everything_else_is_off(self):
		for value in ("0", 0, False, None, "", "no", "off"):
			with self.subTest(value=value):
				self.assertFalse(selection_filters.wants(value))


class TestKeepItem(UnitTestCase):
	def test_off_keeps_every_item_whatever_its_stock(self):
		for value in (None, 0, D("0"), D("-2"), D("5"), "3"):
			with self.subTest(value=value):
				self.assertTrue(selection_filters.keep_item(value, False))

	def test_on_keeps_only_positive_stock(self):
		self.assertFalse(selection_filters.keep_item(0, True))
		self.assertFalse(selection_filters.keep_item(None, True))
		self.assertFalse(selection_filters.keep_item(D("-2"), True))
		self.assertTrue(selection_filters.keep_item(D("5"), True))
		self.assertTrue(selection_filters.keep_item("0.000001", True))

	def test_each_item_is_judged_on_its_own_stock(self):
		rows = {"A": D("0"), "B": D("4"), "C": D("-1"), "D": D("7")}

		kept = [code for code, qty in rows.items() if selection_filters.keep_item(qty, True)]

		self.assertEqual(kept, ["B", "D"])


class TestKeepWarehouse(UnitTestCase):
	def test_off_keeps_every_warehouse(self):
		for value in (None, 0, D("-1"), D("9")):
			with self.subTest(value=value):
				self.assertTrue(selection_filters.keep_warehouse(value, False))

	def test_on_keeps_only_the_warehouses_that_hold_the_item(self):
		self.assertFalse(selection_filters.keep_warehouse(0, True))
		self.assertFalse(selection_filters.keep_warehouse(None, True))
		self.assertFalse(selection_filters.keep_warehouse(D("-3"), True))
		self.assertTrue(selection_filters.keep_warehouse(D("9"), True))

	def test_multiple_warehouses_are_judged_one_by_one(self):
		rows = {"SP-PH-C": D("0"), "SP-PH-D": D("12"), "SP-VW-A": D("0"), "پارسیان": D("100")}

		kept = [name for name, qty in rows.items() if selection_filters.keep_warehouse(qty, True)]

		self.assertEqual(kept, ["SP-PH-D", "پارسیان"])
