"""One Purchase Invoice Item line: what ERPNext received, what actually arrived, what is left.

The first two groups are the pure rules in `core/receipts.py`, which is where every decision
lives. The third group pins the report artifacts a site would otherwise discover only after a
migrate: the report document, its filters, the words it shows, and the fact that neither the
report module nor the loader reaches for `Bin`, for raw SQL, or for a second receipt definition.
"""

import json
import pathlib
import re

from smartprocess_logistics.core import receipts
from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.warehouse import policy

APP = pathlib.Path(__file__).resolve().parent.parent
REPORT_DIR = APP / "smartprocess_logistics" / "report" / "purchased_not_received"
TRANSLATIONS = APP / "translations" / "fa.csv"
REPORT_DATA = APP / "warehouse" / "report_data.py"

PHYSICAL = policy.PHYSICAL
TRANSIT = policy.TRANSIT
VIRTUAL = policy.VIRTUAL


def receipt(
	qty,
	*,
	rejected=0,
	received=None,
	uom="Nos",
	factor=1,
	classification=PHYSICAL,
	docstatus=1,
	is_return=False,
):
	"""One Purchase Receipt Item as ERPNext stores it: `received_qty = qty + rejected_qty`."""
	return receipts.ReceiptRow(
		qty=qty,
		rejected_qty=rejected,
		received_qty=qty + rejected if received is None else received,
		uom=uom,
		conversion_factor=factor,
		classification=classification,
		docstatus=docstatus,
		is_return=is_return,
	)


def line(
	purchased,
	*,
	received=0,
	uom="Nos",
	factor=1,
	is_return=False,
	update_stock=False,
	warehouse=PHYSICAL,
	rows=(),
	cancelled=0,
	draft=0,
):
	return receipts.InvoiceRow(
		purchased_qty=purchased,
		received_qty=received,
		uom=uom,
		conversion_factor=factor,
		is_return=is_return,
		update_stock=update_stock,
		warehouse_classification=warehouse,
		receipts=tuple(rows),
		cancelled_receipts=cancelled,
		draft_receipts=draft,
	)


class TestTheReceiptStatus(UnitTestCase):
	def test_a_line_with_no_receipt_is_not_received_and_owes_all_of_it(self):
		result = receipts.evaluate(line(10))

		self.assertEqual(result.status, receipts.NOT_RECEIVED)
		self.assertEqual(result.received, 0)
		self.assertEqual(result.outstanding, 10)

	def test_a_partial_receipt_leaves_the_rest_outstanding(self):
		result = receipts.evaluate(line(10, received=4, rows=[receipt(4)]))

		self.assertEqual(result.status, receipts.PARTIALLY_RECEIVED)
		self.assertEqual(result.received, 4)
		self.assertEqual(result.outstanding, 6)

	def test_several_receipts_that_add_up_are_received_in_full(self):
		result = receipts.evaluate(line(10, received=10, rows=[receipt(4), receipt(6)]))

		self.assertEqual(result.status, receipts.FULLY_RECEIVED)
		self.assertEqual(result.outstanding, 0)

	def test_more_than_purchased_is_over_received_and_never_negative(self):
		result = receipts.evaluate(line(10, received=12, rows=[receipt(12)]))

		self.assertEqual(result.status, receipts.OVER_RECEIVED)
		self.assertEqual(result.outstanding, 0)

	def test_a_quantity_that_rounds_to_the_purchased_amount_is_received_in_full(self):
		"""Stored quantities carry six places; a smaller difference is not a shortage."""
		result = receipts.evaluate(line("9.9999999", received="9.99999999"))

		self.assertEqual(result.status, receipts.FULLY_RECEIVED)
		self.assertEqual(result.outstanding, 0)

	def test_a_cancelled_receipt_receives_nothing(self):
		result = receipts.evaluate(line(10, rows=[receipt(4, docstatus=2)], cancelled=1))

		self.assertEqual(result.status, receipts.NOT_RECEIVED)
		self.assertEqual(result.physical, 0)
		self.assertIn(receipts.NOTE_CANCELLED, result.notes)

	def test_a_draft_receipt_receives_nothing(self):
		result = receipts.evaluate(line(10, rows=[receipt(4, docstatus=0)], draft=1))

		self.assertEqual(result.status, receipts.NOT_RECEIVED)
		self.assertIn(receipts.NOTE_DRAFT, result.notes)

	def test_a_return_invoice_is_decided_by_the_document_not_by_a_comparison(self):
		result = receipts.evaluate(line(10, is_return=True))

		self.assertEqual(result.status, receipts.PURCHASE_RETURN)
		self.assertEqual(result.outstanding, 0)

	def test_a_return_receipt_reduces_the_stored_receipt(self):
		rows = [receipt(10), receipt(-4, is_return=True)]
		result = receipts.evaluate(line(10, received=6, rows=rows))

		self.assertEqual(result.status, receipts.PARTIALLY_RECEIVED)
		self.assertEqual(result.received, 6)
		self.assertEqual(result.outstanding, 4)
		self.assertIn(receipts.NOTE_RETURNED, result.notes)


