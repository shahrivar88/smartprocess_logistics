"""The validation-only guards, tested without a site.

The decisions live in `core/integrity.py`, which imports no Frappe, so every rule below is a
plain function call. The handler module is checked for the two properties that make it safe:
it is registered on the four documents, and it neither calls `ignore_permissions` nor writes
through SQL.
"""

import ast
import pathlib

from smartprocess_logistics.core import integrity
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]


def codes(problems):
	return [problem.code for problem in problems]


class TestSalesInvoiceScope(UnitTestCase):
	def test_managed_draft_with_update_stock_is_refused(self):
		problems = integrity.sales_invoice_problems(update_stock=1, docstatus=0, managed=True)

		self.assertEqual(codes(problems), ["SI_UPDATE_STOCK"])
		self.assertIn("یادداشت تحویل", problems[0].message)

	def test_managed_draft_without_update_stock_is_allowed(self):
		self.assertEqual(integrity.sales_invoice_problems(0, 0, True), ())

	def test_unmanaged_invoice_keeps_the_standard_workflow(self):
		self.assertEqual(integrity.sales_invoice_problems(1, 0, False), ())

	def test_a_submitted_historical_invoice_is_not_inspected(self):
		self.assertEqual(integrity.sales_invoice_problems(1, 1, True), ())


def pi_row(**overrides):
	row = {
		"idx": 1,
		"item_code": "ITEM",
		"is_stock_item": True,
		"is_fixed_asset": False,
		"qty": 1,
	}
	row.update(overrides)
	return row


class TestPurchaseInvoiceScope(UnitTestCase):
	def test_a_stock_invoice_with_update_stock_is_refused(self):
		problems = integrity.purchase_invoice_problems(1, 0, [pi_row()])

		self.assertEqual(codes(problems), ["PI_UPDATE_STOCK"])
		self.assertIn("داخلی و خارجی", problems[0].message)

	def test_a_stock_invoice_without_update_stock_is_allowed(self):
		self.assertEqual(integrity.purchase_invoice_problems(0, 0, [pi_row()]), ())

	def test_a_service_or_non_stock_invoice_is_left_alone(self):
		self.assertEqual(
			integrity.purchase_invoice_problems(1, 0, [pi_row(is_stock_item=False)]),
			(),
		)

	def test_a_fixed_asset_row_is_left_alone(self):
		self.assertEqual(
			integrity.purchase_invoice_problems(1, 0, [pi_row(is_fixed_asset=True)]),
			(),
		)

	def test_a_zero_quantity_stock_row_is_left_alone(self):
		self.assertEqual(integrity.purchase_invoice_problems(1, 0, [pi_row(qty=0)]), ())

	def test_a_financial_only_return_is_allowed(self):
		self.assertEqual(
			integrity.purchase_invoice_problems(0, 0, [pi_row(qty=-1)], is_return=True),
			(),
		)

	def test_a_stock_affecting_return_is_refused(self):
		problems = integrity.purchase_invoice_problems(1, 0, [pi_row(qty=-1)], is_return=True)

		self.assertEqual(codes(problems), ["PI_UPDATE_STOCK"])
		self.assertIn("رسید خرید برگشتی", problems[0].message)

	def test_a_service_return_with_stock_update_is_allowed(self):
		self.assertEqual(
			integrity.purchase_invoice_problems(1, 0, [pi_row(qty=-1, is_stock_item=False)], is_return=True),
			(),
		)

	def test_an_opening_entry_is_left_alone(self):
		self.assertEqual(
			integrity.purchase_invoice_problems(1, 0, [pi_row()], is_opening=True),
			(),
		)

	def test_an_internal_transfer_is_left_alone(self):
		self.assertEqual(
			integrity.purchase_invoice_problems(1, 0, [pi_row()], is_internal_transfer=True),
			(),
		)

	def test_a_historical_submitted_invoice_is_not_inspected(self):
		self.assertEqual(integrity.purchase_invoice_problems(1, 1, [pi_row()]), ())

	def test_the_first_actual_stock_row_is_named(self):
		problems = integrity.purchase_invoice_problems(
			1,
			0,
			[pi_row(idx=1, is_stock_item=False), pi_row(idx=2)],
		)

		self.assertEqual(codes(problems), ["PI_UPDATE_STOCK"])
		self.assertIn("ردیف 2", problems[0].message)


