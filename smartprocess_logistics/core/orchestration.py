"""The order a write performs its work in, and the locks it takes.

This module is the transaction plan. It decides *what* happens and *in what order*; a gateway
decides *how*. Nothing here imports Frappe, so every plan and every failure mode runs against a
fake without a database.

Two entry points, one rewrite
-----------------------------
`set_row_serials` rewrites the serials of one invoice row. `confirm_import` rewrites the rows a
prepared file covers, and wraps that rewrite in a session claim so the file is applied exactly
once. Both end in the same work — reconcile, delete, find or create serials, insert — so that
work is written once, in `run_rewrite`, and the session is the only thing layered on top.

Why the lock order is fixed
---------------------------
Two requests that take the same two row locks in opposite orders deadlock. The order is
therefore global, and `check_lock_order` refuses a plan that does not follow it:

	logistics_import_session -> sales_invoice -> logistics_serial -> logistics_serial_assignment

Assignment rows are locked last because they are the rows being written; holding them across
the invoice re-read would buy nothing and widen the window.

Why there is no commit
----------------------
A write is **one** transaction, and the framework ends it when the request ends:

- A failure rolls the claim back with everything else, so a session is `Prepared` again and the
  operator can retry. No half-applied import is possible, and no session is left claimed by an
  operation that never finished.
- A second request for the same session blocks on the session row lock. Whichever loses sees
  `Applied` and is refused. That is the replay defence, and it comes from the lock, not from a
  token in a cache.
- Nothing here calls `commit`. A commit inside a business operation would break exactly the
  atomicity the plan exists to provide.

Retry rules, because a caller has to know them:

- A **validation failure is deterministic**. The payload is fixed and the invoice did not
  change, so the same request fails the same way. Retrying without a different file is
  pointless, and the message says so.
- A **lock wait timeout** or a **connection loss** is retryable: the transaction was rolled back
  whole and the session is `Prepared` again.
- An **applied session** is not retryable. The work is done and a new preview is needed.
"""

from dataclasses import dataclass, field

from smartprocess_logistics.core import session as session_rules
from smartprocess_logistics.core.assignment import derive_assignment_values
from smartprocess_logistics.core.authz import InvoiceRef, require_invoice_access
from smartprocess_logistics.core.validation import ExistingAssignment, plan_registration

LOCK_ORDER = (
	"logistics_import_session",
	"sales_invoice",
	"logistics_serial",
	"logistics_serial_assignment",
)

LOCK_SESSION = "lock_session"
CHECK_SESSION = "check_session"
LOCK_INVOICE = "lock_invoice"
RELOCK_SERIALS = "relock_serials"
REVALIDATE = "revalidate"
DELETE_ROW_ASSIGNMENTS = "delete_row_assignments"
ENSURE_SERIALS = "ensure_serials"
INSERT_ASSIGNMENTS = "insert_assignments"
MARK_APPLIED = "mark_applied"

REWRITE_STEPS = (
	LOCK_INVOICE,
	RELOCK_SERIALS,
	REVALIDATE,
	DELETE_ROW_ASSIGNMENTS,
	ENSURE_SERIALS,
	INSERT_ASSIGNMENTS,
)

CONFIRMATION_STEPS = (LOCK_SESSION, CHECK_SESSION, *REWRITE_STEPS, MARK_APPLIED)

STEP_LOCKS = {
	LOCK_SESSION: LOCK_ORDER[0],
	LOCK_INVOICE: LOCK_ORDER[1],
	RELOCK_SERIALS: LOCK_ORDER[2],
	DELETE_ROW_ASSIGNMENTS: LOCK_ORDER[3],
	INSERT_ASSIGNMENTS: LOCK_ORDER[3],
}

REWRITE_ENDPOINT = "set_row_serials"
CONFIRMATION_ENDPOINT = "confirm_import"

GATEWAY_METHODS = (
	"open_transaction",
	"lock_invoice",
	"read_rows",
	"read_existing",
	"lock_serials",
	"delete_row_assignments",
	"find_or_create_serials",
	"insert_assignments",
)

