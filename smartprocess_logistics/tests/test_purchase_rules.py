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


class TestRejectedWarehouse(UnitTestCase):
	def test_rejected_quantity_requires_a_rejected_warehouse(self):
		problems = rules.stock_receipt_problems(
			[row(rejected_qty=1, rejected_warehouse=None, rejected_warehouse_kind=None)]
		)

		self.assertEqual([problem.code for problem in problems], ["PR_REJECTED_WAREHOUSE_REQUIRED"])

	def test_rejected_quantity_only_accepts_a_virtual_warehouse(self):
		allowed = rules.stock_receipt_problems(
			[row(rejected_qty=1, rejected_warehouse="V", rejected_warehouse_kind="virtual")]
		)
		refused = rules.stock_receipt_problems(
			[row(rejected_qty=1, rejected_warehouse="P", rejected_warehouse_kind="physical")]
		)

		self.assertEqual(allowed, ())
		self.assertEqual(
			[problem.code for problem in refused],
			["PR_REJECTED_WAREHOUSE_NOT_VIRTUAL"],
		)

	def test_transit_is_not_a_rejected_warehouse(self):
		problems = rules.stock_receipt_problems(
			[row(rejected_qty=1, rejected_warehouse="T", rejected_warehouse_kind="transit")]
		)

		self.assertEqual(
			[problem.code for problem in problems],
			["PR_REJECTED_WAREHOUSE_NOT_VIRTUAL"],
		)

	def test_a_zero_quantity_rejected_row_is_still_checked(self):
		problems = rules.stock_receipt_problems(
			[row(qty=0, rejected_qty=1, rejected_warehouse="P", rejected_warehouse_kind="physical")]
		)

		self.assertEqual(
			[problem.code for problem in problems],
			["PR_REJECTED_WAREHOUSE_NOT_VIRTUAL"],
		)

	def test_a_return_still_checks_its_rejected_warehouse(self):
		problems = rules.stock_receipt_problems(
			[
				row(
					purchase_invoice=None,
					rejected_qty=1,
					rejected_warehouse="P",
					rejected_warehouse_kind="physical",
				)
			],
			invoice_first=False,
		)

		self.assertEqual(
			[problem.code for problem in problems],
			["PR_REJECTED_WAREHOUSE_NOT_VIRTUAL"],
		)

	def test_non_stock_or_zero_rejected_rows_are_left_to_erpnext(self):
		self.assertEqual(
			rules.stock_receipt_problems(
				[row(is_stock_item=False, rejected_qty=1, rejected_warehouse=None)]
			),
			(),
		)
		self.assertEqual(
			rules.stock_receipt_problems(
				[row(rejected_qty=0, rejected_warehouse=None, rejected_warehouse_kind=None)]
			),
			(),
		)


class TestModuleSafety(UnitTestCase):
	def test_receipt_handler_reads_the_rejected_warehouse_classification(self):
		source = (APP_ROOT / "purchase" / "receipt.py").read_text(encoding="utf-8")

		self.assertIn("rejected_warehouse_kind", source)
		self.assertIn("policy.kind", source)
		self.assertIn("classification.virtual_types()", source)

	def test_receipt_handler_scopes_the_rejected_warehouse_read_to_the_company(self):
		source = (APP_ROOT / "purchase" / "receipt.py").read_text(encoding="utf-8")

		self.assertIn('"company": doc.company', source)

	def test_receipt_handler_reads_standard_stock_flags_without_the_managed_field(self):
		"""The managed flag is optional; its absence must not switch the whole handler off."""
		source = (APP_ROOT / "purchase" / "receipt.py").read_text(encoding="utf-8")
		body = source.split("def _item_flags", 1)[1].split("def validate_purchase_receipt", 1)[0]

		self.assertIn("fields.append(MANAGED_FIELD)", body)
		self.assertLess(body.index('"is_stock_item"'), body.index("get_field(MANAGED_FIELD)"))

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