def dn_row(**overrides):
	row = {
		"idx": 1,
		"item_code": "ITEM",
		"si_detail": "SI-ITEM",
		"against_sales_invoice": "SI",
		"si_item_parent": "SI",
		"si_item_code": "ITEM",
		"si_docstatus": 1,
		"si_company": "COMPANY",
		"si_update_stock": 0,
		"si_managed": True,
		"commitment_state": "active",
		"committed_warehouse": "COMMITTED",
		"is_managed": True,
		"is_stock_item": False,
		"warehouse": "STORES",
		"warehouse_company": "COMPANY",
		"target_warehouse_company": None,
	}
	row.update(overrides)
	return row


class TestApprovedDeliveryNoteScope(UnitTestCase):
	"""I30: the managed Delivery Note rule that does not read a commitment."""

	def test_a_correct_delivery_row_is_allowed(self):
		self.assertEqual(
			integrity.delivery_note_problems("COMPANY", False, [dn_row()], managed=True),
			(),
		)

	def test_si_detail_must_belong_to_the_referenced_invoice(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(si_item_parent="OTHER-SI")], managed=True
		)

		self.assertEqual(codes(problems), ["DN_CROSS_INVOICE"])

	def test_item_code_must_match_the_invoice_row(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(si_item_code="OTHER-ITEM")], managed=True
		)

		self.assertEqual(codes(problems), ["DN_ITEM_MISMATCH"])

	def test_a_reference_without_a_detail_is_refused(self):
		problems = integrity.delivery_note_problems("COMPANY", False, [dn_row(si_detail=None)], managed=True)

		self.assertEqual(codes(problems), ["DN_DETAIL_MISSING"])

	def test_a_detail_without_a_reference_is_refused(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(against_sales_invoice=None)], managed=True
		)

		self.assertEqual(codes(problems), ["DN_INVOICE_MISSING"])

	def test_a_detail_that_does_not_exist_is_refused(self):
		problems = integrity.delivery_note_problems(
			"COMPANY",
			False,
			[dn_row(si_item_parent=None, si_item_code=None, si_docstatus=None)],
			managed=True,
		)

		self.assertEqual(codes(problems), ["DN_DETAIL_NOT_FOUND"])

	def test_a_cancelled_or_draft_invoice_is_refused(self):
		problems = integrity.delivery_note_problems("COMPANY", False, [dn_row(si_docstatus=2)], managed=True)

		self.assertEqual(codes(problems), ["DN_INVOICE_NOT_SUBMITTED"])

	def test_the_invoice_and_delivery_note_must_share_a_company(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(si_company="OTHER-COMPANY")], managed=True
		)

		self.assertEqual(codes(problems), ["DN_COMPANY_MISMATCH"])

	def test_the_warehouse_must_belong_to_the_company(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(warehouse_company="OTHER-COMPANY")], managed=True
		)

		self.assertEqual(codes(problems), ["DN_WAREHOUSE_COMPANY"])

	def test_the_target_warehouse_must_belong_to_the_company(self):
		problems = integrity.delivery_note_problems(
			"COMPANY",
			False,
			[dn_row(target_warehouse_company="OTHER-COMPANY")],
			managed=True,
		)

		self.assertEqual(codes(problems), ["DN_TARGET_WAREHOUSE_COMPANY"])

	def test_a_managed_invoice_that_already_moved_stock_is_refused(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(si_update_stock=1)], managed=True
		)

		self.assertEqual(codes(problems), ["DN_SECOND_DEDUCTION"])

	def test_a_return_is_not_refused_as_a_second_deduction(self):
		self.assertEqual(
			integrity.delivery_note_problems("COMPANY", True, [dn_row(si_update_stock=1)], managed=True),
			(),
		)

	def test_a_non_managed_document_keeps_the_standard_workflow(self):
		self.assertEqual(
			integrity.delivery_note_problems(
				"COMPANY", False, [dn_row(si_item_parent="OTHER-SI")], managed=False
			),
			(),
		)

	def test_an_independent_managed_row_is_refused(self):
		problems = integrity.delivery_note_problems(
			"COMPANY",
			False,
			[dn_row(si_detail=None, against_sales_invoice=None)],
			managed=True,
		)

		self.assertEqual(codes(problems), ["DN_NOT_LINKED"])
		self.assertIn("ردیف فاکتور فروش", problems[0].message)

	def test_a_stock_row_without_a_source_warehouse_is_refused(self):
		problems = integrity.delivery_note_problems(
			"COMPANY", False, [dn_row(is_stock_item=True, warehouse=None)], managed=True
		)

		self.assertEqual(codes(problems), ["DN_SOURCE_WAREHOUSE_MISSING"])

	def test_commitment_fields_are_not_read_by_the_approved_rule(self):
		"""I30 is independent: an active commitment does not make this rule require its target."""
		self.assertEqual(
			integrity.delivery_note_problems(
				"COMPANY",
				False,
				[dn_row(is_stock_item=True, warehouse="STORES", commitment_state="active")],
				managed=True,
			),
			(),
		)

	def test_a_return_keeps_the_standard_delivery_workflow(self):
		self.assertEqual(
			integrity.delivery_note_problems(
				"COMPANY",
				True,
				[dn_row(si_detail=None, against_sales_invoice=None, is_stock_item=True)],
				managed=True,
			),
			(),
		)


