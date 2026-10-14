"""The transaction plan, run against a fake gateway.

What a fake can prove: the order the calls are made in, which calls are made at all, that a
failure stops the sequence where it should, and that nothing is written when a check refuses.
What a fake **cannot** prove: that a real `SELECT ... FOR UPDATE` blocks a second request, that
MariaDB rolls the whole transaction back, or that a unique constraint rejects a duplicate. Those
are database guarantees listed in `test_orchestration.py`'s `NEEDS_A_DATABASE` note and in
`docs/integrity-invariants.md`, and they stay unverified until the app runs on a site.
"""

from dataclasses import dataclass, field
from datetime import datetime

from smartprocess_logistics.core import authz, normalization
from smartprocess_logistics.core.authz import (
	ROLE_EDITOR,
	ROLE_MANAGER,
	AuthorizationError,
	InvoiceRef,
	Principal,
)
from smartprocess_logistics.core.orchestration import (
	CODE_BAD_ORDER,
	CODE_COMPANY_CHANGED,
	CODE_GATEWAY_MISSING,
	CODE_INVOICE_CHANGED,
	CODE_NO_SUBMISSIONS,
	CODE_ROW_UNKNOWN,
	LOCK_ORDER,
	REWRITE_STEPS,
	OrchestrationError,
	Step,
	build_append_plan,
	build_rewrite_plan,
	check_lock_order,
	lock_order_documentation,
	run_rewrite,
)
from smartprocess_logistics.core.validation import (
	CODE_ALREADY_REGISTERED,
	CODE_DUPLICATE_IN_ROW,
	CODE_EXCESS_QUANTITY,
	ExistingAssignment,
	InvoiceRow,
)
from smartprocess_logistics.tests.base import UnitTestCase

# Anything a fake cannot decide, and a real site has to:
NEEDS_A_DATABASE = (
	"a real SELECT ... FOR UPDATE blocking a second request",
	"MariaDB rolling the whole transaction back on an exception",
	"the composite unique index rejecting a duplicate",
	"the request boundary committing exactly once",
)

NOW = datetime(2026, 10, 12, 18, 0)
ALPHA = InvoiceRef(name="IN-260013", company="Alpha", docstatus=1)
EDITOR = Principal(user="editor@example.com", roles=frozenset({ROLE_EDITOR}))
MANAGER = Principal(user="manager@example.com", roles=frozenset({ROLE_MANAGER}))
LIMITED = Principal(
	user="limited@example.com",
	roles=frozenset({ROLE_EDITOR}),
	companies=frozenset({"Beta"}),
)

ROW_1 = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=2, serial_required=True)
ROW_2 = InvoiceRow(row_key="row-2", item_code="ITEM-2", invoiced_qty=1, serial_required=True)


@dataclass
class FakeGateway:
	"""A gateway that records what it was asked to do, and can be told to break."""

	invoice: InvoiceRef = ALPHA
	rows: tuple = (ROW_1, ROW_2)
	existing: tuple = ()
	fail_on: str = None
	calls: list = field(default_factory=list)
	written: list = field(default_factory=list)
	deleted_rows: list = field(default_factory=list)
	applied: list = field(default_factory=list)
	serial_requests: tuple = ()
	# What each read of the stored assignments was asked for: `True` means it was taken as a
	# locking read, which is the difference between the count the capacity rule decides on and
	# whatever the transaction's view happened to hold.
	existing_reads: list = field(default_factory=list)

	def _record(self, name):
		self.calls.append(name)

		if self.fail_on == name:
			raise RuntimeError(f"the gateway was told to fail on {name}")

	def open_transaction(self):
		self._record("open_transaction")

	def lock_invoice(self, name):
		self._record("lock_invoice")

		return self.invoice

	def read_rows(self, invoice_name):
		self._record("read_rows")

		return tuple(self.rows)

	def read_existing(self, invoice_name, for_update=False):
		self._record("read_existing")
		self.existing_reads.append(for_update)

		return tuple(self.existing)

	def lock_serials(self, item_codes):
		self._record("lock_serials")

		return len(item_codes)

	def delete_row_assignments(self, invoice_name, row_key):
		self._record("delete_row_assignments")
		self.deleted_rows.append(row_key)

		return sum(1 for item in self.existing if item.row_key == row_key)

	def find_or_create_serials(self, requests):
		self._record("find_or_create_serials")
		self.serial_requests = requests

		by_normalized = {}
		created = []

		for item_code, normalized, _original, _version in requests:
			by_normalized[(item_code, normalized)] = f"LS-{item_code}-{normalized}"
			created.append(f"LS-{item_code}-{normalized}")

		return {"by_normalized": by_normalized, "created": tuple(created), "reused": 0}

	def insert_assignments(self, values):
		self._record("insert_assignments")
		self.written.extend(values)

		return tuple(f"LSA-{index}" for index in range(len(values)))


