"""The import session lifecycle, and every reason a claim is refused."""

from datetime import datetime, timedelta

from smartprocess_logistics.core.session import (
	APPLIED,
	CLAIMED,
	CODE_ALREADY_CLAIMED,
	CODE_BAD_TRANSITION,
	CODE_EXPIRED,
	CODE_NOT_FOUND,
	CODE_NOT_OWNED,
	CODE_NOT_PREPARED,
	CODE_PAYLOAD_CHANGED,
	CODE_REPLAY,
	CODE_UNKNOWN_STATE,
	EXPIRED,
	FAILED,
	PREPARED,
	STATES,
	TERMINAL_STATES,
	TRANSITIONS,
	ImportSession,
	SessionError,
	apply,
	check_claimable,
	check_state,
	check_transition,
	claim,
	expire,
	fail,
	finished_at,
	is_expirable,
	is_purgeable,
	RETENTION_DAYS,
	select_expirable,
	select_purgeable,
)
from smartprocess_logistics.tests.base import UnitTestCase

NOW = datetime(2026, 10, 12, 18, 0)
LATER = NOW + timedelta(minutes=5)


def session(**overrides):
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


def claim_it(target, **overrides):
	arguments = {"user": "editor@example.com", "payload_hash": "hash-value", "now": NOW}
	arguments.update(overrides)

	return claim(target, **arguments)


class TestStateMachine(UnitTestCase):
	def test_the_five_states_are_known(self):
		self.assertEqual(set(STATES), {PREPARED, CLAIMED, APPLIED, FAILED, EXPIRED})

	def test_an_unknown_state_is_refused(self):
		with self.assertRaises(SessionError) as caught:
			check_state("Almost")

		self.assertEqual(caught.exception.code, CODE_UNKNOWN_STATE)

	def test_the_terminal_states_are_terminal(self):
		for state in TERMINAL_STATES:
			with self.subTest(state=state):
				self.assertEqual(TRANSITIONS[state], ())

	def test_every_declared_transition_is_legal(self):
		for source, targets in TRANSITIONS.items():
			for target in targets:
				with self.subTest(source=source, target=target):
					self.assertEqual(check_transition(source, target), target)

	def test_an_undeclared_transition_is_refused(self):
		for source, target in ((APPLIED, CLAIMED), (EXPIRED, CLAIMED), (PREPARED, APPLIED), (CLAIMED, CLAIMED)):
			with self.subTest(source=source, target=target):
				with self.assertRaises(SessionError) as caught:
					check_transition(source, target)

				self.assertEqual(caught.exception.code, CODE_BAD_TRANSITION)


