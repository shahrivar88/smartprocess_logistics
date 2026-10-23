"""Pure tests for Sales Order demand, procurement and ready-to-invoice calculations."""

from decimal import Decimal

from smartprocess_logistics.core import procurement as planning
from smartprocess_logistics.core.procurement import (
	InvoiceLine,
	PurchaseOrderLine,
	SalesOrderLine,
)
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


def so_line(**overrides):
	values = {
		"name": "SOI-1",
		"sales_order": "SO-1",
		"company": "COMPANY",
		"item_code": "ITEM",
		"qty": 10,
		"transaction_date": "2026-01-01",
		"delivery_date": "2026-01-10",
	}
	values.update(overrides)
	return SalesOrderLine(**values)


def po_line(**overrides):
	values = {
		"name": "POI-1",
		"company": "COMPANY",
		"item_code": "ITEM",
		"qty": 100,
	}
	values.update(overrides)
	return PurchaseOrderLine(**values)


class TestStatuses(UnitTestCase):
	def test_open_sales_order_statuses(self):
		for status in ("To Deliver and Bill", "To Bill", "To Deliver", "To Pay"):
			with self.subTest(status=status):
				self.assertTrue(planning.is_open_sales_order(1, status))

	def test_closed_cancelled_stopped_and_on_hold_are_not_open(self):
		for status in ("Cancelled", "Closed", "Stopped", "On Hold", "  on hold "):
			with self.subTest(status=status):
				self.assertFalse(planning.is_open_sales_order(1, status))

	def test_draft_and_cancelled_documents_are_not_open(self):
		for docstatus in (0, 2, "x", None):
			with self.subTest(docstatus=docstatus):
				self.assertFalse(planning.is_open_sales_order(docstatus, "To Bill"))

	def test_open_purchase_order_statuses(self):
		self.assertTrue(planning.is_open_purchase_order(1, "To Receive and Bill"))
		for status in ("Cancelled", "Closed", "On Hold", "completed"):
			with self.subTest(status=status):
				self.assertFalse(planning.is_open_purchase_order(1, status))


class TestSalesOrderDemand(UnitTestCase):
	def test_open_delivery_demand_uses_stock_uom(self):
		self.assertEqual(
			planning.open_delivery_demand(so_line(qty=10, delivered_qty=4, conversion_factor=2)), D("12")
		)

	def test_delivered_quantity_is_net_of_returns(self):
		"""ERPNext recomputes delivered_qty from submitted delivery rows, including returns."""
		self.assertEqual(
			planning.open_delivery_demand(so_line(qty=10, delivered_qty=4, returned_qty=3)), D("6")
		)

	def test_closed_on_hold_draft_and_cancelled_lines_have_no_demand(self):
		for line in (
			so_line(status="Closed"),
			so_line(status="On Hold"),
			so_line(docstatus=0),
			so_line(docstatus=2),
		):
			with self.subTest(line=line):
				self.assertEqual(planning.open_delivery_demand(line), D("0"))

	def test_over_delivery_does_not_produce_negative_demand(self):
		self.assertEqual(planning.open_delivery_demand(so_line(qty=10, delivered_qty=12)), D("0"))

	def test_demand_is_filtered_by_company(self):
		lines = (
			so_line(name="A", company="COMPANY", qty=10),
			so_line(name="B", company="OTHER", qty=20),
		)

		self.assertEqual(planning.open_delivery_demand_total(lines, "COMPANY"), D("10"))

	def test_demand_can_be_grouped_by_item(self):
		lines = (
			so_line(name="A", item_code="ONE", qty=10),
			so_line(name="B", item_code="ONE", qty=5),
			so_line(name="C", item_code="TWO", qty=3),
		)

		self.assertEqual(
			planning.open_delivery_demand_by_item(lines, "COMPANY"),
			{"ONE": D("15"), "TWO": D("3")},
		)

	def test_invoice_billing_quantity_matches_the_native_formula(self):
		self.assertEqual(planning.billable_quantity(so_line(qty=10, returned_qty=2, delivered_qty=0)), D("8"))
		self.assertEqual(
			planning.billable_quantity(so_line(qty=10, returned_qty=12, delivered_qty=0)), D("0")
		)
		self.assertEqual(
			planning.billable_quantity(so_line(qty=10, returned_qty=0, delivered_qty=4)), D("10")
		)

	def test_remaining_uninvoiced_is_not_capped_by_delivery(self):
		self.assertEqual(
			planning.remaining_uninvoiced(so_line(qty=10, delivered_qty=0, invoiced_qty=4)), D("6")
		)

	def test_remaining_uninvoiced_honours_returns_and_partial_invoices(self):
		self.assertEqual(
			planning.remaining_uninvoiced(so_line(qty=10, returned_qty=2, invoiced_qty=4)),
			D("4"),
		)

	def test_remaining_uninvoiced_is_zero_when_over_invoiced(self):
		self.assertEqual(planning.remaining_uninvoiced(so_line(qty=10, invoiced_qty=12)), D("0"))

	def test_remaining_uninvoiced_is_in_stock_uom_when_asked(self):
		line = so_line(qty=10, invoiced_qty=4, conversion_factor=12)

		self.assertEqual(planning.remaining_uninvoiced_stock(line), D("72"))

	def test_remaining_uninvoiced_can_be_grouped_by_item(self):
		lines = (
			so_line(name="A", item_code="ONE", qty=10, invoiced_qty=4),
			so_line(name="B", item_code="ONE", qty=5, invoiced_qty=1),
			so_line(name="C", item_code="TWO", qty=3),
		)

		self.assertEqual(
			planning.remaining_uninvoiced_stock_by_item(lines, "COMPANY"),
			{"ONE": D("10"), "TWO": D("3")},
		)