def rewrite(rows=(ROW_1, ROW_2), submissions=(("row-1", "A1"),), **overrides):
	arguments = {
		"invoice": ALPHA,
		"rows": rows,
		"existing": (),
		"submissions": submissions,
		"principal": EDITOR,
	}
	arguments.update(overrides)

	return build_rewrite_plan(**arguments)


def append(rows=(ROW_1, ROW_2), row_key="row-1", serials=("A1",), **overrides):
	"""Serials added to one row — the shape the scan station's endpoints build."""
	arguments = {
		"invoice": ALPHA,
		"rows": rows,
		"existing": (),
		"row_key": row_key,
		"serials": serials,
		"principal": EDITOR,
	}
	arguments.update(overrides)

	return build_append_plan(**arguments)


class TestAppendBatch(UnitTestCase):
	"""A row's labels written together: what makes a batch different from a loop of single adds.

	A scan station sends one row's waiting labels in one request. The point of that is not speed
	but *one answer for one row*: a batch the validator refuses writes nothing at all, so a row
	cannot end up holding the first half of what an operator asked for while a failure is reported
	about the second half. These are the two halves of that promise.
	"""

	def test_every_label_of_an_accepted_batch_is_inserted(self):
		wide = (InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=3, serial_required=True),)
		gateway = FakeGateway(rows=wide)
		outcome = run_rewrite(append(rows=wide, serials=("B1", "B2", "B3")), gateway)

		self.assertEqual(outcome.accepted, 3)
		self.assertEqual([item.normalized_serial for item in gateway.written], ["b1", "b2", "b3"])

	def test_a_duplicate_inside_the_batch_refuses_the_whole_batch(self):
		"""Not the duplicate alone: a batch is accepted whole or refused whole."""
		gateway = FakeGateway()

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(append(serials=("D1", "D1")), gateway)

		self.assertEqual(caught.exception.code, CODE_DUPLICATE_IN_ROW)
		self.assertEqual(gateway.written, [], "nothing is written when the batch is refused")

	def test_a_label_already_on_the_invoice_refuses_the_whole_batch(self):
		gateway = FakeGateway(existing=(ExistingAssignment(row_key="row-1", normalized_serial="a0"),))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(append(serials=("NEW", "A0")), gateway)

		self.assertEqual(caught.exception.code, CODE_ALREADY_REGISTERED)
		self.assertEqual(gateway.written, [])

	def test_a_batch_past_the_row_quantity_refuses_the_whole_batch(self):
		"""The quantity limit, applied to the labels in hand rather than one at a time."""
		gateway = FakeGateway()

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(append(serials=("Q1", "Q2", "Q3")), gateway)

		self.assertEqual(caught.exception.code, CODE_EXCESS_QUANTITY)
		self.assertEqual(gateway.written, [])

	def test_a_batch_never_names_a_row_to_replace(self):
		"""The whole reason this is not `set_row_serials`: an append deletes nothing."""
		plan = append(serials=("B1", "B2"))

		self.assertEqual(plan.only_rows, ())

	def test_an_empty_batch_is_refused(self):
		with self.assertRaises(OrchestrationError) as caught:
			append(serials=())

		self.assertEqual(caught.exception.code, CODE_NO_SUBMISSIONS)


class TestLockOrder(UnitTestCase):
	def test_the_documented_order_has_four_locks(self):
		self.assertEqual(len(LOCK_ORDER), 4)
		self.assertEqual(lock_order_documentation().count("->"), 3)

	def test_the_declared_plans_follow_the_order(self):
		self.assertTrue(check_lock_order(tuple(Step(kind) for kind in REWRITE_STEPS)))

	def test_an_inverted_order_is_refused(self):
		bad = (Step("lock_invoice"), Step("lock_session"))

		with self.assertRaises(OrchestrationError) as caught:
			check_lock_order(bad)

		self.assertEqual(caught.exception.code, CODE_BAD_ORDER)

	def test_an_unknown_step_is_refused(self):
		with self.assertRaises(OrchestrationError):
			check_lock_order((Step("do_something_else"),))

	def test_the_rewrite_takes_the_invoice_lock_first(self):
		self.assertEqual(REWRITE_STEPS[0], "lock_invoice")


