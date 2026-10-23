"""Pure tests for physical/virtual stock totals and deterministic allocation."""

from decimal import Decimal

from smartprocess_logistics.core import inventory
from smartprocess_logistics.core.inventory import (
	AllocationItem,
	ItemStock,
	WarehouseStock,
)
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


def stock(
	item_code="ITEM",
	warehouse="PHYS",
	warehouse_type=None,
	actual_qty=0,
	company="COMPANY",
	**overrides,
):
	return WarehouseStock(
		company=company,
		item_code=item_code,
		warehouse=warehouse,
		warehouse_type=warehouse_type,
		actual_qty=actual_qty,
		**overrides,
	)


class TestDecimalSafety(UnitTestCase):
	def test_missing_values_read_as_zero(self):
		for value in (None, ""):
			with self.subTest(value=value):
				self.assertEqual(inventory.as_decimal(value), D("0"))

	def test_numeric_values_are_converted_without_binary_float_artifacts(self):
		self.assertEqual(inventory.as_decimal("0.1"), D("0.1"))
		self.assertEqual(inventory.as_decimal(0.1), D("0.1"))
		self.assertEqual(inventory.as_decimal(3), D("3"))

	def test_a_non_numeric_value_is_refused(self):
		for value in ("abc", [], {}, True, D("NaN"), D("Infinity")):
			with self.subTest(value=value):
				with self.assertRaises(ValueError):
					inventory.as_decimal(value, "qty")

	def test_quantities_are_rounded_once_to_frappe_precision(self):
		self.assertEqual(inventory.quantity("1.2345678"), D("1.234568"))
		self.assertEqual(inventory.quantity("1.2345674"), D("1.234567"))

	def test_a_conversion_factor_must_be_positive(self):
		for value in (0, -1, "abc", None):
			with self.subTest(value=value):
				with self.assertRaises(ValueError):
					inventory.positive_factor(value)

	def test_a_line_quantity_is_converted_to_stock_uom(self):
		self.assertEqual(inventory.stock_quantity(2, 12), D("24"))
		with self.assertRaises(ValueError):
			inventory.stock_quantity(2, 0)


class TestStockSeparation(UnitTestCase):
	def rows(self):
		return (
			stock("A", "P1", None, 10),
			stock("A", "V1", "Virtual", 4),
			stock("A", "V2", "Transit", 2),
			stock("A", "P2", None, 7, disabled=True),
			stock("A", "G1", None, 50, is_group=True),
			stock("A", "OTHER", None, 100, company="OTHER"),
		)

	def test_physical_and_virtual_are_separate(self):
		by_item = inventory.stock_by_item(self.rows(), "COMPANY")

		self.assertEqual(by_item["A"].physical, D("10"))
		self.assertEqual(by_item["A"].virtual, D("6"))
		self.assertEqual(by_item["A"].total, D("16"))

	def test_group_disabled_and_other_company_warehouses_are_not_counted(self):
		totals = inventory.stock_totals(self.rows(), "COMPANY")

		self.assertEqual(totals.physical, D("10"))
		self.assertEqual(totals.virtual, D("6"))
		self.assertEqual(totals.total, D("16"))

	def test_disabled_warehouses_can_be_included_explicitly(self):
		totals = inventory.stock_totals(self.rows(), "COMPANY", include_disabled=True)

		self.assertEqual(totals.physical, D("17"))

	def test_permission_limited_warehouses_are_not_counted(self):
		totals = inventory.stock_totals(
			self.rows(),
			"COMPANY",
			permitted_warehouses={"P1", "V1"},
		)

		self.assertEqual(totals.physical, D("10"))
		self.assertEqual(totals.virtual, D("4"))

	def test_an_empty_permission_set_means_no_stock(self):
		self.assertEqual(inventory.stock_by_item(self.rows(), "COMPANY", permitted_warehouses=set()), {})

	def test_a_missing_company_is_refused(self):
		with self.assertRaises(ValueError):
			inventory.stock_by_item(self.rows(), "")

	def test_negative_stock_is_kept_visible(self):
		rows = (
			stock("A", "P1", None, -5),
			stock("A", "V1", "Virtual", -2),
		)

		by_item = inventory.stock_by_item(rows, "COMPANY")

		self.assertEqual(by_item["A"].physical, D("-5"))
		self.assertEqual(by_item["A"].virtual, D("-2"))
		self.assertEqual(by_item["A"].total, D("-7"))

	def test_custom_virtual_types_are_honoured(self):
		rows = (stock("A", "CUSTOM", "Hold", 3),)

		self.assertEqual(
			inventory.stock_by_item(rows, "COMPANY", virtual_types=("Hold",))["A"].virtual,
			D("3"),
		)


class TestAvailability(UnitTestCase):
	def test_physically_deliverable_stock_does_not_subtract_open_orders(self):
		self.assertEqual(inventory.physically_deliverable_stock(ItemStock(physical=D("10"))), D("10"))

	def test_available_for_new_sales_subtracts_the_allocation(self):
		self.assertEqual(
			inventory.available_for_new_sales(ItemStock(physical=D("10")), D("4")),
			D("6"),
		)

	def test_native_reservations_are_not_subtracted_twice_by_default(self):
		stock_doc = ItemStock(physical=D("10"), reserved=D("6"))

		self.assertEqual(
			inventory.available_for_new_sales(stock_doc, D("4"), native_reserved=D("6")),
			D("6"),
		)
		self.assertEqual(
			inventory.available_for_new_sales(
				stock_doc,
				D("4"),
				native_reserved=D("6"),
				reserved_in_open_demand=False,
			),
			D("0"),
		)

	def test_reserved_quantity_is_reported_but_not_mixed_into_physical(self):
		rows = (stock("A", "P1", None, 10, reserved_qty=4),)

		by_item = inventory.stock_by_item(rows, "COMPANY")

		self.assertEqual(by_item["A"].physical, D("10"))
		self.assertEqual(by_item["A"].reserved, D("4"))
		self.assertEqual(by_item["A"].total, D("10"))


