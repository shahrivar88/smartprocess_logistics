"""Pure tests for the warehouse-neutral purchase receipt rules."""

import pathlib

from smartprocess_logistics.purchase import rules
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPANY = "SP TEST COMPANY"


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


def receipt(**overrides):
	values = {"name": "PR-1", "company": COMPANY, "docstatus": 1}
	values.update(overrides)
	return values


class TestInvoiceCancelGuard(UnitTestCase):
	def test_a_submitted_receipt_blocks_the_cancel_and_is_named(self):
		problems = rules.invoice_cancel_problems([receipt()], COMPANY)

		self.assertEqual([problem.code for problem in problems], ["PI_CANCEL_BLOCKED_BY_PURCHASE_RECEIPT"])
		self.assertIn("PR-1", problems[0].message)

	def test_no_receipt_means_no_problem(self):
		self.assertEqual(rules.invoice_cancel_problems([], COMPANY), ())

	def test_draft_and_cancelled_receipts_do_not_block(self):
		self.assertEqual(rules.invoice_cancel_problems([receipt(docstatus=0)], COMPANY), ())
		self.assertEqual(rules.invoice_cancel_problems([receipt(docstatus=2)], COMPANY), ())

	def test_only_active_receipts_are_named(self):
		problems = rules.invoice_cancel_problems(
			[
				receipt(name="PR-1"),
				receipt(name="PR-2", docstatus=0),
				receipt(name="PR-3", docstatus=2),
				receipt(name="PR-4"),
			],
			COMPANY,
		)

		self.assertIn("PR-1", problems[0].message)
		self.assertIn("PR-4", problems[0].message)
		self.assertNotIn("PR-2", problems[0].message)
		self.assertNotIn("PR-3", problems[0].message)
		self.assertIn("2 رسید خرید ثبت‌شده", problems[0].message)

	def test_a_receipt_of_another_company_neither_blocks_nor_is_named(self):
		problems = rules.invoice_cancel_problems(
			[receipt(name="PR-OTHER", company="OTHER")],
			COMPANY,
		)

		self.assertEqual(problems, ())

	def test_only_the_matching_company_is_named_when_both_exist(self):
		problems = rules.invoice_cancel_problems(
			[receipt(name="PR-OWN"), receipt(name="PR-OTHER", company="OTHER")],
			COMPANY,
		)

		self.assertIn("PR-OWN", problems[0].message)
		self.assertNotIn("PR-OTHER", problems[0].message)
		self.assertIn("1 رسید خرید ثبت‌شده", problems[0].message)

	def test_the_same_receipt_named_twice_blocks_once(self):
		problems = rules.invoice_cancel_problems([receipt(), receipt()], COMPANY)

		self.assertIn("1 رسید خرید ثبت‌شده", problems[0].message)
		self.assertEqual(problems[0].message.count("PR-1"), 1)

	def test_the_message_names_at_most_the_configured_number_of_receipts(self):
		names = [f"PR-{index:02d}" for index in range(1, 13)]

		message = rules.invoice_cancel_problems(
			[receipt(name=name) for name in names], COMPANY
		)[0].message

		for name in names[:10]:
			self.assertIn(name, message)
		self.assertNotIn("PR-11", message)
		self.assertNotIn("PR-12", message)
		self.assertIn("2 رسید دیگر", message)


class TestModuleSafety(UnitTestCase):
	def test_the_cancel_guard_is_registered_as_before_cancel(self):
		from smartprocess_logistics import hooks_composition

		self.assertEqual(
			hooks_composition.approved_doc_events()["Purchase Invoice"]["before_cancel"],
			"smartprocess_logistics.purchase.invoice.before_cancel",
		)

	def test_the_cancel_guard_runs_only_for_a_cancel(self):
		"""`_cancel()` sets docstatus before saving, so the handler keys on the action."""
		source = (APP_ROOT / "purchase" / "invoice.py").read_text(encoding="utf-8")
		body = source.split("def _skip", 1)[1].split("def _item_link_names", 1)[0]

		self.assertIn('"cancel"', body)
		self.assertNotIn('doc.docstatus != 1', body)

	def test_the_cancel_guard_reads_both_receipt_links(self):
		source = (APP_ROOT / "purchase" / "invoice.py").read_text(encoding="utf-8")

		self.assertIn('"purchase_invoice"', source)
		self.assertIn('"purchase_invoice_item"', source)
		self.assertIn('"Purchase Receipt Item"', source)

	def test_the_cancel_guard_honours_the_framework_ignore_links_flag(self):
		"""The standard back-link check skips on `ignore_links`; a data repair must keep working."""
		source = (APP_ROOT / "purchase" / "invoice.py").read_text(encoding="utf-8")
		body = source.split("def _skip", 1)[1].split("def _item_link_names", 1)[0]

		self.assertIn("ignore_links", body)

	def test_the_header_link_is_only_trusted_when_it_points_at_an_invoice(self):
		"""A site field with the same name but another type must not block by accident."""
		source = (APP_ROOT / "purchase" / "invoice.py").read_text(encoding="utf-8")
		body = source.split("def _header_link_names", 1)[1].split("def _submitted_receipts", 1)[0]

		self.assertIn('field.fieldtype != "Link"', body)
		self.assertIn('field.options != "Purchase Invoice"', body)

	def test_the_cancel_guard_locks_the_invoice_before_reading_receipts(self):
		"""A receipt submitted concurrently must be visible to the cancel, and vice versa."""
		source = (APP_ROOT / "purchase" / "invoice.py").read_text(encoding="utf-8")
		body = source.split("def before_cancel", 1)[1]

		self.assertIn("for_update=True", body)
		self.assertLess(body.index("for_update=True"), body.index("_submitted_receipts("))

	def test_the_cancel_guard_passes_the_invoice_company_to_the_rule(self):
		"""Company isolation lives in the pure rule, where a test can reach it."""
		source = (APP_ROOT / "purchase" / "invoice.py").read_text(encoding="utf-8")
		body = source.split("def before_cancel", 1)[1]

		self.assertIn("rules.invoice_cancel_problems(receipts, doc.company)", body)

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
			"purchase/invoice.py",
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