class TestBuildRewritePlan(UnitTestCase):
	def test_a_valid_request_builds(self):
		plan = rewrite()

		self.assertEqual(plan.invoice_name, ALPHA.name)
		self.assertEqual(plan.company, ALPHA.company)
		self.assertEqual(plan.row_keys(), ("row-1", "row-2"))

	def test_a_missing_invoice_is_refused(self):
		with self.assertRaises(OrchestrationError) as caught:
			rewrite(invoice=None)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CHANGED)

	def test_an_empty_request_is_refused(self):
		with self.assertRaises(OrchestrationError) as caught:
			rewrite(rows=(), submissions=())

		self.assertEqual(caught.exception.code, CODE_NO_SUBMISSIONS)

	def test_an_unknown_row_is_refused_before_any_lock(self):
		with self.assertRaises(OrchestrationError) as caught:
			rewrite(submissions=(("row-9", "A1"),))

		self.assertEqual(caught.exception.code, CODE_ROW_UNKNOWN)

	def test_a_principal_from_another_company_cannot_build_a_plan(self):
		with self.assertRaises(AuthorizationError):
			rewrite(principal=LIMITED)

	def test_a_manager_may_build_a_plan(self):
		self.assertTrue(rewrite(principal=MANAGER).invoice_name)


class TestBuildAppendPlan(UnitTestCase):
	"""One serial added to one row: the rule the scan station stands on.

	The station writes what a barcode scanner produced, once per label, into a row that may
	already hold labels this operator never saw. Everything that makes that safe is here: the row
	is added to and never replaced, the quantity rule still applies, and a duplicate is refused
	rather than written over.
	"""

	def test_an_append_replaces_nothing(self):
		plan = append()

		self.assertEqual(plan.only_rows, ())
		self.assertEqual(plan.submissions, (("row-1", "A1"),))

	def test_an_append_keeps_every_label_the_row_already_has(self):
		gateway = FakeGateway(existing=(ExistingAssignment(row_key="row-1", normalized_serial="b7"),))
		outcome = run_rewrite(append(), gateway)

		self.assertEqual(outcome.accepted, 1)
		self.assertEqual(gateway.deleted_rows, [])
		self.assertEqual(outcome.deleted, 0)

	def test_two_operators_scanning_the_same_row_end_with_both_labels(self):
		"""The reason this endpoint exists at all.

		A replace would lose one of the two labels. An append cannot: the second request reads the
		row under the invoice lock — which is what `run_rewrite` takes before it reads — and adds
		to what the first one wrote. Neither request deletes anything.
		"""
		first = FakeGateway()
		run_rewrite(append(serials=("AAA111",)), first)

		second = FakeGateway(existing=(ExistingAssignment(row_key="row-1", normalized_serial="aaa111"),))
		outcome = run_rewrite(append(serials=("BBB222",)), second)

		self.assertEqual(outcome.accepted, 1)
		self.assertEqual(len(second.written), 1)
		self.assertEqual(second.deleted_rows, [])
		self.assertEqual(outcome.deleted, 0)

	def test_an_append_over_the_rows_quantity_is_refused(self):
		# ROW_2 invoices one unit, and one is already registered against it.
		gateway = FakeGateway(existing=(ExistingAssignment(row_key="row-2", normalized_serial="b1"),))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(append(row_key="row-2", serials=("B2",)), gateway)

		self.assertEqual(caught.exception.code, CODE_EXCESS_QUANTITY)
		self.assertEqual(gateway.written, [])

	def test_a_label_already_on_the_row_is_refused(self):
		gateway = FakeGateway(existing=(ExistingAssignment(row_key="row-1", normalized_serial="a1"),))

		with self.assertRaises(OrchestrationError):
			run_rewrite(append(serials=("A1",)), gateway)

		self.assertEqual(gateway.written, [])

	def test_an_append_to_a_row_the_invoice_does_not_have_is_refused(self):
		with self.assertRaises(OrchestrationError) as caught:
			append(row_key="row-9")

		self.assertEqual(caught.exception.code, CODE_ROW_UNKNOWN)

	def test_an_append_to_a_missing_invoice_is_refused(self):
		with self.assertRaises(OrchestrationError) as caught:
			append(invoice=None)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CHANGED)

	def test_an_append_from_another_company_is_refused(self):
		with self.assertRaises(AuthorizationError):
			append(principal=LIMITED)