class TestThePhysicalArrival(UnitTestCase):
	"""The accounting receipt and the arrival on the floor are two different answers."""

	def test_a_physical_receipt_arrives(self):
		self.assertEqual(receipts.evaluate(line(10, received=4, rows=[receipt(4)])).physical, 4)

	def test_a_transit_receipt_is_received_and_has_not_arrived(self):
		result = receipts.evaluate(line(7, received=7, rows=[receipt(7, classification=TRANSIT)]))

		self.assertEqual(result.received, 7)
		self.assertEqual(result.physical, 0)
		self.assertIn(receipts.NOTE_TRANSIT, result.notes)
		self.assertIn(receipts.NOTE_NO_PHYSICAL, result.notes)

	def test_a_virtual_receipt_is_received_and_has_not_arrived(self):
		result = receipts.evaluate(line(7, received=7, rows=[receipt(7, classification=VIRTUAL)]))

		self.assertEqual(result.physical, 0)
		self.assertIn(receipts.NOTE_VIRTUAL, result.notes)
		self.assertNotIn(receipts.NOTE_TRANSIT, result.notes)

	def test_only_the_physical_part_of_a_mixed_receipt_arrives(self):
		result = receipts.evaluate(
			line(10, received=7, rows=[receipt(3), receipt(4, classification=TRANSIT)])
		)

		self.assertEqual(result.received, 7)
		self.assertEqual(result.physical, 3)
		self.assertIn(receipts.NOTE_TRANSIT, result.notes)
		self.assertNotIn(receipts.NOTE_NO_PHYSICAL, result.notes)

	def test_rejected_units_are_received_and_never_arrive(self):
		"""`received_qty` counts them; the target warehouse never gets them."""
		result = receipts.evaluate(line(10, received=12, rows=[receipt(10, rejected=2)]))

		self.assertEqual(result.status, receipts.OVER_RECEIVED)
		self.assertEqual(result.received, 12)
		self.assertEqual(result.physical, 10)
		self.assertIn(receipts.NOTE_REJECTED, result.notes)

	def test_a_warehouse_that_cannot_be_resolved_is_not_zero(self):
		result = receipts.evaluate(line(10, received=4, rows=[receipt(4, classification=None)]))

		self.assertEqual(result.received, 4)
		self.assertIsNone(result.physical)
		self.assertIn(receipts.NOTE_UNKNOWN_WAREHOUSE, result.notes)

	def test_a_receipt_row_in_another_unit_is_not_zero(self):
		result = receipts.evaluate(line(10, received=4, rows=[receipt(4, uom="Box")]))

		self.assertIsNone(result.physical)
		self.assertIn(receipts.NOTE_UOM_MISMATCH, result.notes)

	def test_a_blank_unit_is_a_mismatch_and_not_an_assumption(self):
		self.assertIsNone(receipts.evaluate(line(10, received=4, rows=[receipt(4, uom="")])).physical)

	def test_a_different_conversion_factor_is_a_mismatch(self):
		result = receipts.evaluate(line(10, received=4, rows=[receipt(4, factor=2)]))

		self.assertIsNone(result.physical)
		self.assertIn(receipts.NOTE_UOM_MISMATCH, result.notes)

	def test_a_stored_figure_that_disagrees_with_the_linked_rows_is_noted(self):
		result = receipts.evaluate(line(10, received=4, rows=[receipt(5)]))

		self.assertEqual(result.received, 4, "the status follows ERPNext's own figure")
		self.assertEqual(result.physical, 5)
		self.assertIn(receipts.NOTE_STORED_DISAGREES, result.notes)

	def test_an_invoice_that_updated_stock_receives_its_own_quantity(self):
		result = receipts.evaluate(line(5, update_stock=True))

		self.assertEqual(result.status, receipts.FULLY_RECEIVED)
		self.assertEqual(result.received, 5)
		self.assertEqual(result.physical, 5)
		self.assertIn(receipts.NOTE_INVOICE_STOCK, result.notes)

	def test_an_invoice_that_updated_stock_into_transit_has_not_arrived(self):
		result = receipts.evaluate(line(5, update_stock=True, warehouse=TRANSIT))

		self.assertEqual(result.received, 5)
		self.assertEqual(result.physical, 0)
		self.assertIn(receipts.NOTE_TRANSIT, result.notes)

	def test_an_invoice_that_updated_stock_without_a_resolvable_warehouse_is_not_zero(self):
		result = receipts.evaluate(line(5, update_stock=True, warehouse=None))

		self.assertIsNone(result.physical)
		self.assertIn(receipts.NOTE_UNKNOWN_WAREHOUSE, result.notes)

	def test_a_line_with_no_receipt_row_at_all_is_zero_and_not_unknown(self):
		self.assertEqual(receipts.evaluate(line(10)).physical, 0)

	def test_a_note_is_never_repeated_and_the_order_is_stable(self):
		result = receipts.evaluate(line(5, update_stock=True, warehouse=TRANSIT, cancelled=1, draft=1))

		self.assertEqual(result.notes, tuple(dict.fromkeys(result.notes)))
		self.assertEqual(result.notes[0], receipts.NOTE_INVOICE_STOCK)


