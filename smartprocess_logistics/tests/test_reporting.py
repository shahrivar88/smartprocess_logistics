"""Pure tests for the report row shaping built on the Stage B calculations."""

from decimal import Decimal
from types import SimpleNamespace

from smartprocess_logistics.core import reporting
from smartprocess_logistics.core.inventory import ItemStock
from smartprocess_logistics.core.measurements import CURRENT, HISTORICAL, Measurement
from smartprocess_logistics.core.procurement import SalesOrderLine
from smartprocess_logistics.tests.base import UnitTestCase

D = Decimal


def so(name="SOI-1", so_name="SO-1", item="ITEM", qty=10, date="2026-01-01"):
	return SalesOrderLine(
		name=name,
		sales_order=so_name,
		company="COMPANY",
		item_code=item,
		qty=qty,
		transaction_date=date,
		delivery_date="2026-01-10",
	)


class TestWarehouseInventoryRows(UnitTestCase):
	def test_physical_and_virtual_are_classified(self):
		rows = (
			SimpleNamespace(
				company="COMPANY",
				item_code="ITEM",
				warehouse="P",
				warehouse_type=None,
				actual_qty=10,
				stock_uom="Nos",
				stock_value=100,
				valuation_rate=10,
			),
			SimpleNamespace(
				company="COMPANY",
				item_code="ITEM",
				warehouse="V",
				warehouse_type="Virtual",
				actual_qty=4,
				stock_uom="Nos",
				stock_value=40,
				valuation_rate=10,
			),
		)
		items = {"ITEM": {"item_name": "Lamp", "item_group": "Products", "stock_uom": "Nos"}}

		result = reporting.warehouse_inventory_rows(rows, items)

		self.assertEqual([row["classification"] for row in result], ["physical", "virtual"])
		self.assertEqual([row["actual_qty"] for row in result], [D("10"), D("4")])
		self.assertEqual(result[0]["item_name"], "Lamp")


class TestDashboardRows(UnitTestCase):
	def test_dashboard_separates_physical_transit_and_virtual(self):
		rows = reporting.inventory_dashboard_rows(
			"COMPANY",
			{"ITEM": ItemStock(physical=D("20"), virtual=D("30"))},
			[so(qty=100)],
			{"ITEM": D("10")},
			transit_by_item={"ITEM": D("30")},
		)

		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["physical_stock"], D("20"))
		self.assertEqual(row["transit_stock"], D("30"))
		self.assertEqual(row["virtual_stock"], D("0"))
		self.assertEqual(row["total_stock"], D("50"))
		self.assertEqual(row["open_demand"], D("100"))
		self.assertEqual(row["physically_deliverable"], D("20"))
		self.assertEqual(row["available_for_new_sales"], D("0"))
		self.assertEqual(row["immediate_shortage"], D("80"))
		self.assertEqual(row["procurement_shortage"], D("50"))
		self.assertEqual(row["open_purchase_supply"], D("10"))
		self.assertEqual(row["recommended_purchase"], D("40"))

	def test_virtual_stock_never_covers_the_operational_shortage(self):
		rows = reporting.inventory_dashboard_rows(
			"COMPANY",
			{"ITEM": ItemStock(physical=D("20"), virtual=D("30"))},
			[so(qty=100)],
			{"ITEM": D("10")},
		)

		self.assertEqual(rows[0]["transit_stock"], D("0"))
		self.assertEqual(rows[0]["virtual_stock"], D("30"))
		self.assertEqual(rows[0]["procurement_shortage"], D("80"))
		self.assertEqual(rows[0]["recommended_purchase"], D("70"))

	def test_dashboard_does_not_give_the_same_stock_to_two_orders(self):
		lines = (
			so(name="A", so_name="SO-A", qty=10, date="2026-01-01"),
			so(name="B", so_name="SO-B", qty=10, date="2026-01-02"),
		)

		rows = reporting.inventory_dashboard_rows(
			"COMPANY",
			{"ITEM": ItemStock(physical=D("15"))},
			lines,
			{},
		)

		self.assertEqual(rows[0]["available_for_new_sales"], D("0"))
		self.assertEqual(rows[0]["open_demand"], D("20"))

	def test_procurement_shortage_rows_keep_source_orders_and_suggested_purchase(self):
		lines = (so(name="A", so_name="SO-A", qty=100),)

		rows = reporting.procurement_shortage_rows(
			"COMPANY",
			{"ITEM": ItemStock(physical=D("20"), virtual=D("30"))},
			lines,
			{"ITEM": D("10")},
			transit_by_item={"ITEM": D("30")},
			related_orders={"ITEM": ("SO-A",)},
		)

		self.assertEqual(rows[0]["related_orders"], "SO-A")
		self.assertEqual(rows[0]["transit_stock"], D("30"))
		self.assertEqual(rows[0]["suggested_purchase"], D("40"))

	def test_procurement_shortage_ignores_virtual_stock(self):
		rows = reporting.procurement_shortage_rows(
			"COMPANY",
			{"ITEM": ItemStock(physical=D("20"), virtual=D("30"))},
			(so(name="A", so_name="SO-A", qty=100),),
			{"ITEM": D("10")},
		)

		self.assertEqual(rows[0]["virtual_stock"], D("30"))
		self.assertEqual(rows[0]["procurement_shortage"], D("80"))
		self.assertEqual(rows[0]["suggested_purchase"], D("70"))