class TestRunRewrite(UnitTestCase):
	def test_an_import_writes_in_order_and_deletes_nothing(self):
		# `rewrite()` names no replaced row, which is the shape `confirm_import` builds. An
		# import adds to a row that may already hold serials the file never mentions, so the
		# rows it fills keep what they have.
		gateway = FakeGateway()
		outcome = run_rewrite(rewrite(), gateway)

		self.assertEqual(outcome.accepted, 1)
		self.assertEqual(len(gateway.written), 1)
		self.assertEqual(
			gateway.calls,
			[
				"open_transaction",
				"lock_invoice",
				"read_rows",
				"lock_serials",
				"read_existing",
				"find_or_create_serials",
				"insert_assignments",
			],
		)
		self.assertEqual(gateway.deleted_rows, [])
		self.assertEqual(outcome.deleted, 0)

	def test_the_count_the_cap_decides_on_is_read_after_the_locks_and_locked_itself(self):
		"""A consistent read here would answer with a view taken before another writer committed.

		The two halves of this are one decision: the read has to happen under the invoice lock —
		otherwise it is a read of nothing — and it has to be a locking read, or it is a read of what
		the row held when this transaction began. Measured on a real site: with a plain read, a
		request that had read a row's third place, waited while another request took it, and then
		wrote was let through the cap on its older count.
		"""
		gateway = FakeGateway()
		run_rewrite(rewrite(), gateway)

		self.assertEqual(gateway.existing_reads, [True])
		self.assertLess(gateway.calls.index("lock_invoice"), gateway.calls.index("read_existing"))
		self.assertLess(gateway.calls.index("lock_serials"), gateway.calls.index("read_existing"))
		self.assertLess(gateway.calls.index("read_existing"), gateway.calls.index("insert_assignments"))

	def test_an_import_keeps_what_the_row_already_has(self):
		gateway = FakeGateway(existing=(ExistingAssignment(row_key="row-1", normalized_serial="a0"),))
		outcome = run_rewrite(rewrite(submissions=(("row-1", "A1"),)), gateway)

		self.assertEqual(outcome.accepted, 1)
		self.assertEqual(gateway.deleted_rows, [])
		self.assertEqual(outcome.deleted, 0)

	def test_the_invoice_is_locked_before_anything_is_read_or_written(self):
		gateway = FakeGateway()
		run_rewrite(rewrite(), gateway)

		self.assertLess(gateway.calls.index("lock_invoice"), gateway.calls.index("read_rows"))
		self.assertLess(gateway.calls.index("lock_invoice"), gateway.calls.index("insert_assignments"))

	def test_the_identity_is_derived_from_the_serial_not_from_the_request(self):
		gateway = FakeGateway()
		run_rewrite(rewrite(submissions=(("row-1", "  aB-12  "),)), gateway)

		written = gateway.written[0]

		self.assertEqual(written.normalized_serial, "ab-12")
		self.assertEqual(written.sales_invoice_item, "row-1")
		self.assertEqual(written.company, "Alpha")

	def test_the_rule_set_that_normalized_a_serial_travels_with_it(self):
		# The version is the one the normalization actually ran with, so a serial created after a
		# rule change is not relabelled with the new rules' number.
		gateway = FakeGateway()
		run_rewrite(rewrite(submissions=(("row-1", "A1"),)), gateway)

		self.assertEqual(
			gateway.serial_requests,
			(("ITEM-1", "a1", "A1", normalization.NORMALIZATION_VERSION),),
		)

	def test_a_row_that_disappeared_stops_the_write(self):
		gateway = FakeGateway(rows=(ROW_1,))
		plan = rewrite(rows=(ROW_1, ROW_2), submissions=(("row-2", "B1"),))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(plan, gateway)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CHANGED)
		self.assertEqual(gateway.written, [])
		self.assertEqual(gateway.deleted_rows, [])

	def test_a_changed_company_stops_the_write(self):
		gateway = FakeGateway(invoice=InvoiceRef(name=ALPHA.name, company="Beta", docstatus=1))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(rewrite(), gateway)

		self.assertEqual(caught.exception.code, CODE_COMPANY_CHANGED)
		self.assertEqual(gateway.written, [])

	def test_an_invoice_cancelled_while_the_request_waited_stops_the_write(self):
		"""The lifecycle as it is *now*, read under the lock.

		The endpoint decided against the invoice as it was a moment ago. If the site cancelled it
		in between — the case a lock exists for — the locked read is the answer that counts, and
		the write must not happen. A fake cannot prove MariaDB's lock; it can prove that this plan
		reads the value the lock returned and refuses on it, which is what the real one does too.
		"""
		gateway = FakeGateway(invoice=InvoiceRef(name=ALPHA.name, company="Alpha", docstatus=2))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(rewrite(), gateway)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CHANGED)
		self.assertEqual(caught.exception.message, authz.REFUSAL_INVOICE_CANCELLED)
		self.assertEqual(gateway.written, [])
		self.assertEqual(gateway.deleted_rows, [])

	def test_a_missing_invoice_stops_the_write(self):
		gateway = FakeGateway(invoice=None)

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(rewrite(), gateway)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CHANGED)

	def test_an_excess_is_caught_by_the_revalidation_and_nothing_is_written(self):
		"""The validator's own code survives, so a caller can tell which rule was broken.

		This used to assert `CODE_PAYLOAD_CHANGED` for every validation failure, which made an
		excess, a duplicate and an over-long serial indistinguishable in the code — the half of
		the error a client switches on — while the message told them apart.
		"""
		gateway = FakeGateway()
		plan = rewrite(
			rows=(ROW_1,),
			submissions=(("row-1", "A1"), ("row-1", "A2"), ("row-1", "A3")),
		)

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(plan, gateway)

		self.assertEqual(caught.exception.code, CODE_EXCESS_QUANTITY)
		self.assertEqual(gateway.written, [])
		self.assertEqual(gateway.deleted_rows, [])

	def test_a_duplicate_in_one_row_keeps_the_validators_code(self):
		gateway = FakeGateway()
		plan = rewrite(rows=(ROW_1,), submissions=(("row-1", "A1"), ("row-1", "a1")))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(plan, gateway)

		self.assertEqual(caught.exception.code, CODE_DUPLICATE_IN_ROW)
		self.assertEqual(gateway.written, [])

	def test_a_duplicate_inside_the_file_is_caught_before_anything_is_deleted(self):
		gateway = FakeGateway()
		plan = rewrite(rows=(ROW_1,), submissions=(("row-1", "A1"), ("row-1", "A1")))

		with self.assertRaises(OrchestrationError):
			run_rewrite(plan, gateway)

		self.assertEqual(gateway.deleted_rows, [])

	def test_a_serial_already_on_the_invoice_is_caught(self):
		gateway = FakeGateway(existing=(ExistingAssignment("row-1", "a1"),))

		with self.assertRaises(OrchestrationError):
			run_rewrite(rewrite(rows=(ROW_1,)), gateway)

		self.assertEqual(gateway.written, [])

	def test_a_failure_late_in_the_sequence_leaves_the_earlier_writes_uncommitted(self):
		# The fake records the calls, so this shows the sequence stopped; only a database can
		# show that the writes it did make were rolled back. See NEEDS_A_DATABASE.
		gateway = FakeGateway(fail_on="insert_assignments")

		with self.assertRaises(RuntimeError):
			run_rewrite(rewrite(only_rows=("row-1",)), gateway)

		self.assertIn("delete_row_assignments", gateway.calls)
		# Nothing in this path talks to a session any more: the claim that made a preview
		# applyable once went with the endpoint that applied it.
		self.assertFalse([call for call in gateway.calls if "session" in call], gateway.calls)

	def test_a_gateway_missing_a_method_is_refused_before_it_starts(self):
		class Partial:
			def open_transaction(self):
				pass

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(rewrite(), Partial())

		self.assertEqual(caught.exception.code, CODE_GATEWAY_MISSING)

	def test_the_only_row_rewritten_can_be_restricted(self):
		gateway = FakeGateway()
		plan = rewrite(rows=(ROW_1, ROW_2), submissions=(("row-1", "A1"),), only_rows=("row-1",))
		outcome = run_rewrite(plan, gateway)

		self.assertEqual(outcome.rewrite_rows, ("row-1",))
		self.assertEqual(gateway.deleted_rows, ["row-1"])

	def test_a_named_row_may_be_written_with_the_serials_it_already_has(self):
		# The end-to-end shape of a correction: the row is named as replaced, so the serials it
		# already holds are not duplicates, and its assignments are removed before the new set is
		# written. `plan_registration` alone cannot show the deletion order, which is the half
		# that has to happen first.
		gateway = FakeGateway(
			existing=(
				ExistingAssignment(row_key="row-1", normalized_serial="a1"),
				ExistingAssignment(row_key="row-1", normalized_serial="a2"),
			)
		)
		# `ROW_1` needs two serials. Sending one it already holds and one it does not is the
		# smallest correction: two accepted, both stored serials rewritten.
		plan = rewrite(submissions=(("row-1", "A1"), ("row-1", "A9")), only_rows=("row-1",))

		outcome = run_rewrite(plan, gateway)

		self.assertEqual(outcome.accepted, 2)
		self.assertEqual(outcome.deleted, 2)
		self.assertEqual(gateway.deleted_rows, ["row-1"])
		self.assertLess(
			gateway.calls.index("delete_row_assignments"), gateway.calls.index("insert_assignments")
		)
		self.assertEqual(sorted(written.normalized_serial for written in gateway.written), ["a1", "a9"])
