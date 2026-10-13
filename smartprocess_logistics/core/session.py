"""The import session lifecycle, and which transitions are legal.

A session carries one prepared preview from the moment it is built to the moment it is applied.
Its whole purpose is to make confirmation **atomic and single-use**: the preview an operator
sees is the payload that gets written, exactly once, by exactly one request.

	Prepared ──claim──> Claimed ──apply──> Applied
	   │                    │
	   │                    └──failure──> Failed
	   └──expiry───────────────────────> Expired

What is actually stored, and why
--------------------------------
Confirmation runs inside **one** transaction: the claim is written, the assignments are
written, and the transaction ends. There is no intermediate commit, which has three
consequences that are worth being explicit about:

- A stored `Claimed` row is never produced by this design. The state exists because the
  DocType lists it and because the claim is the transition the code makes in memory, but the
  row that the framework commits is `Applied` or nothing at all.
- A failure rolls the claim back with everything else, so the session is `Prepared` again.
  That is the right outcome: the payload did not change, so the operator can retry, and a
  deterministic validation error will simply fail the same way again.
- `Failed` is therefore **not** written by the failing confirmation. Writing it would need a
  second transaction after the rollback, and this app does not commit inside a business
  operation. `Failed` is reached by the explicit abandon path and by the cleanup job, which run
  in their own requests and commit normally.

That is also why a `Claimed` row found in the database is treated as a state this design does
not produce: it blocks the claim, and an operator has to expire it. Guessing that it is stale
would be guessing about a write that, by construction, this code never made.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

PREPARED = "Prepared"
CLAIMED = "Claimed"
APPLIED = "Applied"
FAILED = "Failed"
EXPIRED = "Expired"

STATES = (PREPARED, CLAIMED, APPLIED, FAILED, EXPIRED)

TERMINAL_STATES = (APPLIED, FAILED, EXPIRED)

# The two states something can still happen from, and therefore the only two a cleanup job is
# allowed to expire. Writing "Expired" over a session that was applied while the cleanup was
# reading the table would replace the record of an import that did happen with a statement that
# it did not; the states are therefore part of the update's own condition.
OPEN_STATES = (PREPARED, CLAIMED)

TRANSITIONS = {
	PREPARED: (CLAIMED, FAILED, EXPIRED),
	CLAIMED: (APPLIED, FAILED),
	APPLIED: (),
	FAILED: (),
	EXPIRED: (),
}

CODE_UNKNOWN_STATE = "SESSION_UNKNOWN_STATE"
CODE_NOT_FOUND = "SESSION_NOT_FOUND"
CODE_NOT_PREPARED = "SESSION_NOT_PREPARED"
CODE_EXPIRED = "SESSION_EXPIRED"
CODE_REPLAY = "SESSION_ALREADY_APPLIED"
CODE_ALREADY_CLAIMED = "SESSION_ALREADY_CLAIMED"
CODE_BAD_TRANSITION = "SESSION_INVALID_TRANSITION"
CODE_PAYLOAD_CHANGED = "SESSION_PAYLOAD_CHANGED"
CODE_NOT_OWNED = "SESSION_NOT_OWNED"


class SessionError(ValueError):
	"""A session that cannot be used as asked. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class ImportSession:
	"""A prepared preview, and everything needed to confirm it once.

	`payload_hash` is the hash of the payload the session was built from. Confirmation compares
	it, so a session whose rows changed underneath cannot be applied with the old preview.
	"""

	name: str
	token: str
	status: str = PREPARED
	owner: str = ""
	company: str = ""
	sales_invoice: str = ""
	payload_hash: str = ""
	expires_at: datetime | None = None
	claimed_at: datetime | None = None
	applied_at: datetime | None = None
	result_summary: str = ""

	def is_expired(self, now):
		"""Whether the session has passed its expiry instant."""
		if self.expires_at is None:
			return False

		return now >= self.expires_at

	def is_terminal(self):
		return self.status in TERMINAL_STATES

	def is_replay(self):
		"""Whether this session has already been applied."""
		return self.status == APPLIED


def check_state(state):
	"""Refuse a status that is not one of the five."""
	if state not in STATES:
		raise SessionError(CODE_UNKNOWN_STATE, f"وضعیت ناشناختهٔ نشست: {state}")

	return state


def check_transition(current, target):
	"""Refuse a transition the lifecycle does not allow."""
	check_state(current)
	check_state(target)

	if target not in TRANSITIONS[current]:
		raise SessionError(
			CODE_BAD_TRANSITION,
			f"گذار از «{current}» به «{target}» مجاز نیست.",
		)

	return target


