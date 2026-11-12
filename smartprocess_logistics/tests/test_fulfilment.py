"""Pure tests for the operational fulfilment report.

Three promises are pinned here: virtual stock never reaches a total, a converted quotation is
not demand twice, and physical and transit stay separate columns.
"""

from decimal import Decimal

from smartprocess_logistics.core import fulfilment
from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.warehouse import policy

D = Decimal
TODAY = "2026-10-30"


def quotation_line(
	name="Q1",
	quotation="QUO-1",
	company="COMPANY",
	item_code="ITEM",
	stock_qty=10,
	ordered_qty=0,
	qty=None,
	conversion_factor=1,
	status="Open",
	docstatus=1,
	valid_till=None,
):
	return fulfilment.QuotationLine(
		name=name,
		quotation=quotation,
		company=company,
		item_code=item_code,
		qty=stock_qty if qty is None else qty,
		stock_qty=stock_qty,
		ordered_qty=ordered_qty,
		conversion_factor=conversion_factor,
		status=status,
		docstatus=docstatus,
		valid_till=valid_till,
	)


class TestWarehouseKind(UnitTestCase):
	def test_the_three_kinds(self):
		self.assertEqual(policy.kind(None), policy.PHYSICAL)
		self.assertEqual(policy.kind(""), policy.PHYSICAL)
		self.assertEqual(policy.kind("Physical"), policy.PHYSICAL)
		self.assertEqual(policy.kind("Transit"), policy.TRANSIT)
		self.assertEqual(policy.kind("Virtual"), policy.VIRTUAL)
		self.assertEqual(policy.kind("مجازی"), policy.VIRTUAL)

	def test_both_stay_unsellable(self):
		"""`is_virtual_type` answers the sales question and must keep answering it the same way."""
		self.assertTrue(policy.is_virtual_type("Transit"))
		self.assertTrue(policy.is_virtual_type("Virtual"))
		self.assertFalse(policy.is_virtual_type("Physical"))
		self.assertFalse(policy.is_virtual_type(None))

	def test_a_site_added_unsellable_type_counts_as_virtual(self):
		self.assertEqual(policy.kind("Quarantine", ("Quarantine", "Transit")), policy.VIRTUAL)

	def test_the_configured_list_decides_membership(self):
		"""An override that leaves `Transit` out makes transit stock physical, not transit."""
		override = ("Virtual", "مجازی")

		self.assertEqual(policy.kind("Transit", override), policy.PHYSICAL)
		self.assertFalse(policy.is_virtual_type("Transit", override))
		self.assertEqual(policy.kind("Virtual", override), policy.VIRTUAL)

	def test_every_kind_agrees_with_the_unsellable_answer(self):
		for warehouse_type in ("", None, "Physical", "Virtual", "Transit", "Quarantine"):
			for configured in (
				policy.DEFAULT_VIRTUAL_TYPES,
				("Virtual", "مجازی"),
				("Quarantine", "Transit"),
				(),
			):
				with self.subTest(warehouse_type=warehouse_type, configured=configured):
					self.assertEqual(
						policy.kind(warehouse_type, configured) != policy.PHYSICAL,
						policy.is_virtual_type(warehouse_type, configured),
					)


class TestStockByItem(UnitTestCase):
	def test_physical_and_transit_stay_separate_and_virtual_disappears(self):
		bins = [
			{"item_code": "A", "warehouse_type": None, "actual_qty": 10},
			{"item_code": "A", "warehouse_type": "Physical", "actual_qty": 5},
			{"item_code": "A", "warehouse_type": "Transit", "actual_qty": 7},
			{"item_code": "A", "warehouse_type": "Virtual", "actual_qty": 100},
			{"item_code": "B", "warehouse_type": "Transit", "actual_qty": 3},
		]

		stock = fulfilment.stock_by_item(bins)

		self.assertEqual(stock["A"].physical, D("15"))
		self.assertEqual(stock["A"].transit, D("7"))
		self.assertEqual(stock["B"].physical, D("0"))
		self.assertEqual(stock["B"].transit, D("3"))

	def test_a_virtual_only_item_has_no_row(self):
		stock = fulfilment.stock_by_item(
			[{"item_code": "A", "warehouse_type": "Virtual", "actual_qty": 50}]
		)

		self.assertEqual(stock, {})

	def test_negative_stock_is_kept_as_it_is(self):
		stock = fulfilment.stock_by_item(
			[{"item_code": "A", "warehouse_type": "Transit", "actual_qty": -2}]
		)

		self.assertEqual(stock["A"].transit, D("-2"))