def pr_header(**overrides):
	header = {
		"company": "COMPANY",
		"currency": "USD",
		"supplier": "SUPPLIER",
		"conversion_rate": 500000,
	}
	header.update(overrides)
	return header


def pr_row(**overrides):
	row = {
		"idx": 1,
		"item_code": "ITEM",
		"is_stock_item": True,
		"purchase_invoice": "PI",
		"purchase_invoice_item": "PI-ITEM",
		"rate": 10,
		"conversion_factor": 1,
		"uom": "Nos",
		"pi_item_parent": "PI",
		"pi_item_code": "ITEM",
		"pi_item_rate": 10,
		"pi_item_conversion_factor": 1,
		"pi_item_uom": "Nos",
		"pi_company": "COMPANY",
		"pi_currency": "USD",
		"pi_supplier": "SUPPLIER",
		"pi_conversion_rate": 500000,
		"pi_update_stock": 0,
		"pi_docstatus": 1,
	}
	row.update(overrides)
	return row


class TestPurchaseReceiptScope(UnitTestCase):
	def test_a_preserved_invoice_rate_is_allowed(self):
		self.assertEqual(integrity.purchase_receipt_problems(pr_header(), [pr_row()]), ())

	def test_a_market_rate_on_the_receipt_is_refused(self):
		problems = integrity.purchase_receipt_problems(pr_header(conversion_rate=520000), [pr_row()])

		self.assertIn("PR_RATE_MISMATCH", codes(problems))

	def test_the_invoice_row_must_belong_to_the_invoice(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(pi_item_parent="OTHER-PI")])

		self.assertEqual(codes(problems), ["PR_CROSS_INVOICE"])

	def test_the_item_code_must_match(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(pi_item_code="OTHER-ITEM")])

		self.assertEqual(codes(problems), ["PR_ITEM_MISMATCH"])

	def test_a_missing_invoice_row_is_refused(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(purchase_invoice_item=None)])

		self.assertEqual(codes(problems), ["PR_MISSING_PI_ITEM"])

	def test_an_invoice_that_already_moved_stock_blocks_a_receipt(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(pi_update_stock=1)])

		self.assertIn("PR_PI_UPDATED_STOCK", codes(problems))

	def test_a_return_may_reverse_a_historical_stock_effecting_invoice(self):
		problems = integrity.purchase_receipt_problems(
			pr_header(),
			[pr_row(pi_update_stock=1)],
			is_return=True,
		)

		self.assertEqual(problems, ())

	def test_company_currency_and_supplier_must_match(self):
		for override, code in (
			({"pi_company": "OTHER"}, "PR_COMPANY_MISMATCH"),
			({"pi_currency": "EUR"}, "PR_CURRENCY_MISMATCH"),
			({"pi_supplier": "OTHER"}, "PR_SUPPLIER_MISMATCH"),
			({"pi_docstatus": 2}, "PR_PI_NOT_SUBMITTED"),
		):
			with self.subTest(code=code):
				self.assertIn(
					code,
					codes(integrity.purchase_receipt_problems(pr_header(), [pr_row(**override)])),
				)

	def test_a_different_item_rate_is_refused(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(rate=11)])

		self.assertIn("PR_VALUE_MISMATCH", codes(problems))

	def test_a_different_uom_is_refused(self):
		row = pr_row(rate=120, conversion_factor=12, uom="Box")
		row["pi_item_conversion_factor"] = 1

		problems = integrity.purchase_receipt_problems(pr_header(), [row])

		self.assertIn("PR_UOM_MISMATCH", codes(problems))

	def test_a_different_conversion_factor_is_refused(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(conversion_factor=2)])

		self.assertIn("PR_UOM_MISMATCH", codes(problems))

	def test_a_zero_conversion_factor_is_refused(self):
		problems = integrity.purchase_receipt_problems(pr_header(), [pr_row(conversion_factor=0)])

		self.assertEqual(codes(problems), ["PR_UOM_INVALID"])

	def test_a_non_stock_receipt_row_is_left_to_standard(self):
		self.assertEqual(
			integrity.purchase_receipt_problems(
				pr_header(conversion_rate=520000),
				[pr_row(is_stock_item=False)],
			),
			(),
		)


