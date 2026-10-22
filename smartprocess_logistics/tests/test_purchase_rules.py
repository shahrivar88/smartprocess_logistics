"""Pure tests for the warehouse-neutral purchase receipt rules."""

import pathlib

from smartprocess_logistics.purchase import rules
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]


def row(**overrides):
	values = {
		"idx": 1,
		"item_code": "ITEM",
		"is_stock_item": True,
		"is_managed": True,
		"has_serial_no": False,
		"qty": 1,
		"purchase_invoice": "PI",
		"purchase_invoice_docstatus": 1,
	}
	values.update(overrides)
	return values


class TestInvoiceFirstReceipt(UnitTestCase):
	def test_a_linked_stock_row_is_allowed(self):
		self.assertEqual(rules.stock_receipt_problems([row()]), ())
		self.assertEqual(
			rules.stock_receipt_problems([row(is_managed=False)]),
			(),
		)

	def test_every_stock_row_without_invoice_is_refused(self):
		for managed in (True, False):
			with self.subTest(managed=managed):
				problems = rules.stock_receipt_problems([row(is_managed=managed, purchase_invoice=None)])

				self.assertEqual(
					[problem.code for problem in problems],
					["PR_PURCHASE_INVOICE_REQUIRED"],
				)

	def test_a_draft_or_cancelled_invoice_is_refused(self):
		for status in (0, 2):
			with self.subTest(status=status):
				problems = rules.stock_receipt_problems([row(purchase_invoice_docstatus=status)])

				self.assertEqual(
					[problem.code for problem in problems],
					["PR_PURCHASE_INVOICE_NOT_SUBMITTED"],
				)

	def test_a_managed_serial_controlled_item_is_refused(self):
		problems = rules.stock_receipt_problems([row(has_serial_no=True)])

		self.assertEqual([problem.code for problem in problems], ["PR_SERIAL_PURCHASE_UNSUPPORTED"])

	def test_an_unmanaged_standard_serial_item_keeps_erpnext_workflow(self):
		self.assertEqual(
			rules.stock_receipt_problems([row(is_managed=False, has_serial_no=True)]),
			(),
		)

	def test_non_stock_and_zero_quantity_rows_are_left_to_standard(self):
		self.assertEqual(
			rules.stock_receipt_problems(
				[
					row(is_stock_item=False, purchase_invoice=None),
					row(qty=0, purchase_invoice=None),
				]
			),
			(),
		)

	def test_each_bad_row_is_reported(self):
		problems = rules.stock_receipt_problems(
			[row(idx=1, purchase_invoice=None), row(idx=2, has_serial_no=True)]
		)

		self.assertEqual(
			[problem.code for problem in problems],
			["PR_PURCHASE_INVOICE_REQUIRED", "PR_SERIAL_PURCHASE_UNSUPPORTED"],
		)


class TestModuleSafety(UnitTestCase):
	def test_new_handlers_do_not_ignore_permissions_or_commit(self):
		for module in (
			"purchase/receipt.py",
			"warehouse/classification.py",
			"warehouse/stock_entry.py",
			"warehouse/sales.py",
		):
			source = (APP_ROOT / module).read_text(encoding="utf-8")
			with self.subTest(module=module):
				self.assertNotIn("ignore_permissions=", source)
				self.assertNotIn("ignore_permissions =", source)
				self.assertNotIn("frappe.db.commit", source)
				self.assertNotIn("frappe.db.sql", source)