class TestQuotationDemand(UnitTestCase):
	def test_an_open_quotation_counts_its_full_quantity(self):
		self.assertEqual(fulfilment.quotation_remaining(quotation_line(), TODAY), D("10"))

	def test_a_partially_converted_quotation_counts_only_the_remainder(self):
		line = quotation_line(stock_qty=10, ordered_qty=4)

		self.assertEqual(fulfilment.quotation_remaining(line, TODAY), D("6"))

	def test_a_fully_converted_quotation_is_no_demand(self):
		self.assertEqual(fulfilment.quotation_remaining(quotation_line(ordered_qty=10), TODAY), D("0"))
		self.assertEqual(fulfilment.quotation_remaining(quotation_line(ordered_qty=12), TODAY), D("0"))

	def test_closed_states_carry_no_demand(self):
		for status in ("Ordered", "Lost", "Cancelled", "Expired"):
			with self.subTest(status=status):
				self.assertEqual(
					fulfilment.quotation_remaining(quotation_line(status=status), TODAY), D("0")
				)

	def test_drafts_are_not_demand(self):
		self.assertEqual(fulfilment.quotation_remaining(quotation_line(docstatus=0), TODAY), D("0"))

	def test_an_expired_quotation_is_not_demand_even_before_the_daily_job_catches_up(self):
		line = quotation_line(valid_till="2026-10-01")

		self.assertEqual(fulfilment.quotation_remaining(line, TODAY), D("0"))
		self.assertEqual(fulfilment.quotation_remaining(quotation_line(valid_till=TODAY), TODAY), D("10"))
		self.assertEqual(fulfilment.quotation_remaining(quotation_line(valid_till="2026-11-30"), TODAY), D("10"))

	def test_stock_quantity_is_used_when_present_and_computed_otherwise(self):
		self.assertEqual(
			fulfilment.quotation_remaining(
				quotation_line(qty=2, stock_qty=None, conversion_factor=12), TODAY
			),
			D("24"),
		)

	def test_demand_is_grouped_per_item_and_company(self):
		lines = [
			quotation_line(name="Q1", item_code="A", stock_qty=10),
			quotation_line(name="Q2", quotation="QUO-2", item_code="A", stock_qty=5, ordered_qty=5),
			quotation_line(name="Q3", item_code="B", stock_qty=2),
			quotation_line(name="Q4", item_code="A", stock_qty=99, company="OTHER"),
		]

		demand = fulfilment.quotation_demand_by_item(lines, "COMPANY", TODAY)

		self.assertEqual(demand, {"A": D("10"), "B": D("2")})


class TestDemandAndShortage(UnitTestCase):
	def test_total_demand_is_the_sum_of_both_sources(self):
		self.assertEqual(fulfilment.total_demand(D("6"), D("4")), D("10"))

	def test_negative_inputs_never_reduce_demand(self):
		self.assertEqual(fulfilment.total_demand(D("-3"), D("4")), D("4"))

	def test_shortage_uses_physical_and_transit_and_ignores_virtual(self):
		self.assertEqual(fulfilment.order_shortage(D("10"), D("4"), D("3")), D("3"))
		self.assertEqual(fulfilment.order_shortage(D("10"), D("4"), D("6")), D("0"))
		self.assertEqual(fulfilment.order_shortage(D("0"), D("5"), D("0")), D("0"))

	def test_negative_stock_increases_the_shortage(self):
		self.assertEqual(fulfilment.order_shortage(D("10"), D("-2"), D("0")), D("12"))