class TestClaim(UnitTestCase):
	def test_a_prepared_session_is_claimed(self):
		claimed = claim_it(session())

		self.assertEqual(claimed.status, CLAIMED)
		self.assertEqual(claimed.claimed_at, NOW)

	def test_the_original_value_is_not_mutated(self):
		original = session()
		claim_it(original)

		self.assertEqual(original.status, PREPARED)
		self.assertIsNone(original.claimed_at)

	def test_a_missing_session_is_refused(self):
		with self.assertRaises(SessionError) as caught:
			claim_it(None)

		self.assertEqual(caught.exception.code, CODE_NOT_FOUND)

	def test_an_applied_session_is_a_replay(self):
		with self.assertRaises(SessionError) as caught:
			claim_it(session(status=APPLIED))

		self.assertEqual(caught.exception.code, CODE_REPLAY)

	def test_an_expired_session_is_refused(self):
		with self.assertRaises(SessionError) as caught:
			claim_it(session(expires_at=NOW))

		self.assertEqual(caught.exception.code, CODE_EXPIRED)

	def test_expiry_is_inclusive_at_the_instant(self):
		with self.assertRaises(SessionError):
			claim_it(session(expires_at=NOW))

	def test_a_session_with_no_expiry_never_expires(self):
		self.assertFalse(session(expires_at=None).is_expired(LATER))
		self.assertEqual(claim_it(session(expires_at=None)).status, CLAIMED)

	def test_a_string_now_is_not_a_valid_clock(self):
		"""The contract is `datetime`, and this is the check that pins it.

		The API passed `frappe.utils.now()` — a string — where the lifecycle expected a
		`datetime`, so every confirmation died on `str >= datetime` before it wrote anything.
		A hundred lines below, the same file already built `expires_at` with `now_datetime()`.
		"""
		with self.assertRaises(TypeError):
			session(expires_at=NOW).is_expired("2026-01-01 00:00:00")

	def test_an_already_claimed_session_is_refused(self):
		# This design never stores a claimed row, so finding one means somebody else wrote it.
		with self.assertRaises(SessionError) as caught:
			claim_it(session(status=CLAIMED))

		self.assertEqual(caught.exception.code, CODE_ALREADY_CLAIMED)

	def test_a_failed_session_is_not_claimable(self):
		with self.assertRaises(SessionError) as caught:
			claim_it(session(status=FAILED))

		self.assertEqual(caught.exception.code, CODE_NOT_PREPARED)

	def test_another_users_session_is_refused(self):
		with self.assertRaises(SessionError) as caught:
			claim_it(session(), user="someone@example.com")

		self.assertEqual(caught.exception.code, CODE_NOT_OWNED)

	def test_a_changed_payload_is_refused(self):
		with self.assertRaises(SessionError) as caught:
			claim_it(session(), payload_hash="another-hash")

		self.assertEqual(caught.exception.code, CODE_PAYLOAD_CHANGED)

	def test_a_session_in_a_used_state_is_refused_before_anything_is_written(self):
		for state, code in (
			(APPLIED, CODE_REPLAY),
			(CLAIMED, CODE_ALREADY_CLAIMED),
			(FAILED, CODE_NOT_PREPARED),
			(EXPIRED, CODE_NOT_PREPARED),
		):
			with self.subTest(state=state):
				with self.assertRaises(SessionError) as caught:
					check_claimable(
						session(status=state),
						user="editor@example.com",
						payload_hash="hash-value",
						now=NOW,
					)

				self.assertEqual(caught.exception.code, code)


class TestApplyAndFail(UnitTestCase):
	def test_a_claimed_session_is_applied(self):
		applied = apply(claim_it(session()), now=NOW, summary="done")

		self.assertEqual(applied.status, APPLIED)
		self.assertEqual(applied.applied_at, NOW)
		self.assertEqual(applied.result_summary, "done")
		self.assertTrue(applied.is_replay())

	def test_a_prepared_session_cannot_be_applied_directly(self):
		with self.assertRaises(SessionError) as caught:
			apply(session(), now=NOW)

		self.assertEqual(caught.exception.code, CODE_BAD_TRANSITION)

	def test_an_applied_session_cannot_be_applied_again(self):
		first = apply(claim_it(session()), now=NOW)

		with self.assertRaises(SessionError) as caught:
			apply(first, now=LATER)

		self.assertEqual(caught.exception.code, CODE_BAD_TRANSITION)

	def test_a_prepared_session_can_be_abandoned(self):
		failed = fail(session(), now=NOW, summary="abandoned")

		self.assertEqual(failed.status, FAILED)
		self.assertTrue(failed.is_terminal())

	def test_a_claimed_session_can_fail(self):
		self.assertEqual(fail(claim_it(session()), now=NOW).status, FAILED)

	def test_an_applied_session_cannot_fail(self):
		with self.assertRaises(SessionError):
			fail(apply(claim_it(session()), now=NOW), now=LATER)

	def test_a_prepared_session_can_expire(self):
		self.assertEqual(expire(session(), now=LATER).status, EXPIRED)

	def test_an_applied_session_cannot_expire(self):
		with self.assertRaises(SessionError):
			expire(apply(claim_it(session()), now=NOW), now=LATER)


class TestExpiry(UnitTestCase):
	def test_a_prepared_session_past_its_time_is_expirable(self):
		self.assertTrue(is_expirable(session(expires_at=NOW), LATER))

	def test_a_fresh_session_is_not_expirable(self):
		self.assertFalse(is_expirable(session(expires_at=LATER + timedelta(hours=1)), LATER))

	def test_a_terminal_session_is_never_expirable(self):
		for state in TERMINAL_STATES:
			with self.subTest(state=state):
				self.assertFalse(is_expirable(session(status=state, expires_at=NOW), LATER))

	def test_a_missing_session_is_not_expirable(self):
		self.assertFalse(is_expirable(None, LATER))