class TestInvoiceLinks(UnitTestCase):
	def test_only_submitted_invoice_rows_are_summed(self):
		rows = (
			InvoiceLine("SOI-1", 4, docstatus=1),
			InvoiceLine("SOI-1", 2, docstatus=0),
			InvoiceLine("SOI-1", 8, docstatus=2),
			InvoiceLine("SOI-2", 3, docstatus=1),
			InvoiceLine("", 9, docstatus=1),
		)

		self.assertEqual(planning.invoiced_quantities_by_so_detail(rows), {"SOI-1": D("4"), "SOI-2": D("3")})

	def test_credit_notes_reduce_the_invoiced_quantity(self):
		rows = (InvoiceLine("SOI-1", 10), InvoiceLine("SOI-1", -2))

		self.assertEqual(planning.invoiced_quantities_by_so_detail(rows), {"SOI-1": D("8")})

	def test_a_credit_note_increases_the_remaining_quantity(self):
		self.assertEqual(planning.remaining_uninvoiced(so_line(qty=10, invoiced_qty=-2)), D("12"))

	def test_a_value_only_credit_note_does_not_create_demand(self):
		line = InvoiceLine("SOI-1", 0, is_return=True, amount=50)

		self.assertEqual(planning.credit_note_review(line), planning.CREDIT_NOTE_VALUE_ONLY)
		self.assertEqual(planning.invoiced_quantities_by_so_detail((line,)), {"SOI-1": D("0")})
		self.assertEqual(planning.remaining_uninvoiced(so_line(qty=10, invoiced_qty=0)), D("10"))

	def test_a_quantity_reversal_is_not_flagged_as_a_value_correction(self):
		line = InvoiceLine("SOI-1", -2, is_return=True, amount=-20)

		self.assertIsNone(planning.credit_note_review(line))
		self.assertEqual(planning.invoiced_quantities_by_so_detail((line,)), {"SOI-1": D("-2")})

	def test_negative_quantity_without_a_return_flag_is_flagged(self):
		line = InvoiceLine("SOI-1", -2, is_return=False, amount=-20)

		self.assertEqual(
			planning.credit_note_review(line),
			planning.CREDIT_NOTE_NEGATIVE_WITHOUT_RETURN,
		)

	def test_reviews_are_grouped_by_sales_order_item(self):
		rows = (
			InvoiceLine("SOI-1", 0, is_return=True, amount=50),
			InvoiceLine("SOI-1", -2, is_return=False, amount=-20),
			InvoiceLine("SOI-2", -2, is_return=True, amount=-20),
		)

		self.assertEqual(
			planning.credit_note_reviews_by_so_detail(rows),
			{
				"SOI-1": (
					planning.CREDIT_NOTE_NEGATIVE_WITHOUT_RETURN,
					planning.CREDIT_NOTE_VALUE_ONLY,
				),
			},
		)