class TestRows(UnitTestCase):
	def stock(self):
		return {
			"A": fulfilment.OperationalStock(physical=D("5"), transit=D("2")),
			"B": fulfilment.OperationalStock(physical=D("100"), transit=D("0")),
		}

	def test_rows_carry_the_formula_and_sort_by_shortage(self):
		lines = [
			quotation_line(name="Q1", item_code="A", stock_qty=10),
			quotation_line(name="Q2", item_code="B", stock_qty=50),
		]

		rows = fulfilment.fulfilment_rows(
			"COMPANY", self.stock(), lines, {"A": D("4")}, today=TODAY,
			sales_orders_by_item={"A": {"SO-1"}},
		)

		self.assertEqual([row.item_code for row in rows], ["A", "B"])
		self.assertEqual(rows[0].physical, D("5"))
		self.assertEqual(rows[0].transit, D("2"))
		self.assertEqual(rows[0].quotation_demand, D("10"))
		self.assertEqual(rows[0].sales_order_demand, D("4"))
		self.assertEqual(rows[0].total_demand, D("14"))
		self.assertEqual(rows[0].shortage, D("7"))
		self.assertEqual(rows[0].related_quotations, ("QUO-1",))
		self.assertEqual(rows[0].related_sales_orders, ("SO-1",))
		self.assertEqual(rows[1].shortage, D("0"))

	def test_a_converted_quotation_is_not_double_counted(self):
		lines = [quotation_line(stock_qty=10, ordered_qty=10)]

		rows = fulfilment.fulfilment_rows(
			"COMPANY", {}, lines, {"ITEM": D("10")}, today=TODAY
		)

		self.assertEqual(rows[0].quotation_demand, D("0"))
		self.assertEqual(rows[0].total_demand, D("10"))
		self.assertEqual(rows[0].shortage, D("10"))

	def test_partial_conversion_splits_the_demand_once(self):
		lines = [quotation_line(stock_qty=10, ordered_qty=4)]

		rows = fulfilment.fulfilment_rows(
			"COMPANY", {}, lines, {"ITEM": D("4")}, today=TODAY
		)

		self.assertEqual(rows[0].total_demand, D("10"))

	def test_only_shortage_drops_covered_items(self):
		lines = [
			quotation_line(item_code="A", stock_qty=1),
			quotation_line(name="Q2", item_code="B", stock_qty=150),
		]

		rows = fulfilment.fulfilment_rows(
			"COMPANY", self.stock(), lines, {}, today=TODAY, only_shortage=True
		)

		self.assertEqual([row.item_code for row in rows], ["B"])

	def test_items_without_demand_or_stock_are_not_invented(self):
		rows = fulfilment.fulfilment_rows("COMPANY", self.stock(), [], {}, today=TODAY)

		self.assertEqual([row.item_code for row in rows], ["A", "B"])
		self.assertEqual([row.total_demand for row in rows], [D("0"), D("0")])

	def test_another_company_quotation_is_ignored(self):
		lines = [quotation_line(company="OTHER", stock_qty=99)]

		rows = fulfilment.fulfilment_rows("COMPANY", {}, lines, {}, today=TODAY)

		self.assertEqual(rows, [])

	def test_purchase_supply_is_a_column_and_never_enters_the_shortage(self):
		stock = {"A": fulfilment.OperationalStock(physical=D("4"))}

		rows = fulfilment.fulfilment_rows(
			"COMPANY",
			stock,
			[],
			{"A": D("10")},
			purchase_supply_by_item={"A": D("100"), "ON-ORDER": D("5")},
			today=TODAY,
		)

		by_code = {row.item_code: row for row in rows}
		self.assertEqual(by_code["A"].shortage, D("6"))
		self.assertEqual(by_code["A"].open_purchase_supply, D("100"))
		self.assertEqual(by_code["A"].suggested_purchase, D("0"))
		self.assertEqual(by_code["ON-ORDER"].shortage, D("0"))
		self.assertEqual(by_code["ON-ORDER"].suggested_purchase, D("0"))

	def test_the_suggestion_is_the_part_of_the_shortage_no_open_order_covers(self):
		rows = fulfilment.fulfilment_rows(
			"COMPANY",
			{},
			[quotation_line(item_code="A", stock_qty=10)],
			{},
			purchase_supply_by_item={"A": D("3")},
			today=TODAY,
		)

		self.assertEqual(rows[0].shortage, D("10"))
		self.assertEqual(rows[0].suggested_purchase, D("7"))

	def test_open_supply_netting_follows_the_site_policy(self):
		arguments = {"purchase_supply_by_item": {"A": D("3")}, "today": TODAY}
		lines = [quotation_line(item_code="A", stock_qty=10)]

		netted = fulfilment.fulfilment_rows("COMPANY", {}, lines, {}, **arguments)
		uncovered = fulfilment.fulfilment_rows(
			"COMPANY", {}, lines, {}, net_open_supply=False, **arguments
		)

		self.assertEqual(netted[0].suggested_purchase, D("7"))
		self.assertEqual(uncovered[0].suggested_purchase, D("10"))
		self.assertEqual(uncovered[0].open_purchase_supply, D("3"))
		self.assertEqual(uncovered[0].shortage, D("10"))