class TestPurchaseReceiptRateSet(UnitTestCase):
	def test_multiple_invoices_with_the_same_rate_are_allowed(self):
		self.assertEqual(
			integrity.purchase_receipt_rate_set_problems(
				[
					{"currency": "USD", "conversion_rate": 500000},
					{"currency": "USD", "conversion_rate": 500000},
				]
			),
			(),
		)

	def test_multiple_invoices_with_different_rates_are_refused(self):
		problems = integrity.purchase_receipt_rate_set_problems(
			[
				{"currency": "USD", "conversion_rate": 500000},
				{"currency": "USD", "conversion_rate": 520000},
			]
		)

		self.assertEqual(codes(problems), ["PR_MIXED_RATES"])

	def test_multiple_currencies_are_refused(self):
		problems = integrity.purchase_receipt_rate_set_problems(
			[
				{"currency": "USD", "conversion_rate": 500000},
				{"currency": "EUR", "conversion_rate": 500000},
			]
		)

		self.assertEqual(codes(problems), ["PR_MIXED_RATES"])


class TestPurchaseReturnLinks(UnitTestCase):
	def original(self, **overrides):
		row = {
			"name": "PR-ITEM",
			"idx": 1,
			"item_code": "ITEM",
			"purchase_invoice": "PI",
			"purchase_invoice_item": "PI-ITEM",
		}
		row.update(overrides)
		return row

	def return_row(self, **overrides):
		row = {
			"idx": 1,
			"item_code": "ITEM",
			"purchase_invoice": "PI",
			"purchase_invoice_item": None,
			"purchase_receipt_item": None,
		}
		row.update(overrides)
		return row

	def test_a_missing_link_is_filled_from_the_original_row(self):
		plans, problems = integrity.purchase_return_link_plan([self.return_row()], [self.original()], {})

		self.assertEqual(problems, ())
		self.assertEqual(
			plans,
			[{"idx": 1, "purchase_invoice": "PI", "purchase_invoice_item": "PI-ITEM"}],
		)

	def test_a_named_original_row_wins(self):
		original = [
			self.original(name="PR-1", idx=1, purchase_invoice_item="PI-1"),
			self.original(name="PR-2", idx=2, purchase_invoice_item="PI-2"),
		]
		plans, problems = integrity.purchase_return_link_plan(
			[self.return_row(purchase_receipt_item="PR-2")], original, {}
		)

		self.assertEqual(problems, ())
		self.assertEqual(plans[0]["purchase_invoice_item"], "PI-2")

	def test_the_row_position_breaks_a_tie(self):
		original = [
			self.original(name="PR-1", idx=1, purchase_invoice_item="PI-1"),
			self.original(name="PR-2", idx=2, purchase_invoice_item="PI-2"),
		]
		plans, problems = integrity.purchase_return_link_plan([self.return_row(idx=2)], original, {})

		self.assertEqual(problems, ())
		self.assertEqual(plans[0]["purchase_invoice_item"], "PI-2")

	def test_an_ambiguity_is_refused(self):
		original = [
			self.original(name="PR-1", idx=1, purchase_invoice_item="PI-1"),
			self.original(name="PR-2", idx=2, purchase_invoice_item="PI-2"),
		]
		plans, problems = integrity.purchase_return_link_plan([self.return_row(idx=9)], original, {})

		self.assertEqual(plans, [])
		self.assertEqual(codes(problems), ["RETURN_LINK_AMBIGUOUS"])

	def test_an_existing_link_is_not_rewritten(self):
		plans, problems = integrity.purchase_return_link_plan(
			[self.return_row(purchase_invoice_item="EXISTING")], [self.original()], {}
		)

		self.assertEqual(plans, [])
		self.assertEqual(problems, ())

	def test_a_unique_invoice_row_is_used_without_a_prior_receipt(self):
		plans, problems = integrity.purchase_return_link_plan(
			[self.return_row()],
			[],
			{"PI": [{"name": "PI-ITEM", "item_code": "ITEM"}]},
		)

		self.assertEqual(problems, ())
		self.assertEqual(plans[0]["purchase_invoice_item"], "PI-ITEM")

	def test_a_missing_link_on_the_original_row_is_resolved_from_the_invoice_row(self):
		plans, problems = integrity.purchase_return_link_plan(
			[self.return_row()],
			[self.original(purchase_invoice_item=None)],
			{"PI": [{"name": "PI-ITEM", "item_code": "ITEM"}]},
		)

		self.assertEqual(problems, ())
		self.assertEqual(plans[0]["purchase_invoice_item"], "PI-ITEM")
		self.assertNotEqual(plans[0]["purchase_invoice_item"], "PR-ITEM")

	def test_a_return_without_a_purchase_invoice_is_left_to_the_standard_workflow(self):
		plans, problems = integrity.purchase_return_link_plan(
			[self.return_row(purchase_invoice=None)],
			[self.original(purchase_invoice=None, purchase_invoice_item=None)],
			{},
		)

		self.assertEqual(plans, [])
		self.assertEqual(problems, ())

	def test_an_original_row_without_a_link_and_an_ambiguous_invoice_row_is_refused(self):
		plans, problems = integrity.purchase_return_link_plan(
			[self.return_row()],
			[self.original(purchase_invoice_item=None)],
			{
				"PI": [
					{"name": "PI-ITEM-1", "item_code": "ITEM"},
					{"name": "PI-ITEM-2", "item_code": "ITEM"},
				]
			},
		)

		self.assertEqual(plans, [])
		self.assertEqual(codes(problems), ["RETURN_LINK_AMBIGUOUS"])