class TestReadyInvoicingRows(UnitTestCase):
	def test_physical_and_virtual_recommendations_are_separate(self):
		lines = [so()]
		from smartprocess_logistics.core import procurement

		readiness = procurement.ready_for_invoicing(
			lines,
			{"ITEM": 4},
			{"ITEM": 6},
			"COMPANY",
		)
		rows = reporting.ready_invoicing_rows(
			readiness,
			{"SOI-1": {"customer": "CUST", "order_date": "2026-01-01", "age_days": 3}},
			{},
		)

		self.assertEqual(rows[0]["suggested_invoice_qty"], D("4"))
		self.assertEqual(rows[0]["incoming_qty"], D("6"))
		self.assertEqual(rows[0]["customer"], "CUST")

	def test_review_text_is_carried_without_changing_quantities(self):
		from smartprocess_logistics.core import procurement

		readiness = procurement.ready_for_invoicing([so()], {"ITEM": 10}, {}, "COMPANY")
		rows = reporting.ready_invoicing_rows(
			readiness,
			{"SOI-1": {"customer": "CUST"}},
			{"SOI-1": ("value_only_credit_note",)},
		)

		self.assertEqual(rows[0]["suggested_invoice_qty"], D("10"))
		self.assertEqual(rows[0]["review"], "value_only_credit_note")
		self.assertTrue(rows[0]["invoiced_qty_available"])

	def test_operational_rows_mark_invoice_fields_unavailable(self):
		from smartprocess_logistics.core import procurement

		readiness = procurement.operational_readiness(
			[so()],
			{"ITEM": D("4")},
			{"ITEM": D("6")},
			"COMPANY",
		)
		rows = reporting.operational_ready_invoicing_rows(
			readiness,
			{"SOI-1": {"customer": "CUST"}},
			"invoice unavailable",
		)

		self.assertIsNone(rows[0]["invoiced_qty"])
		self.assertIsNone(rows[0]["remaining_uninvoiced_qty"])
		self.assertIsNone(rows[0]["suggested_invoice_qty"])
		self.assertFalse(rows[0]["invoiced_qty_available"])
		self.assertEqual(rows[0]["billing_note"], "invoice unavailable")
		self.assertEqual(rows[0]["allocated_physical_qty"], D("4"))
		self.assertEqual(rows[0]["incoming_qty"], D("6"))


class TestWeightVolumeRows(UnitTestCase):
	def test_native_net_weight_is_used_and_missing_gross_is_explicit(self):
		row = reporting.weight_volume_row(4, native_net_weight=10, native_weight_uom="Kg")

		self.assertEqual(row["net_weight"], D("10"))
		self.assertIsNone(row["gross_weight"])
		self.assertIsNone(row["volume_cm3"])
		self.assertEqual(row["net_source"], "native_snapshot")
		self.assertEqual(row["gross_source"], "unavailable")
		self.assertEqual(row["volume_source"], "unavailable")

	def test_current_and_historical_sources_are_not_mixed(self):
		current = Measurement(
			weight_per_unit=2,
			length_cm=1,
			width_cm=2,
			height_cm=3,
			source=CURRENT,
		)
		historical = Measurement(
			weight_per_unit=4,
			length_cm=1,
			width_cm=1,
			height_cm=1,
			source=HISTORICAL,
		)

		current_row = reporting.weight_volume_row(
			2,
			measurement=current,
			gross_available=True,
			volume_available=True,
		)
		historical_row = reporting.weight_volume_row(
			2,
			measurement=historical,
			gross_available=True,
			volume_available=True,
		)

		self.assertEqual(current_row["net_source"], CURRENT)
		self.assertEqual(current_row["gross_source"], CURRENT)
		self.assertEqual(historical_row["net_source"], HISTORICAL)
		self.assertEqual(historical_row["volume_source"], HISTORICAL)

	def test_a_missing_net_weight_stays_missing_when_the_caller_says_so(self):
		measurement = Measurement(weight_per_unit=2, gross_weight=3, source=HISTORICAL)

		row = reporting.weight_volume_row(
			4,
			measurement=measurement,
			net_available=False,
			gross_available=True,
		)

		self.assertIsNone(row["net_weight"])
		self.assertEqual(row["net_source"], "unavailable")
		self.assertEqual(row["gross_weight"], D("12"))

	def test_missing_gross_and_dimensions_stay_missing_even_when_flags_are_true(self):
		measurement = Measurement(
			weight_per_unit=2,
			gross_weight=None,
			length_cm=None,
			width_cm=1,
			height_cm=1,
			source=HISTORICAL,
		)

		row = reporting.weight_volume_row(
			2,
			measurement=measurement,
			gross_available=True,
			volume_available=True,
		)

		self.assertIsNone(row["gross_weight"])
		self.assertIsNone(row["volume_cm3"])
		self.assertIsNone(row["volume_m3"])
		self.assertEqual(row["gross_source"], "unavailable")
		self.assertEqual(row["volume_source"], "unavailable")

	def test_explicit_zero_gross_and_dimensions_remain_zero(self):
		measurement = Measurement(
			weight_per_unit=0,
			gross_weight=0,
			length_cm=0,
			width_cm=0,
			height_cm=0,
			source=HISTORICAL,
		)

		row = reporting.weight_volume_row(
			2,
			measurement=measurement,
			gross_available=True,
			volume_available=True,
		)

		self.assertEqual(row["gross_weight"], D("0"))
		self.assertEqual(row["volume_cm3"], D("0"))
		self.assertEqual(row["volume_m3"], D("0"))
		self.assertEqual(row["gross_source"], HISTORICAL)
		self.assertEqual(row["volume_source"], HISTORICAL)