class TestAllocationStrategies(UnitTestCase):
	def items(self):
		return (
			AllocationItem(
				key="A",
				item_code="ITEM",
				demand=5,
				transaction_date="2026-01-01",
				delivery_date="2026-02-01",
				sales_order="SO-2",
				idx=1,
			),
			AllocationItem(
				key="B",
				item_code="ITEM",
				demand=5,
				transaction_date="2026-01-02",
				delivery_date="2026-01-05",
				sales_order="SO-1",
				idx=1,
			),
		)

	def test_oldest_order_first_uses_the_transaction_date(self):
		result = inventory.allocate(self.items(), {"ITEM": 6}, strategy=inventory.OLDEST_ORDER)

		self.assertEqual(result.allocations["A"], D("5"))
		self.assertEqual(result.allocations["B"], D("1"))

	def test_earliest_delivery_first_uses_the_delivery_date(self):
		result = inventory.allocate(self.items(), {"ITEM": 6}, strategy=inventory.EARLIEST_DELIVERY)

		self.assertEqual(result.allocations["A"], D("1"))
		self.assertEqual(result.allocations["B"], D("5"))

	def test_order_then_delivery_is_the_default(self):
		result = inventory.allocate(self.items(), {"ITEM": 6})

		self.assertEqual(result.strategy, inventory.ORDER_THEN_DELIVERY)
		self.assertEqual(result.allocations["A"], D("5"))
		self.assertEqual(result.allocations["B"], D("1"))

	def test_ties_are_broken_deterministically(self):
		items = (
			AllocationItem(
				key="B", item_code="ITEM", demand=5, sales_order="SO-B", transaction_date="2026-01-01"
			),
			AllocationItem(
				key="A", item_code="ITEM", demand=5, sales_order="SO-A", transaction_date="2026-01-01"
			),
		)

		result = inventory.allocate(items, {"ITEM": 5}, strategy=inventory.OLDEST_ORDER)

		self.assertEqual(result.allocations["A"], D("5"))
		self.assertEqual(result.allocations["B"], D("0"))

	def test_the_same_stock_is_never_allocated_twice(self):
		items = (
			AllocationItem(key="A", item_code="ITEM", demand=8, transaction_date="2026-01-01"),
			AllocationItem(key="B", item_code="ITEM", demand=8, transaction_date="2026-01-02"),
		)

		result = inventory.allocate(items, {"ITEM": 10}, strategy=inventory.OLDEST_ORDER)

		self.assertEqual(result.allocations["A"], D("8"))
		self.assertEqual(result.allocations["B"], D("2"))
		self.assertEqual(result.remaining["ITEM"], D("0"))

	def test_items_are_allocated_from_their_own_stock_only(self):
		items = (
			AllocationItem(key="A", item_code="ONE", demand=10),
			AllocationItem(key="B", item_code="TWO", demand=10),
		)

		result = inventory.allocate(items, {"ONE": 3, "TWO": 8})

		self.assertEqual(result.allocations["A"], D("3"))
		self.assertEqual(result.allocations["B"], D("8"))
		self.assertEqual(result.remaining["ONE"], D("0"))
		self.assertEqual(result.remaining["TWO"], D("0"))

	def test_negative_stock_and_negative_demand_are_not_allocated(self):
		items = (AllocationItem(key="A", item_code="ITEM", demand=-5),)

		result = inventory.allocate(items, {"ITEM": -3})

		self.assertEqual(result.allocations["A"], D("0"))
		self.assertEqual(result.remaining["ITEM"], D("0"))

	def test_an_unknown_strategy_is_refused(self):
		with self.assertRaises(ValueError):
			inventory.allocate(self.items(), {"ITEM": 5}, strategy="unknown")

	def test_a_malformed_index_does_not_abort_allocation(self):
		items = (AllocationItem(key="A", item_code="ITEM", demand=1, idx="not-a-number"),)

		result = inventory.allocate(items, {"ITEM": 1}, strategy=inventory.OLDEST_ORDER)

		self.assertEqual(result.allocations["A"], D("1"))

	def test_duplicate_keys_are_refused(self):
		items = (
			AllocationItem(key="A", item_code="ITEM", demand=1),
			AllocationItem(key="A", item_code="ITEM", demand=1),
		)

		with self.assertRaises(ValueError):
			inventory.allocate(items, {"ITEM": 5})

	def test_items_need_a_key_and_an_item_code(self):
		for item in (
			AllocationItem(key="", item_code="ITEM", demand=1),
			AllocationItem(key="A", item_code="", demand=1),
		):
			with self.subTest(item=item):
				with self.assertRaises(ValueError):
					inventory.allocate((item,), {"ITEM": 5})

	def test_inputs_are_not_mutated(self):
		items = self.items()
		stocks = {"ITEM": D("6")}

		inventory.allocate(items, stocks)

		self.assertEqual(stocks, {"ITEM": D("6")})
		self.assertEqual(tuple(item.key for item in items), ("A", "B"))