class TestTheFilterAndTheWords(UnitTestCase):
	def test_outstanding_is_the_two_statuses_that_still_owe_goods(self):
		self.assertEqual(receipts.STATUS_FILTERS[receipts.FILTER_OUTSTANDING], receipts.OUTSTANDING)
		self.assertEqual(receipts.OUTSTANDING, (receipts.NOT_RECEIVED, receipts.PARTIALLY_RECEIVED))

	def test_every_status_is_reachable_from_a_filter_of_its_own(self):
		single = [
			statuses
			for code, statuses in receipts.STATUS_FILTERS.items()
			if code != receipts.FILTER_ALL and len(statuses) == 1
		]

		self.assertEqual([status for statuses in single for status in statuses], list(receipts.STATUSES))

	def test_all_is_every_status(self):
		self.assertEqual(receipts.STATUS_FILTERS[receipts.FILTER_ALL], receipts.STATUSES)

	def test_the_filter_codes_are_stable_and_not_the_persian_labels(self):
		for code in receipts.STATUS_FILTERS:
			with self.subTest(code=code):
				self.assertRegex(code, r"^[a-z_]+$")
				self.assertNotIn(code, receipts.STATUS_LABELS.values())

	def test_every_status_has_a_persian_label(self):
		self.assertEqual(set(receipts.STATUS_LABELS), set(receipts.STATUSES))
		for label in receipts.STATUS_LABELS.values():
			with self.subTest(label=label):
				self.assertTrue(any(ord(character) > 127 for character in label))

	def test_every_note_that_can_be_raised_has_a_sentence(self):
		declared = {
			value
			for name, value in vars(receipts).items()
			if name.startswith("NOTE_") and isinstance(value, str)
		}

		self.assertEqual(set(receipts.NOTE_LABELS), declared)


