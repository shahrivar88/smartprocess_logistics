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
	is_expirable,
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