def check_claimable(session, *, user, payload_hash, now):
	"""Every reason a session may not be claimed, in the order a caller should hear them."""
	if session is None:
		raise SessionError(CODE_NOT_FOUND, "نشست پیش‌نمایش یافت نشد.")

	check_state(session.status)

	if session.is_replay():
		raise SessionError(CODE_REPLAY, "این پیش‌نمایش قبلاً اعمال شده است.")

	if session.is_expired(now):
		raise SessionError(CODE_EXPIRED, "اعتبار این پیش‌نمایش به پایان رسیده است.")

	if session.status == CLAIMED:
		raise SessionError(
			CODE_ALREADY_CLAIMED,
			"این پیش‌نمایش در حال اعمال است. اگر ادامه نیافت، آن را باطل کنید.",
		)

	if session.status != PREPARED:
		raise SessionError(CODE_NOT_PREPARED, f"نشست در وضعیت «{session.status}» قابل اعمال نیست.")

	if user != session.owner:
		raise SessionError(CODE_NOT_OWNED, "این پیش‌نمایش را کاربر دیگری ساخته است.")

	if payload_hash != session.payload_hash:
		raise SessionError(CODE_PAYLOAD_CHANGED, "محتوای پیش‌نمایش تغییر کرده است.")

	return True


def claim(session, *, user, payload_hash, now):
	"""Move a `Prepared` session to `Claimed`, or explain why not."""
	check_claimable(session, user=user, payload_hash=payload_hash, now=now)
	check_transition(session.status, CLAIMED)

	return replace(session, status=CLAIMED, claimed_at=now)


def apply(session, *, now, summary=""):
	"""Move a claimed session to `Applied`."""
	check_transition(session.status, APPLIED)

	return replace(session, status=APPLIED, applied_at=now, result_summary=summary)


def fail(session, *, now, summary=""):
	"""Move a session to `Failed`.

	Called by the abandon path and by the cleanup job, never by a confirmation that has just
	rolled back: that write would roll back with it, and the session is better left `Prepared`
	so the operator can retry.
	"""
	check_transition(session.status, FAILED)

	return replace(session, status=FAILED, result_summary=summary)


def expire(session, *, now, summary=""):
	"""Move a session to `Expired`."""
	check_transition(session.status, EXPIRED)

	return replace(session, status=EXPIRED, result_summary=summary)


def is_expirable(session, now):
	"""Whether a cleanup job should expire this session."""
	if session is None or session.is_terminal():
		return False

	return session.is_expired(now)


# How long a finished session's record is kept before a cleanup job may remove it. The row is the
# only trace of what an import applied — its status, when, and a one-line summary — and it also
# carries the payload, which is every serial the preview accepted. A month is long enough to
# answer "what happened to that file" and short enough that the payloads do not accumulate for
# ever. Not a `Logistics Settings` field: it bounds housekeeping rather than expressing a
# decision an operator makes, and a value nothing reads is a value that goes stale — which is
# what `normalization_version` was.
RETENTION_DAYS = 30

# How many rows one run of the cleanup job may touch, in each of its two steps. Bounded so a site
# that accumulated thousands of previews is cleaned over several runs rather than in one long
# transaction, and so an interrupted run leaves only work it had not started.
PURGE_BATCH = 200

CODE_NOT_TERMINAL = "SESSION_NOT_TERMINAL"


def finished_at(session):
	"""When a session stopped being able to do anything, or `None` while it still can.

	A session that was applied is dated by its apply, and one that expired or failed by the
	expiry that ended it. A `Prepared` one has not finished, whatever its expiry says: expiring it
	is the step before this one.
	"""
	if session is None or not session.is_terminal():
		return None

	return session.applied_at or session.claimed_at or session.expires_at


def is_purgeable(session, now, retention_days=RETENTION_DAYS):
	"""Whether a cleanup job may remove this session's record."""
	finished = finished_at(session)

	if finished is None:
		return False

	return finished <= now - timedelta(days=retention_days)


def select_purgeable(sessions, now, limit=PURGE_BATCH, retention_days=RETENTION_DAYS):
	"""Up to `limit` sessions a cleanup job may remove, oldest first.

	Oldest first makes a bound an order as well as a ceiling: run after run removes the same
	queue from its head, so a site that fell behind catches up instead of removing whichever rows
	the database happened to return.
	"""
	purgeable = [
		session
		for session in sessions
		if is_purgeable(session, now, retention_days=retention_days)
	]

	purgeable.sort(key=lambda session: (finished_at(session), session.name))

	return tuple(session.name for session in purgeable[:limit])


def select_expirable(sessions, now, limit=PURGE_BATCH):
	"""Up to `limit` sessions a cleanup job should mark expired, oldest expiry first."""
	expirable = [session for session in sessions if is_expirable(session, now)]
	expirable.sort(key=lambda session: (session.expires_at, session.name))

	return tuple(session.name for session in expirable[:limit])