class TestTheReportArtifact(UnitTestCase):
	def document(self):
		return json.loads((REPORT_DIR / "purchased_not_received.json").read_text(encoding="utf-8"))

	def module_source(self):
		return (REPORT_DIR / "purchased_not_received.py").read_text(encoding="utf-8")

	def form_source(self):
		return (REPORT_DIR / "purchased_not_received.js").read_text(encoding="utf-8")

	def test_it_is_a_standard_script_report_over_the_purchase_invoice(self):
		document = self.document()

		self.assertEqual(document["name"], "Purchased Not Received")
		self.assertEqual(document["report_name"], document["name"])
		self.assertEqual(document["is_standard"], "Yes")
		self.assertEqual(document["report_type"], "Script Report")
		self.assertEqual(document["ref_doctype"], "Purchase Invoice")
		self.assertTrue(document["roles"])

	def test_the_columns_are_the_ones_the_report_was_asked_for(self):
		source = self.module_source()

		for fieldname in (
			"purchase_invoice",
			"posting_date",
			"supplier",
			"item_code",
			"item_name",
			"purchased_qty",
			"received_qty",
			"outstanding_qty",
			"uom",
			"warehouse",
			"currency",
			"receipt_status",
		):
			with self.subTest(fieldname=fieldname):
				self.assertIn(f'"fieldname": "{fieldname}"', source)

	def test_the_invoice_number_is_a_link_so_it_opens(self):
		source = self.module_source()
		column = re.search(r'"fieldname": "purchase_invoice".*?\}', source, re.DOTALL)

		self.assertIsNotNone(column)
		self.assertIn('"fieldtype": "Link"', column.group(0))
		self.assertIn('"options": "Purchase Invoice"', column.group(0))

	def test_the_report_reads_through_the_permission_aware_loader(self):
		source = self.module_source()

		self.assertIn("report_data.require_company(filters)", source)
		self.assertIn("report_data.purchased_not_received(company, filters)", source)

	def test_the_persian_words_come_from_one_place(self):
		source = self.module_source()

		self.assertIn("receipts.STATUS_LABELS", source)
		self.assertIn("receipts.NOTE_LABELS", source)

	def test_nothing_is_derived_from_a_bin_balance(self):
		"""The receipt loader family starts at `_receipt_statuses`; none of it may name `Bin`."""
		loader = re.search(r"def _receipt_statuses\(.*", REPORT_DATA.read_text(encoding="utf-8"), re.DOTALL)

		self.assertIsNotNone(loader)
		self.assertNotIn('"Bin"', loader.group(0))
		self.assertNotIn('"actual_qty"', loader.group(0))

	def test_no_part_of_the_report_skips_permissions_or_writes_sql(self):
		for source in (self.module_source(), REPORT_DATA.read_text(encoding="utf-8")):
			with self.subTest():
				self.assertNotIn("ignore_permissions", source)
				self.assertNotIn("frappe.db.sql", source)

	def test_the_form_offers_exactly_the_eight_filters(self):
		fieldnames = re.findall(r'fieldname: "([a-z_]+)"', self.form_source())

		self.assertEqual(
			fieldnames,
			[
				"company",
				"from_date",
				"to_date",
				"supplier",
				"purchase_invoice",
				"item",
				"warehouse",
				"receipt_status",
			],
		)

	def test_the_form_opens_on_the_rows_that_still_owe_goods(self):
		self.assertIn('default: "outstanding"', self.form_source())

	def test_the_form_offers_every_status_the_server_accepts(self):
		offered = set(re.findall(r'value: "([a-z_]+)"', self.form_source()))

		self.assertEqual(offered, set(receipts.STATUS_FILTERS))

	def test_cannot_be_determined_is_shown_instead_of_zero(self):
		source = self.form_source()

		self.assertIn("physical_qty === null", source)
		self.assertIn("قابل تعیین نیست", source)

	def test_the_title_is_translated(self):
		self.assertIn(
			"Purchased Not Received,کالاهای خریداری‌شده و دریافت‌نشده",
			TRANSLATIONS.read_text(encoding="utf-8"),
		)