class TestHooksAreRegisteredAndSafe(UnitTestCase):
	def test_the_approved_document_events_are_registered(self):
		"""The release composition contains every approved guard and no commitment handler."""
		from smartprocess_logistics import hooks_composition

		doc_events = hooks_composition.approved_doc_events()

		self.assertEqual(
			set(doc_events),
			{
				"Sales Invoice",
				"Delivery Note",
				"Purchase Invoice",
				"Purchase Receipt",
				"Stock Entry",
				"Stock Reservation Entry",
			},
		)

		self.assertEqual(
			doc_events["Sales Invoice"],
			{
				"validate": [
					"smartprocess_logistics.integrity.validate_sales_invoice",
					"smartprocess_logistics.warehouse.sales.validate_sales_invoice",
				],
			},
		)
		self.assertEqual(
			doc_events["Purchase Receipt"]["validate"],
			[
				"smartprocess_logistics.integrity.validate_purchase_receipt",
				"smartprocess_logistics.purchase.receipt.validate_purchase_receipt",
			],
		)
		self.assertEqual(
			doc_events["Delivery Note"]["validate"],
			[
				"smartprocess_logistics.integrity.validate_delivery_note",
				"smartprocess_logistics.warehouse.sales.validate_delivery_note",
			],
		)
		self.assertEqual(
			doc_events["Stock Entry"]["validate"],
			"smartprocess_logistics.warehouse.stock_entry.validate_stock_entry",
		)
		self.assertEqual(
			doc_events["Stock Reservation Entry"]["validate"],
			"smartprocess_logistics.warehouse.sales.validate_stock_reservation_entry",
		)
		self.assertEqual(
			doc_events["Purchase Invoice"]["validate"],
			"smartprocess_logistics.integrity.validate_purchase_invoice",
		)

	def test_the_handler_never_ignores_permissions_or_writes_sql(self):
		source = (APP_ROOT / "integrity.py").read_text(encoding="utf-8")

		self.assertNotIn("ignore_permissions=", source)
		self.assertNotIn("ignore_permissions =", source)
		self.assertNotIn("frappe.db.sql", source)
		self.assertNotIn("frappe.db.set_value", source)

	def test_the_rules_do_not_import_frappe(self):
		tree = ast.parse((APP_ROOT / "core" / "integrity.py").read_text(encoding="utf-8"))
		imports = {
			node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
		} | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

		self.assertNotIn("frappe", imports)
