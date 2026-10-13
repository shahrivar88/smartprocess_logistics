"""The transaction plan, run against a fake gateway.

What a fake can prove: the order the calls are made in, which calls are made at all, that a
failure stops the sequence where it should, and that nothing is written when a check refuses.
What a fake **cannot** prove: that a real `SELECT ... FOR UPDATE` blocks a second request, that
MariaDB rolls the whole transaction back, or that a unique constraint rejects a duplicate. Those
are database guarantees listed in `test_orchestration.py`'s `NEEDS_A_DATABASE` note and in
`docs/integrity-invariants.md`, and they stay unverified until the app runs on a site.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from smartprocess_logistics.core import normalization
from smartprocess_logistics.core import session as session_rules
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
	CODE_PAYLOAD_CHANGED,
	CODE_ROW_UNKNOWN,
	CONFIRMATION_STEPS,
	LOCK_ORDER,
	REWRITE_STEPS,
	ConfirmationPlan,
	OrchestrationError,
	Step,
	build_confirmation_plan,
	build_rewrite_plan,
	check_lock_order,
	existing_from_rows,
	is_retryable,
	lock_order_documentation,
	payload_hash,
	run_confirmation,
	run_rewrite,
)
from smartprocess_logistics.core.session import PREPARED, ImportSession
from smartprocess_logistics.core.validation import (
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
ALPHA = InvoiceRef(name="IN-260013", company="Alpha")
EDITOR = Principal(user="editor@example.com", roles=frozenset({ROLE_EDITOR}))
MANAGER = Principal(user="manager@example.com", roles=frozenset({ROLE_MANAGER}))
LIMITED = Principal(
	user="limited@example.com",
	roles=frozenset({ROLE_EDITOR}),
	companies=frozenset({"Beta"}),
)

ROW_1 = InvoiceRow(row_key="row-1", item_code="ITEM-1", invoiced_qty=2)
ROW_2 = InvoiceRow(row_key="row-2", item_code="ITEM-2", invoiced_qty=1)


@dataclass
class FakeGateway:
	"""A gateway that records what it was asked to do, and can be told to break."""

	invoice: InvoiceRef = ALPHA
	rows: tuple = (ROW_1, ROW_2)
	stored_session: ImportSession = None
	existing: tuple = ()
	fail_on: str = None
	session_gone: bool = False
	calls: list = field(default_factory=list)
	written: list = field(default_factory=list)
	deleted_rows: list = field(default_factory=list)
	applied: list = field(default_factory=list)
	serial_requests: tuple = ()

	def _record(self, name):
		self.calls.append(name)

		if self.fail_on == name:
			raise RuntimeError(f"the gateway was told to fail on {name}")

	def open_transaction(self):
		self._record("open_transaction")

	def lock_session(self, token):
		self._record("lock_session")

		return None if self.session_gone else self.stored_session

	def lock_invoice(self, name):
		self._record("lock_invoice")

		return self.invoice

	def read_rows(self, invoice_name):
		self._record("read_rows")

		return tuple(self.rows)

	def read_existing(self, invoice_name):
		self._record("read_existing")

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

	def mark_session_applied(self, name, applied_at, summary):
		self._record("mark_session_applied")
		self.applied.append((name, applied_at, summary))

		return True


def prepared_session(**overrides):
	values = {
		"name": "LIS-2026-0001",
		"token": "token-value",
		"status": PREPARED,
		"owner": "editor@example.com",
		"company": "Alpha",
		"sales_invoice": "IN-260013",
		"payload_hash": "hash-value",
		"expires_at": NOW + timedelta(minutes=10),
	}
	values.update(overrides)

	return ImportSession(**values)


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


class TestLockOrder(UnitTestCase):
	def test_the_documented_order_has_four_locks(self):
		self.assertEqual(len(LOCK_ORDER), 4)
		self.assertEqual(lock_order_documentation().count("->"), 3)

	def test_the_declared_plans_follow_the_order(self):
		self.assertTrue(check_lock_order(tuple(Step(kind) for kind in REWRITE_STEPS)))
		self.assertTrue(check_lock_order(tuple(Step(kind) for kind in CONFIRMATION_STEPS)))

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

	def test_the_confirmation_takes_the_session_lock_first(self):
		self.assertEqual(CONFIRMATION_STEPS[0], "lock_session")


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
				"read_existing",
				"lock_serials",
				"find_or_create_serials",
				"insert_assignments",
			],
		)
		self.assertEqual(gateway.deleted_rows, [])
		self.assertEqual(outcome.deleted, 0)

	def test_an_import_keeps_what_the_row_already_has(self):
		gateway = FakeGateway(
			existing=(ExistingAssignment(row_key="row-1", normalized_serial="a0"),)
		)
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
		gateway = FakeGateway(invoice=InvoiceRef(name=ALPHA.name, company="Beta"))

		with self.assertRaises(OrchestrationError) as caught:
			run_rewrite(rewrite(), gateway)

		self.assertEqual(caught.exception.code, CODE_COMPANY_CHANGED)
		self.assertEqual(gateway.written, [])

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
		self.assertNotIn("mark_session_applied", gateway.calls)

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
		self.assertEqual(
			sorted(written.normalized_serial for written in gateway.written), ["a1", "a9"]
		)


class TestRunConfirmation(UnitTestCase):
	def plan(self, gateway, *, owner=None, payload_hash="hash-value"):
		rewrite_plan = rewrite()

		return build_confirmation_plan(
			session=gateway.stored_session,
			rewrite=rewrite_plan,
			owner=owner or gateway.stored_session.owner,
			payload_hash=payload_hash,
			now=NOW,
		)

	def test_a_prepared_session_is_applied(self):
		gateway = FakeGateway(stored_session=prepared_session())
		outcome = run_confirmation(self.plan(gateway), gateway)

		self.assertTrue(outcome.accepted)
		self.assertEqual(gateway.applied[0][0], "LIS-2026-0001")
		self.assertEqual(gateway.applied[0][1], NOW)
		self.assertIn("mark_session_applied", gateway.calls)

	def test_the_session_is_locked_before_the_invoice(self):
		gateway = FakeGateway(stored_session=prepared_session())
		run_confirmation(self.plan(gateway), gateway)

		self.assertLess(gateway.calls.index("lock_session"), gateway.calls.index("lock_invoice"))

	def test_an_applied_session_cannot_be_confirmed_again(self):
		gateway = FakeGateway(
			stored_session=prepared_session(status=session_rules.APPLIED),
		)

		with self.assertRaises(session_rules.SessionError) as caught:
			run_confirmation(self.plan(gateway), gateway)

		self.assertEqual(caught.exception.code, session_rules.CODE_REPLAY)
		self.assertEqual(gateway.written, [])

	def test_an_expired_session_cannot_be_confirmed(self):
		gateway = FakeGateway(stored_session=prepared_session(expires_at=NOW))

		with self.assertRaises(session_rules.SessionError) as caught:
			run_confirmation(self.plan(gateway), gateway)

		self.assertEqual(caught.exception.code, session_rules.CODE_EXPIRED)
		self.assertEqual(gateway.written, [])

	def test_a_session_that_vanished_is_refused(self):
		gateway = FakeGateway(stored_session=prepared_session(), session_gone=True)

		with self.assertRaises(session_rules.SessionError) as caught:
			run_confirmation(self.plan(gateway), gateway)

		self.assertEqual(caught.exception.code, session_rules.CODE_NOT_FOUND)

	def test_another_users_session_is_refused(self):
		gateway = FakeGateway(stored_session=prepared_session(owner="someone@example.com"))

		with self.assertRaises(session_rules.SessionError) as caught:
			run_confirmation(self.plan(gateway, owner="editor@example.com"), gateway)

		self.assertEqual(caught.exception.code, session_rules.CODE_NOT_OWNED)
		self.assertEqual(gateway.written, [])

	def test_a_changed_payload_is_refused(self):
		# The hash the caller computed from the payload it just read does not match the hash the
		# session recorded, which is what catches a payload field edited after the fact.
		gateway = FakeGateway(stored_session=prepared_session(payload_hash="recorded-hash"))

		with self.assertRaises(session_rules.SessionError) as caught:
			run_confirmation(self.plan(gateway, payload_hash="recomputed-hash"), gateway)

		self.assertEqual(caught.exception.code, session_rules.CODE_PAYLOAD_CHANGED)

	def test_the_hash_is_computed_the_same_way_the_session_computes_it(self):
		encoded = '{"submissions": []}'

		self.assertEqual(payload_hash(encoded), payload_hash(encoded))
		self.assertNotEqual(payload_hash(encoded), payload_hash(encoded + " "))

	def test_nothing_is_marked_applied_when_the_rewrite_fails(self):
		gateway = FakeGateway(stored_session=prepared_session(), fail_on="insert_assignments")

		with self.assertRaises(RuntimeError):
			run_confirmation(self.plan(gateway), gateway)

		self.assertEqual(gateway.applied, [])

	def test_the_session_is_never_marked_claimed(self):
		# The claim lives in the transaction and is never written; that is why a stored
		# `Claimed` row cannot be produced by this design.
		gateway = FakeGateway(stored_session=prepared_session())
		run_confirmation(self.plan(gateway), gateway)

		self.assertNotIn("mark_session_claimed", gateway.calls)
		self.assertEqual(gateway.applied[0][0], "LIS-2026-0001")


class TestRetryRules(UnitTestCase):
	def test_a_validation_failure_is_not_retryable(self):
		self.assertFalse(is_retryable(OrchestrationError(CODE_PAYLOAD_CHANGED, "x")))
		self.assertFalse(is_retryable(OrchestrationError(CODE_NO_SUBMISSIONS, "x")))
		self.assertFalse(is_retryable(OrchestrationError(CODE_COMPANY_CHANGED, "x")))
		self.assertFalse(is_retryable(OrchestrationError(CODE_ROW_UNKNOWN, "x")))

	def test_a_contention_failure_is_retryable(self):
		self.assertTrue(is_retryable(OrchestrationError(CODE_INVOICE_CHANGED, "x")))
		self.assertTrue(is_retryable(TimeoutError("lock wait timeout")))

	def test_an_expired_session_is_retryable_and_a_replay_is_not(self):
		self.assertTrue(is_retryable(session_rules.SessionError(session_rules.CODE_EXPIRED, "x")))
		self.assertFalse(is_retryable(session_rules.SessionError(session_rules.CODE_REPLAY, "x")))

	def test_an_unknown_error_is_treated_as_retryable(self):
		self.assertTrue(is_retryable(RuntimeError("connection reset")))


class TestExistingFromRows(UnitTestCase):
	def test_stored_rows_become_existing_assignments(self):
		built = existing_from_rows(
			[
				{"sales_invoice_item": "row-1", "normalized_serial": "a1"},
				{"sales_invoice_item": "row-1", "normalized_serial": "a2"},
			]
		)

		self.assertEqual(len(built), 2)
		self.assertEqual(built[0].row_key, "row-1")
		self.assertEqual(built[1].normalized_serial, "a2")
