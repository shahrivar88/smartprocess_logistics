"""What the gateway reads for a transfer, driven through the fake runtime.

One read in this path is the whole reason the module exists: counting what an amended invoice
**already holds**, which is the number the capacity rule subtracts. `read_rows` gives the rows and
their quantities, `read_existing` gives the registrations, and the difference is the room a
transfer has left.

It was broken, and nothing reached it: `_count_by_row` reads the row *dicts* the import path has in
hand (they carry the invoice they were read from), and this path hands it `ExistingAssignment`
objects (read for one invoice, so they do not). A target with no registrations on it never enters
that loop, so every database-free test and the browser harness — both of which preview against an
empty target — passed while the second preview of a real transfer raised `AttributeError`. The lab
found it on the plan's phase 4 step 5, the same button pressed a second time.

So the case below is deliberately the one every other suite skips: a target that already holds
registrations.

`read_rows` is stubbed rather than faked. It reads `Sales Invoice Item` rows and reaches
`row.item_code` on each, and the fake runtime hands back plain `dict`s where Frappe hands back
attribute-accessible ones — a difference in the stand-in, not in the code under test. The subject
here is the count and the subtraction, and `read_existing` — the half that reads assignments, with
`.get`, which the fake does support — runs for real.
"""

import importlib
import unittest

from smartprocess_logistics.core.validation import InvoiceRow
from smartprocess_logistics.tests.fake_bench import FakeBench, FakeReadRuntime

INVOICE = "Sales Invoice"
ASSIGNMENT = "Logistics Serial Assignment"

TARGET = "SINV-B"
ROW = "SINV-B-1"


def bench_with_a_target_holding(registrations, qty=3):
	"""One invoice whose single row invoices `qty`, with `registrations` labels already on it."""
	bench = FakeBench()
	bench.add_doctype(
		ASSIGNMENT,
		rows=[
			{
				"name": f"LSA-{index}",
				"sales_invoice": TARGET,
				"sales_invoice_item": ROW,
				"item_code": "ITEM-1",
				"normalized_serial": f"AAA{index}",
				"logistics_serial": f"LS-{index}",
				"company": "COMPANY",
			}
			for index in range(1, registrations + 1)
		],
	)

	return bench


class TargetRowsTest(unittest.TestCase):
	def gateway_over(self, bench, qty=3, serial_required=True, rows=1):
		self.runtime = FakeReadRuntime(bench)
		self.runtime.start()
		self.addCleanup(self.runtime.stop)

		# `importlib`, like the company-scope tests: a plain import finds the parent package's
		# stale attribute and hands back the module built for the previous test's runtime.
		gateway = importlib.import_module("smartprocess_logistics.api.gateway")
		instance = gateway.FrappeGateway()
		instance.read_rows = lambda invoice_name: tuple(
			InvoiceRow(
				row_key=f"{TARGET}-{index}",
				item_code="ITEM-1",
				invoiced_qty=qty,
				serial_required=serial_required,
			)
			for index in range(1, rows + 1)
		)

		return instance

	def test_a_target_that_already_holds_registrations_counts_them(self):
		"""The case that raised: the count is taken over objects, and the room is the difference."""
		gateway = self.gateway_over(bench_with_a_target_holding(registrations=2))

		rows = gateway.read_target_rows(TARGET)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].row_key, ROW)
		self.assertEqual(rows[0].capacity, 3)
		self.assertEqual(rows[0].held, 2)
		self.assertEqual(rows[0].room, 1)

	def test_an_untouched_target_has_all_of_its_room(self):
		gateway = self.gateway_over(bench_with_a_target_holding(registrations=0))

		rows = gateway.read_target_rows(TARGET)

		self.assertEqual(rows[0].held, 0)
		self.assertEqual(rows[0].room, rows[0].capacity)

	def test_a_full_target_has_no_room_left(self):
		gateway = self.gateway_over(bench_with_a_target_holding(registrations=3))

		rows = gateway.read_target_rows(TARGET)

		self.assertEqual(rows[0].room, 0)

	def test_the_capacity_is_the_quantity_the_row_invoices(self):
		gateway = self.gateway_over(bench_with_a_target_holding(registrations=0), qty=18)

		rows = gateway.read_target_rows(TARGET)

		self.assertEqual(rows[0].capacity, 18)
		self.assertEqual(rows[0].room, 18)

	def test_the_item_s_own_flag_travels_with_the_row(self):
		"""Eligibility is read from the item and carried, not decided by whoever counts."""
		gateway = self.gateway_over(bench_with_a_target_holding(registrations=1), serial_required=False)

		rows = gateway.read_target_rows(TARGET)

		self.assertFalse(rows[0].serial_required)
		self.assertEqual(rows[0].held, 1)

	def test_every_row_is_reported_with_its_own_count(self):
		"""Two rows, and only one of them holds anything: the counts do not bleed between them."""
		bench = bench_with_a_target_holding(registrations=1)
		bench.tables[ASSIGNMENT].append(
			{
				"name": "LSA-other",
				"sales_invoice": TARGET,
				"sales_invoice_item": f"{TARGET}-2",
				"item_code": "ITEM-1",
				"normalized_serial": "BBB1",
				"logistics_serial": "LS-other",
				"company": "COMPANY",
			}
		)
		gateway = self.gateway_over(bench, rows=2)

		rows = gateway.read_target_rows(TARGET)

		self.assertEqual([row.held for row in rows], [1, 1])
		self.assertEqual([row.index for row in rows], [1, 2])