class TestProcurement(UnitTestCase):
	def test_the_approved_virtual_coverage_example(self):
		self.assertEqual(planning.procurement_shortage(100, 0, 60), D("40"))

	def test_the_approved_physical_and_virtual_example(self):
		shortage = planning.procurement_shortage(100, 20, 30)

		self.assertEqual(shortage, D("50"))
		self.assertEqual(planning.recommended_purchase(shortage, 10), D("40"))

	def test_virtual_stock_can_be_switched_off(self):
		self.assertEqual(
			planning.procurement_shortage(100, 20, 30, virtual_stock_covers=False),
			D("80"),
		)

	def test_negative_physical_stock_increases_the_shortage(self):
		self.assertEqual(planning.procurement_shortage(100, -5, 0), D("105"))

	def test_negative_virtual_stock_never_increases_coverage(self):
		self.assertEqual(planning.procurement_shortage(100, 0, -10), D("100"))

	def test_recommended_purchase_can_remain_gross(self):
		self.assertEqual(planning.recommended_purchase(50, 10, net_open_supply=False), D("50"))

	def test_open_purchase_supply_uses_received_quantity_without_double_subtracting_returns(self):
		lines = (
			po_line(qty=100, received_qty=20, returned_qty=5, conversion_factor=2),
			po_line(name="POI-2", qty=50, received_qty=50),
			po_line(name="POI-3", qty=100, docstatus=2),
			po_line(name="POI-4", qty=100, status="On Hold"),
			po_line(name="POI-5", qty=100, company="OTHER"),
		)

		self.assertEqual(planning.outstanding_purchase_supply(lines, "COMPANY"), D("160"))
		self.assertEqual(
			planning.outstanding_purchase_supply_by_item(lines, "COMPANY"),
			{"ITEM": D("160")},
		)

	def test_open_po_supply_reduces_the_recommendation_without_double_counting(self):
		shortage = planning.procurement_shortage(100, 20, 30)
		supply = planning.outstanding_purchase_supply((po_line(qty=10, received_qty=0),), "COMPANY")

		self.assertEqual(shortage, D("50"))
		self.assertEqual(planning.recommended_purchase(shortage, supply), D("40"))


class TestPhysicalAllocation(UnitTestCase):
	def test_open_demand_is_allocated_once(self):
		lines = (
			so_line(name="A", sales_order="SO-A", transaction_date="2026-01-01"),
			so_line(name="B", sales_order="SO-B", transaction_date="2026-01-02"),
		)

		result = planning.allocate_physical_for_demand(
			lines,
			{"ITEM": 15},
			"COMPANY",
			strategy=planning.DEFAULT_ALLOCATION_STRATEGY,
		)

		self.assertEqual(result.allocations["A"], D("10"))
		self.assertEqual(result.allocations["B"], D("5"))