class TestHideZeroRows(UnitTestCase):
	"""«عدم نمایش اقلام صفر»: hide a row only when every operational column is zero."""

	def row(self, **values):
		base = {
			"company": "COMPANY",
			"item_code": "ITEM",
			"physical": D("0"),
			"transit": D("0"),
			"quotation_demand": D("0"),
			"sales_order_demand": D("0"),
			"total_demand": D("0"),
			"shortage": D("0"),
			"related_quotations": (),
			"related_sales_orders": (),
			"open_purchase_supply": D("0"),
			"suggested_purchase": D("0"),
		}
		base.update(values)
		return fulfilment.FulfilmentRow(**base)

	def test_a_row_is_empty_only_when_every_column_is_zero(self):
		self.assertTrue(fulfilment.is_empty_row(self.row()))
		self.assertFalse(fulfilment.is_empty_row(self.row(physical=D("0.000001"))))
		self.assertFalse(fulfilment.is_empty_row(self.row(transit=D("-1"))))
		self.assertFalse(fulfilment.is_empty_row(self.row(quotation_demand=D("2"))))
		self.assertFalse(fulfilment.is_empty_row(self.row(sales_order_demand=D("2"))))
		self.assertFalse(fulfilment.is_empty_row(self.row(shortage=D("1"))))
		self.assertFalse(fulfilment.is_empty_row(self.row(open_purchase_supply=D("2"))))
		self.assertFalse(fulfilment.is_empty_row(self.row(suggested_purchase=D("2"))))

	def test_off_keeps_every_row(self):
		stock = {
			"EMPTY": fulfilment.OperationalStock(),
			"NEGATIVE": fulfilment.OperationalStock(physical=D("-2")),
		}

		rows = fulfilment.fulfilment_rows("COMPANY", stock, [], {}, today=TODAY)

		self.assertEqual([row.item_code for row in rows], ["NEGATIVE", "EMPTY"])

	def test_on_drops_only_the_all_zero_rows(self):
		stock = {
			"EMPTY": fulfilment.OperationalStock(),
			"PHYSICAL": fulfilment.OperationalStock(physical=D("5")),
			"TRANSIT": fulfilment.OperationalStock(transit=D("3")),
			"NEGATIVE": fulfilment.OperationalStock(physical=D("-2")),
		}
		lines = [quotation_line(item_code="QUOTED", stock_qty=4)]

		rows = fulfilment.fulfilment_rows(
			"COMPANY",
			stock,
			lines,
			{"ORDERED": D("2")},
			today=TODAY,
			hide_zero=True,
		)

		self.assertEqual(
			[row.item_code for row in rows], ["QUOTED", "NEGATIVE", "ORDERED", "PHYSICAL", "TRANSIT"]
		)

	def test_on_keeps_a_row_whose_only_value_is_a_shortage(self):
		lines = [quotation_line(item_code="NO-STOCK", stock_qty=10)]

		rows = fulfilment.fulfilment_rows(
			"COMPANY", {}, lines, {}, today=TODAY, hide_zero=True
		)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].shortage, D("10"))

	def test_on_keeps_a_row_whose_only_value_is_open_purchase_supply(self):
		stock = {"EMPTY": fulfilment.OperationalStock(), "ON-ORDER": fulfilment.OperationalStock()}

		without_on = fulfilment.fulfilment_rows(
			"COMPANY", stock, [], {}, today=TODAY, hide_zero=True
		)
		with_on = fulfilment.fulfilment_rows(
			"COMPANY",
			stock,
			[],
			{},
			purchase_supply_by_item={"ON-ORDER": D("5")},
			today=TODAY,
			hide_zero=True,
		)

		self.assertEqual(without_on, [])
		self.assertEqual([row.item_code for row in with_on], ["ON-ORDER"])
		self.assertEqual(with_on[0].open_purchase_supply, D("5"))
		self.assertEqual(with_on[0].suggested_purchase, D("0"))