CONFIRMATION_GATEWAY_METHODS = (
	*GATEWAY_METHODS,
	"lock_session",
	"mark_session_applied",
)

CODE_BAD_STEP = "PLAN_UNKNOWN_STEP"
CODE_BAD_ORDER = "PLAN_BAD_LOCK_ORDER"
CODE_INVOICE_CHANGED = "PLAN_INVOICE_CHANGED"
CODE_COMPANY_CHANGED = "PLAN_COMPANY_CHANGED"
CODE_PAYLOAD_CHANGED = "PLAN_PAYLOAD_CHANGED"
CODE_NO_SUBMISSIONS = "PLAN_NOTHING_TO_APPLY"
CODE_ROW_UNKNOWN = "PLAN_ROW_UNKNOWN"
CODE_GATEWAY_MISSING = "PLAN_GATEWAY_INCOMPLETE"


class OrchestrationError(ValueError):
	"""A plan that cannot be built or run. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class Step:
	"""One unit of work."""

	kind: str

	def __str__(self):
		return self.kind


@dataclass(frozen=True)
class RewritePlan:
	"""Everything a write needs, checked as far as it can be before a lock is taken."""

	invoice_name: str
	company: str
	rows: tuple
	existing: tuple
	submissions: tuple
	delimiters: tuple = ()
	policy: object = None
	allow_over_quantity: bool = False
	only_rows: tuple = ()

	def row_keys(self):
		return tuple(row.row_key for row in self.rows)


@dataclass(frozen=True)
class ConfirmationPlan:
	"""A rewrite plus the session claim that makes it happen exactly once.

	`owner` and `payload_hash` are the **caller's** claim about the session, not the session's
	own values. Copying them out of the stored row would make both checks compare a value with
	itself, which is the shape of a check that never fails.
	"""

	rewrite: RewritePlan
	steps: tuple
	session_name: str
	token: str
	payload_hash: str
	owner: str
	now: object = None

	def kinds(self):
		return tuple(step.kind for step in self.steps)


@dataclass(frozen=True)
class WriteOutcome:
	"""What a completed write did."""

	accepted: int
	deleted: int
	created_serials: tuple
	serials_reused: int
	rewrite_rows: tuple
	summary: str
	steps: tuple = field(default=())

	@property
	def touched_serials(self):
		return len(self.created_serials) + self.serials_reused


def check_lock_order(steps):
	"""Refuse a plan whose locks are not taken in the one global order."""
	seen = []

	for step in steps:
		if step.kind not in STEP_LOCKS and step.kind not in (
			CHECK_SESSION,
			REVALIDATE,
			ENSURE_SERIALS,
			MARK_APPLIED,
		):
			raise OrchestrationError(CODE_BAD_STEP, f"مرحلهٔ ناشناخته: {step.kind}")

		lock = STEP_LOCKS.get(step.kind)

		if lock is not None and lock not in seen:
			seen.append(lock)

	positions = [LOCK_ORDER.index(lock) for lock in seen]

	if positions != sorted(positions):
		raise OrchestrationError(
			CODE_BAD_ORDER,
			"ترتیب قفل‌گیری با ترتیب سراسری یکسان نیست: " + "، ".join(seen),
		)

	return True


def build_rewrite_plan(
	*,
	invoice,
	rows,
	existing,
	submissions,
	principal,
	only_rows=(),
	delimiters=(),
	policy=None,
	allow_over_quantity=False,
):
	"""Check what can be checked before a lock is taken, and lay out the rewrite."""
	if invoice is None or not isinstance(invoice, InvoiceRef):
		raise OrchestrationError(CODE_INVOICE_CHANGED, "فاکتور یافت نشد.")

	require_invoice_access(principal, REWRITE_ENDPOINT, invoice)

	submissions = tuple(submissions)

	if not submissions and not only_rows:
		raise OrchestrationError(CODE_NO_SUBMISSIONS, "چیزی برای اعمال وجود ندارد.")

	known_rows = {row.row_key for row in rows}
	unknown = sorted({row_key for row_key, _serial in submissions if row_key not in known_rows})

	if unknown:
		raise OrchestrationError(CODE_ROW_UNKNOWN, "ردیف فاکتور پیدا نشد: " + "، ".join(unknown))

	steps = tuple(Step(kind) for kind in REWRITE_STEPS)
	check_lock_order(steps)

	return RewritePlan(
		invoice_name=invoice.name,
		company=invoice.company,
		rows=tuple(rows),
		existing=tuple(existing),
		submissions=submissions,
		delimiters=tuple(delimiters),
		policy=policy,
		allow_over_quantity=allow_over_quantity,
		only_rows=tuple(only_rows),
	)


def payload_hash(payload):
	"""The hash a session stores for its payload, computed the one way it is ever computed.

	Confirmation recomputes this from the payload it just read and passes it in, so a `payload`
	field that was edited after the session was written no longer matches its recorded hash and
	the claim is refused.
	"""
	import hashlib

	return hashlib.sha256(payload.encode()).hexdigest()


def build_confirmation_plan(*, session, rewrite, owner, payload_hash, now=None):
	"""Wrap a rewrite plan in the session claim that makes it single-use.

	`owner` is the acting user and `payload_hash` is the hash of the payload that was just read
	back, so both checks compare the caller's claim against the session rather than the session
	against itself.
	"""
	steps = tuple(Step(kind) for kind in CONFIRMATION_STEPS)
	check_lock_order(steps)

	return ConfirmationPlan(
		rewrite=rewrite,
		steps=steps,
		session_name=session.name,
		token=session.token,
		payload_hash=payload_hash,
		owner=owner,
		now=now,
	)


def _require(gateway, names):
	"""Refuse a gateway that cannot finish, before it has done half the work."""
	missing = [name for name in names if not callable(getattr(gateway, name, None))]

	if missing:
		raise OrchestrationError(
			CODE_GATEWAY_MISSING,
			"دروازهٔ داده این متدها را ندارد: " + "، ".join(missing),
		)


def run_rewrite(plan, gateway):
	"""Execute a rewrite against a gateway, in one transaction, in the fixed lock order."""
	_require(gateway, GATEWAY_METHODS)
	gateway.open_transaction()

	locked = gateway.lock_invoice(plan.invoice_name)

	if locked is None:
		raise OrchestrationError(CODE_INVOICE_CHANGED, "فاکتور یافت نشد.")

	if getattr(locked, "company", None) != plan.company:
		raise OrchestrationError(CODE_COMPANY_CHANGED, "شرکت فاکتور تغییر کرده است.")

	# The rows are read again, under the lock. The preview's copy is not trusted, because the
	# invoice may have been edited between the preview and the write.
	rows = tuple(gateway.read_rows(plan.invoice_name))
	existing = tuple(gateway.read_existing(plan.invoice_name))

	_verify_rows_unchanged(plan, rows)

	gateway.lock_serials(tuple(sorted({row.item_code for row in rows})))

	result = plan_registration(
		rows=rows,
		submissions=plan.submissions,
		existing=existing,
		delimiters=plan.delimiters,
		policy=plan.policy,
		allow_over_quantity=plan.allow_over_quantity,
	)

	if not result.is_valid:
		raise OrchestrationError(
			CODE_PAYLOAD_CHANGED,
			"; ".join(issue.message for issue in result.errors),
		)

	rewrite_rows = plan.only_rows or tuple(sorted({item.row_key for item in result.accepted}))
	deleted = 0

	for row_key in rewrite_rows:
		deleted += gateway.delete_row_assignments(plan.invoice_name, row_key)

	requests = tuple(
		(item.item_code, item.serial.normalized, item.serial.original) for item in result.accepted
	)
	serials = gateway.find_or_create_serials(requests)
	by_key = serials.get("by_normalized", {})

	values = []

	for item in result.accepted:
		serial_name = by_key.get((item.item_code, item.serial.normalized))

		if not serial_name:
			raise OrchestrationError(
				CODE_PAYLOAD_CHANGED,
				f"سریال ساخته‌نشده برای کالای {item.item_code}",
			)

		values.append(
			derive_assignment_values(
				serial=item.serial,
				serial_name=serial_name,
				item_code=item.item_code,
				sales_invoice=plan.invoice_name,
				sales_invoice_item=item.row_key,
				company=plan.company,
			)
		)

	gateway.insert_assignments(tuple(values))

	return WriteOutcome(
		accepted=len(values),
		deleted=deleted,
		created_serials=tuple(serials.get("created", ())),
		serials_reused=serials.get("reused", 0),
		rewrite_rows=rewrite_rows,
		summary=(
			f"{len(values)} سریال ثبت شد و {deleted} ارجاع پیشین بازنویسی شد."
			if deleted
			else f"{len(values)} سریال ثبت شد."
		),
		steps=tuple(REWRITE_STEPS),
	)


def _verify_rows_unchanged(plan, rows):
	"""Refuse a write whose invoice rows are not the ones the preview described.

	The row names are compared, not the quantities. A row that was deleted and re-added has a
	new name, and an assignment to the old name would be orphaned the moment it was written.
	"""
	expected = set(plan.row_keys())
	actual = {row.row_key for row in rows}

	lost = sorted(expected - actual)

	if lost:
		raise OrchestrationError(
			CODE_INVOICE_CHANGED,
			"ردیف‌های فاکتور از زمان پیش‌نمایش تغییر کرده‌اند: " + "، ".join(lost),
		)

	return True


def run_confirmation(plan, gateway):
	"""Execute a confirmation: claim the session, then rewrite, in one transaction."""
	_require(gateway, CONFIRMATION_GATEWAY_METHODS)
	gateway.open_transaction()

	stored = gateway.lock_session(plan.token)

	# The claim is checked here and **not written**. It happens under the session row lock, so
	# a concurrent request is blocked until this transaction ends and then reads what it
	# committed: `Applied` if this one succeeded, `Prepared` if it rolled back. Writing a
	# `Claimed` row would need a second transaction to survive, and a transaction this app does
	# not open. That is exactly why the lifecycle says a stored `Claimed` row is never produced.
	claimed = session_rules.claim(
		stored,
		user=plan.owner,
		payload_hash=plan.payload_hash,
		now=plan.now,
	)

	outcome = run_rewrite(plan.rewrite, gateway)

	applied = session_rules.apply(claimed, now=plan.now, summary=outcome.summary)
	gateway.mark_session_applied(applied.name, applied.applied_at, applied.result_summary)

	return WriteOutcome(
		accepted=outcome.accepted,
		deleted=outcome.deleted,
		created_serials=outcome.created_serials,
		serials_reused=outcome.serials_reused,
		rewrite_rows=outcome.rewrite_rows,
		summary=outcome.summary,
		steps=plan.kinds(),
	)


def is_retryable(error):
	"""Whether a caller may send the same request again."""
	if isinstance(error, OrchestrationError):
		return error.code not in (
			CODE_PAYLOAD_CHANGED,
			CODE_NO_SUBMISSIONS,
			CODE_COMPANY_CHANGED,
			CODE_ROW_UNKNOWN,
		)

	if isinstance(error, session_rules.SessionError):
		return error.code in (session_rules.CODE_EXPIRED,)

	return True


def lock_order_documentation():
	"""The lock order, for a log line or a report."""
	return " -> ".join(LOCK_ORDER)


def existing_from_rows(rows):
	"""Build `ExistingAssignment` values from assignment rows a gateway returned."""
	return tuple(
		ExistingAssignment(
			row_key=row["sales_invoice_item"],
			normalized_serial=row["normalized_serial"],
		)
		for row in rows
	)