class TestReadyForInvoicing(UnitTestCase):
	def test_physical_stock_is_ready(self):
		row = planning.ready_for_invoicing([so_line()], {"ITEM": 10}, {}, "COMPANY")[0]

		self.assertEqual(row.remaining_uninvoiced_qty, D("10"))
		self.assertEqual(row.allocated_physical_qty, D("10"))
		self.assertEqual(row.allocated_virtual_qty, D("0"))
		self.assertEqual(row.suggested_invoice_qty, D("10"))
		self.assertEqual(row.readiness, planning.READY)

	def test_virtual_stock_is_incoming(self):
		row = planning.ready_for_invoicing([so_line()], {}, {"ITEM": 10}, "COMPANY")[0]

		self.assertEqual(row.allocated_physical_qty, D("0"))
		self.assertEqual(row.allocated_virtual_qty, D("10"))
		self.assertEqual(row.suggested_invoice_qty, D("0"))
		self.assertEqual(row.incoming_qty, D("10"))
		self.assertEqual(row.readiness, planning.INCOMING)

	def test_no_stock_is_waiting(self):
		row = planning.ready_for_invoicing([so_line()], {}, {}, "COMPANY")[0]

		self.assertEqual(row.suggested_invoice_qty, D("0"))
		self.assertEqual(row.incoming_qty, D("0"))
		self.assertEqual(row.readiness, planning.WAITING)

	def test_delivered_but_unbilled_is_ready_without_stock(self):
		row = planning.ready_for_invoicing(
			[so_line(qty=10, delivered_qty=3)],
			{},
			{},
			"COMPANY",
		)[0]

		self.assertEqual(row.remaining_uninvoiced_qty, D("10"))
		self.assertEqual(row.delivered_unbilled_qty, D("3"))
		self.assertEqual(row.suggested_invoice_qty, D("3"))
		self.assertEqual(row.incoming_qty, D("0"))
		self.assertEqual(row.readiness, planning.READY)

	def test_partial_invoicing_and_stock_are_both_visible(self):
		row = planning.ready_for_invoicing(
			[so_line(qty=10, invoiced_qty=4)],
			{"ITEM": 10},
			{},
			"COMPANY",
		)[0]

		self.assertEqual(row.invoiced_qty, D("4"))
		self.assertEqual(row.remaining_uninvoiced_qty, D("6"))
		self.assertEqual(row.allocated_physical_qty, D("6"))
		self.assertEqual(row.suggested_invoice_qty, D("6"))

	def test_returns_are_reflected_in_the_remaining_quantity(self):
		row = planning.ready_for_invoicing(
			[so_line(qty=10, returned_qty=2, invoiced_qty=0)],
			{"ITEM": 8},
			{},
			"COMPANY",
		)[0]

		self.assertEqual(row.remaining_uninvoiced_qty, D("8"))
		self.assertEqual(row.suggested_invoice_qty, D("8"))

	def test_physical_and_virtual_stock_are_allocated_separately(self):
		row = planning.ready_for_invoicing([so_line()], {"ITEM": 4}, {"ITEM": 6}, "COMPANY")[0]

		self.assertEqual(row.allocated_physical_qty, D("4"))
		self.assertEqual(row.allocated_virtual_qty, D("6"))
		self.assertEqual(row.suggested_invoice_qty, D("4"))
		self.assertEqual(row.incoming_qty, D("6"))
		self.assertEqual(row.readiness, planning.READY)

	def test_competing_orders_never_share_the_same_physical_stock(self):
		lines = (
			so_line(name="A", sales_order="SO-A", transaction_date="2026-01-01"),
			so_line(name="B", sales_order="SO-B", transaction_date="2026-01-02"),
		)

		rows = planning.ready_for_invoicing(lines, {"ITEM": 15}, {}, "COMPANY")

		self.assertEqual(rows[0].allocated_physical_qty, D("10"))
		self.assertEqual(rows[1].allocated_physical_qty, D("5"))
		self.assertEqual(rows[0].suggested_invoice_qty + rows[1].suggested_invoice_qty, D("15"))
		self.assertEqual(rows[0].incoming_qty + rows[1].incoming_qty, D("0"))

	def test_virtual_stock_is_allocated_only_after_physical_stock(self):
		lines = (
			so_line(name="A", sales_order="SO-A", transaction_date="2026-01-01"),
			so_line(name="B", sales_order="SO-B", transaction_date="2026-01-02"),
		)

		rows = planning.ready_for_invoicing(lines, {"ITEM": 10}, {"ITEM": 5}, "COMPANY")

		self.assertEqual(rows[0].allocated_physical_qty, D("10"))
		self.assertEqual(rows[0].allocated_virtual_qty, D("0"))
		self.assertEqual(rows[0].suggested_invoice_qty, D("10"))
		self.assertEqual(rows[0].readiness, planning.READY)
		self.assertEqual(rows[1].allocated_physical_qty, D("0"))
		self.assertEqual(rows[1].allocated_virtual_qty, D("5"))
		self.assertEqual(rows[1].suggested_invoice_qty, D("0"))
		self.assertEqual(rows[1].incoming_qty, D("5"))
		self.assertEqual(rows[1].readiness, planning.INCOMING)

	def test_uom_conversion_is_applied_to_stock_allocation(self):
		row = planning.ready_for_invoicing(
			[so_line(qty=2, conversion_factor=12)],
			{"ITEM": 10},
			{},
			"COMPANY",
		)[0]

		self.assertEqual(row.remaining_uninvoiced_qty, D("2"))
		self.assertEqual(row.remaining_uninvoiced_stock_qty, D("24"))
		self.assertEqual(row.allocated_physical_qty, D("10"))
		self.assertEqual(row.suggested_invoice_qty, D("0.833333"))

	def test_other_companies_are_not_mixed(self):
		lines = (
			so_line(name="A", company="COMPANY"),
			so_line(name="B", company="OTHER"),
		)

		rows = planning.ready_for_invoicing(lines, {"ITEM": 10}, {}, "COMPANY")

		self.assertEqual(tuple(row.key for row in rows), ("A",))

	def test_operational_readiness_does_not_invent_invoiced_quantities(self):
		lines = [so_line(qty=10, invoiced_qty=4)]

		rows = planning.operational_readiness(
			lines,
			{"ITEM": D("5")},
			{"ITEM": D("3")},
			"COMPANY",
		)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].ordered_qty, D("10"))
		self.assertEqual(rows[0].allocated_physical_qty, D("5"))
		self.assertEqual(rows[0].allocated_virtual_qty, D("3"))
		self.assertEqual(rows[0].incoming_qty, D("3"))
		self.assertEqual(rows[0].readiness, planning.READY)
		self.assertFalse(hasattr(rows[0], "invoiced_qty"))

	def test_closed_and_on_hold_lines_are_not_offered(self):
		lines = (
			so_line(name="A", status="Closed"),
			so_line(name="B", status="On Hold"),
			so_line(name="C", docstatus=0),
		)

		self.assertEqual(planning.ready_for_invoicing(lines, {"ITEM": 100}, {}, "COMPANY"), ())

	def test_ready_for_invoicing_does_not_mutate_stock(self):
		physical = {"ITEM": 10}
		virtual = {"ITEM": 5}

		planning.ready_for_invoicing([so_line()], physical, virtual, "COMPANY")

		self.assertEqual(physical, {"ITEM": 10})
		self.assertEqual(virtual, {"ITEM": 5})