class TestCleanup(UnitTestCase):
	"""Which sessions a cleanup job may remove, and which it must leave.

	Two steps, and they are different questions. A preview nobody confirmed is **expired**: its
	row stays, because the summary a reader sees should say "expired" rather than "prepared for
	ever". A session that finished is **removed**, but not at once: the row is the only trace of
	what an import applied, and it carries the payload — every serial the preview accepted — so it
	is kept for `RETENTION_DAYS` and then taken in bounded batches.
	"""

	def test_a_prepared_session_is_expired_before_it_is_removable(self):
		stale = session(expires_at=NOW)

		self.assertTrue(is_expirable(stale, LATER))
		self.assertFalse(is_purgeable(stale, LATER))
		self.assertIsNone(finished_at(stale))

	def test_an_applied_session_older_than_the_retention_is_removable(self):
		applied = apply(claim_it(session()), now=NOW, summary="1 سریال ثبت شد.")
		old = NOW + timedelta(days=RETENTION_DAYS + 1)

		self.assertTrue(is_purgeable(applied, old))
		self.assertEqual(finished_at(applied), NOW)

	def test_an_applied_session_inside_the_retention_is_kept(self):
		applied = apply(claim_it(session()), now=NOW)

		self.assertFalse(is_purgeable(applied, NOW + timedelta(days=RETENTION_DAYS - 1)))

	def test_a_failed_session_is_removable_when_its_expiry_is_old(self):
		# A failure that was never applied is dated by the expiry that ended it.
		failed = session(status=FAILED, expires_at=NOW)

		self.assertTrue(is_purgeable(failed, NOW + timedelta(days=RETENTION_DAYS + 1)))

	def test_a_claimed_session_is_expirable_but_never_removable(self):
		# A claim that was abandoned — the process died between the claim and the write — is
		# expired by the job like any other session past its time, and the claim itself refuses an
		# expired one either way. It is never *removed*, because the row is the only record that a
		# write may have been attempted, and removing it would erase that.
		# It is built directly because the claim refuses an expired session, which is the state
		# this row is in only after a claim succeeded and time passed.
		claimed = session(status=CLAIMED, expires_at=NOW)

		self.assertTrue(is_expirable(claimed, NOW + timedelta(days=365)))
		self.assertFalse(is_purgeable(claimed, NOW + timedelta(days=365)))

	def test_the_batch_takes_the_oldest_first(self):
		rows = [
			apply(claim_it(session(name="LIS-3")), now=NOW + timedelta(days=2)),
			apply(claim_it(session(name="LIS-1")), now=NOW),
			apply(claim_it(session(name="LIS-2")), now=NOW + timedelta(days=1)),
		]
		long_after = NOW + timedelta(days=RETENTION_DAYS + 10)

		self.assertEqual(select_purgeable(rows, long_after, limit=2), ("LIS-1", "LIS-2"))
		self.assertEqual(select_purgeable(rows, long_after), ("LIS-1", "LIS-2", "LIS-3"))

	def test_the_batch_is_bounded(self):
		rows = [apply(claim_it(session(name=f"LIS-{index}")), now=NOW) for index in range(10)]
		long_after = NOW + timedelta(days=RETENTION_DAYS + 1)

		self.assertEqual(len(select_purgeable(rows, long_after, limit=3)), 3)

	def test_the_expire_step_is_bounded_and_ordered_by_expiry(self):
		rows = [
			session(name="LIS-late", expires_at=NOW + timedelta(minutes=5)),
			session(name="LIS-early", expires_at=NOW - timedelta(minutes=5)),
			session(name="LIS-fresh", expires_at=LATER + timedelta(hours=2)),
		]

		self.assertEqual(select_expirable(rows, LATER), ("LIS-early", "LIS-late"))
		self.assertEqual(select_expirable(rows, LATER, limit=1), ("LIS-early",))

	def test_an_empty_table_is_an_empty_batch(self):
		self.assertEqual(select_purgeable([], LATER), ())
		self.assertEqual(select_expirable([], LATER), ())
