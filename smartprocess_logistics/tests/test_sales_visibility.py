"""Pure tests for the Sales Invoice, Quotation and Sales Order Link descriptions."""

from decimal import Decimal

from smartprocess_logistics.core import sales_visibility
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


class TestStockText(UnitTestCase):
	def test_sales_invoice_shows_physical_stock_only(self):
		self.assertEqual(
			sales_visibility.stock_text(10, "Nos"),
			"موجودی فیزیکی: 10 Nos",
		)

	def test_sales_invoice_line_without_a_unit_has_no_trailing_space(self):
		self.assertEqual(sales_visibility.stock_text(10), "موجودی فیزیکی: 10")

	def test_quotation_and_sales_order_put_physical_and_transit_on_one_line(self):
		self.assertEqual(
			sales_visibility.combined_stock_text(10, 5),
			"موجودی فیزیکی: 10 | موجودی ترانزیت: 5",
		)

	def test_the_two_quantities_are_never_added_together(self):
		self.assertEqual(
			sales_visibility.combined_stock_text(10, 5),
			"موجودی فیزیکی: 10 | موجودی ترانزیت: 5",
		)
		self.assertNotIn("15", sales_visibility.combined_stock_text(10, 5))

	def test_zero_and_negative_stock_are_visible_rather_than_hidden(self):
		self.assertEqual(
			sales_visibility.combined_stock_text(-2, 0),
			"موجودی فیزیکی: -2 | موجودی ترانزیت: 0",
		)
		self.assertEqual(
			sales_visibility.combined_stock_text(None, None),
			"موجودی فیزیکی: 0 | موجودی ترانزیت: 0",
		)

	def test_the_item_code_stays_the_link_value(self):
		row = sales_visibility.item_suggestion("ITEM-1", "Lamp", D("10"), D("5"), "Nos", True)

		self.assertEqual(row[0], "ITEM-1")
		self.assertEqual(row[1], "Lamp")
		self.assertEqual(row[2], "موجودی فیزیکی: 10 | موجودی ترانزیت: 5")

	def test_the_sales_invoice_suggestion_keeps_one_description_line(self):
		row = sales_visibility.item_suggestion("ITEM-1", "Lamp", D("10"), D("5"), "Nos", False)

		self.assertEqual(row, ("ITEM-1", "Lamp", "موجودی فیزیکی: 10 Nos"))

	def test_warehouse_selector_shows_zero_and_actual_quantities(self):
		self.assertEqual(sales_visibility.warehouse_stock_text(0), "موجودی: 0")
		self.assertEqual(sales_visibility.warehouse_stock_text(D("24")), "موجودی: 24")
		self.assertEqual(sales_visibility.warehouse_stock_text(D("-2")), "موجودی: -2")
		self.assertEqual(sales_visibility.warehouse_stock_text(3, "transit"), "موجودی ترانزیت: 3")
		self.assertEqual(sales_visibility.warehouse_stock_text(4, "virtual"), "موجودی مجازی: 4")
