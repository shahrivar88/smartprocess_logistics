"""Pure tests for the Sales Invoice and Sales Order Item-Link description."""

from decimal import Decimal

from smartprocess_logistics.core import sales_visibility
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


class TestStockText(UnitTestCase):
	def test_sales_invoice_shows_physical_stock_only(self):
		self.assertEqual(
			sales_visibility.stock_text(10, 5, "Nos", include_virtual=False),
			["موجودی فیزیکی: 10 Nos"],
		)

	def test_sales_order_shows_physical_and_virtual_separately(self):
		self.assertEqual(
			sales_visibility.stock_text(10, 5, "Nos", include_virtual=True),
			["موجودی فیزیکی: 10 Nos", "موجودی مجازی: 5 Nos"],
		)

	def test_zero_and_negative_stock_are_visible_rather_than_hidden(self):
		self.assertEqual(
			sales_visibility.stock_text(-2, 0, "Nos", include_virtual=True),
			["موجودی فیزیکی: -2 Nos", "موجودی مجازی: 0 Nos"],
		)

	def test_the_item_code_stays_the_link_value(self):
		row = sales_visibility.item_suggestion("ITEM-1", "Lamp", D("10"), D("5"), "Nos", True)

		self.assertEqual(row[0], "ITEM-1")
		self.assertEqual(row[1], "Lamp")
		self.assertIn("موجودی فیزیکی", row[2])
		self.assertIn("موجودی مجازی", row[3])
