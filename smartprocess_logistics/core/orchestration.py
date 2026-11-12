"""The order a write performs its work in, and the locks it takes.

This module is the transaction plan. It decides *what* happens and *in what order*; a gateway
decides *how*. Nothing here imports Frappe, so every plan and every failure mode runs against a
fake without a database.

Two entry points, one rewrite
-----------------------------
`set_row_serials` rewrites the serials of one invoice row, and `add_row_serials` adds to one
without replacing anything. Both end in the same work — reconcile, delete, find or create
serials, insert — so that work is written once, in `run_rewrite`.

A third entry point used to sit beside them: `confirm_import` rewrote the rows a stored preview
covered, wrapped in a session claim so a file was applied exactly once. Nothing prepared a
session any more — the window stages what it reads and `add_row_serials` writes it — so the
endpoint, its claim, and the two plans it needed were removed rather than left reachable as a
second way into these tables.

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

from smartprocess_logistics.core import authz
from smartprocess_logistics.core import delivery as delivery_rules
from smartprocess_logistics.core import session as session_rules
from smartprocess_logistics.core.assignment import derive_assignment_values
from smartprocess_logistics.core.authz import InvoiceRef, require_invoice_access
from smartprocess_logistics.core.validation import (
	CODE_DELIVERY_CHOICE_REQUIRED,
	CODE_DELIVERY_NOT_A_CANDIDATE,
	ExistingAssignment,
	plan_registration,
)

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


STEP_LOCKS = {
	LOCK_SESSION: LOCK_ORDER[0],
	LOCK_INVOICE: LOCK_ORDER[1],
	RELOCK_SERIALS: LOCK_ORDER[2],
	DELETE_ROW_ASSIGNMENTS: LOCK_ORDER[3],
	INSERT_ASSIGNMENTS: LOCK_ORDER[3],
}

REWRITE_ENDPOINT = "set_row_serials"

GATEWAY_METHODS = (
	"open_transaction",
	"lock_invoice",
	"read_rows",
	"read_existing",
	"delivery_candidates",
	"delivery_links",
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

# How many serials a refusal names before it stops counting them out.
NAMED_SERIALS = 10


def refusal_message(errors):
	"""One message for a refused write, naming the serials each issue is about.

	A batch is refused whole, so "one of these twenty is already registered" leaves the operator
	to guess which one to remove. The validator already recorded the serials of each issue, so
	they are named here — up to `NAMED_SERIALS`, then a count — because a wall of five hundred
	names hides the number that matters.
	"""
	lines = []

	for issue in errors:
		named = tuple(issue.serials)

		if not named:
			lines.append(issue.message)
			continue

		shown = "، ".join(named[:NAMED_SERIALS])
		rest = len(named) - NAMED_SERIALS
		suffix = f" و {rest} مورد دیگر" if rest > 0 else ""

		lines.append(f"{issue.message} ({shown}{suffix})")

	return "; ".join(lines)


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
	"""Everything a write needs, checked as far as it can be before a lock is taken.

	`selections` pairs a row key with the delivery note an operator named for it. It is the
	caller's request and nothing else: the delivery rows themselves are **not** on the plan,
	because they are read inside the transaction. A plan built from a stale read would decide a
	row's delivery from a document that has since been cancelled or amended, and the decision
	would look as trustworthy as one made under the lock.
	"""

	invoice_name: str
	company: str
	rows: tuple
	existing: tuple
	submissions: tuple
	delimiters: tuple = ()
	policy: object = None
	only_rows: tuple = ()
	selections: tuple = ()

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
	selections=(),
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

	# A selection names the delivery an operator chose for one row. A selection for a row this
	# invoice does not have cannot be honoured, and dropping it silently would let the request
	# appear to have been obeyed while the serials went to a different row's delivery.
	selections = tuple(selections)
	unknown_selections = sorted({row_key for row_key, _note in selections if row_key not in known_rows})

	if unknown_selections:
		raise OrchestrationError(
			CODE_ROW_UNKNOWN,
			"ردیف فاکتور پیدا نشد: " + "، ".join(unknown_selections),
		)

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
		only_rows=tuple(only_rows),
		selections=selections,
	)


def build_append_plan(
	*,
	invoice,
	rows,
	existing,
	row_key,
	serials,
	principal,
	policy=None,
	delivery_note=None,
):
	"""Serials added to one row, keeping everything else that row already holds.

	The difference from `build_rewrite_plan` is one argument, and it is the whole reason a scan
	station can be safe. `set_row_serials` names the row it rewrites, so the row's stored set is
	counted out and replaced: two operators registering into the same row at the same time each
	send the list they believe in, and the later one silently drops the earlier one's serials.

	An append names no replaced row. `replaced_rows` stays empty, nothing the row already holds
	is deleted, and a serial that is already there is caught by the validator rather than by
	overwriting it. That is the same shape an import uses, stated for one row — and the serials
	are added under the invoice lock `run_rewrite` takes, so the two operators' serials both
	survive.

	One serial and twenty are the same rule, so a batch is the same call with a longer tuple
	rather than a second function that has to be kept in step with this one. The batch is checked
	as a whole *and* written as a whole: a list with a duplicate in it is refused entire, and a
	list that is accepted is inserted in one transaction, so a row cannot end up holding the
	first half of what an operator asked for.

	The decision lives here rather than in the endpoint because it is a decision: what "add"
	means for the row's existing registrations is a rule, and a rule in the endpoint is a rule
	no test can reach.
	"""
	serials = tuple(serials)

	# `delivery_note` is the operator's answer to "which shipment did these leave with?", asked
	# only when the row has more than one live delivery. It is carried as a selection rather than
	# resolved here: the delivery rows are read under the lock, and choosing from a list read
	# before the transaction is how a serial ends up attached to a delivery that changed.
	selections = ((row_key, delivery_note),) if delivery_note else ()

	return build_rewrite_plan(
		invoice=invoice,
		rows=rows,
		existing=existing,
		submissions=tuple((row_key, serial) for serial in serials),
		principal=principal,
		only_rows=(),
		delimiters=(),
		policy=policy,
		selections=selections,
	)


def payload_hash(payload):
	"""The hash a session stores for its payload, computed the one way it is ever computed.

	Confirmation recomputes this from the payload it just read and passes it in, so a `payload`
	field that was edited after the session was written no longer matches its recorded hash and
	the claim is refused.
	"""
	import hashlib

	return hashlib.sha256(payload.encode()).hexdigest()


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

	# The lifecycle, read under the same lock and for the same reason the company is: the check
	# the endpoint made was against the invoice as it was a moment ago, and an invoice cancelled
	# in between must not receive a registration. `docstatus` comes off the locked read, so this
	# is the site's answer now rather than the answer the request arrived with.
	if getattr(locked, "docstatus", 0) == authz.DOCSTATUS_CANCELLED:
		raise OrchestrationError(CODE_INVOICE_CHANGED, authz.REFUSAL_INVOICE_CANCELLED)

	# The rows are read again, under the lock. The preview's copy is not trusted, because the
	# invoice may have been edited between the preview and the write.
	rows = tuple(gateway.read_rows(plan.invoice_name))

	_verify_rows_unchanged(plan, rows)

	gateway.lock_serials(tuple(sorted({row.item_code for row in rows})))

	# The stored assignments are read here, after the invoice and the serials are locked, and as a
	# **locking** read — because this is the count the capacity rule decides on, and the rule has
	# to be decided on what the invoice holds now. A plain read here is a consistent read, and
	# under `REPEATABLE-READ` that answers with the view this transaction had when it first
	# looked, which is how a caller that read a full row's third place, waited, and then wrote was
	# measured **passing** the cap and being stopped only by an unrelated deadlock afterwards
	# (`deployment/lab/verify_scan_station.py`, the `stale_round` check). The lock order above is
	# the plan's own — invoice, serial, assignment — so reading it here takes no new lock out of
	# turn.
	existing = tuple(gateway.read_existing(plan.invoice_name, for_update=True))

	# The delivery rows that carry this invoice's rows, read here for the same reason the rows
	# above are: the link a serial is written with has to be decided on the documents as they
	# are now. Every row of the invoice is read, not only the ones being written, because the
	# validator refuses a mapping that skipped a row — and that refusal is deliberate. A row
	# whose delivery was never looked up must not be read as "no delivery"; that would mark a
	# linked shipment as provisional, which is the one way this rule may never fail.
	deliveries = gateway.delivery_candidates(
		plan.invoice_name,
		tuple(row.row_key for row in rows),
	)

	# The rows whose whole set is written again, and therefore the rows whose stored assignments
	# the validator must count out: the ones a caller named as replaced. `set_row_serials` names
	# one row, an import names none — an import adds to a row that may already hold serials the
	# file never mentions, and deleting those first discarded registrations nobody asked to
	# remove. Adding is the import's job; removing is `delete_assignments`, or `set_row_serials`
	# with a shorter list. Both the validator and the delete loop below read this one value, so
	# the check and the deletion cannot drift apart.
	rewrite_rows = tuple(plan.only_rows)

	result = plan_registration(
		rows=rows,
		submissions=plan.submissions,
		existing=existing,
		replaced_rows=rewrite_rows,
		delimiters=plan.delimiters,
		policy=plan.policy,
		deliveries=deliveries,
		selections=dict(plan.selections),
	)

	if not result.is_valid:
		errors = tuple(result.errors)

		# The validator's own codes are the ones a caller can act on — DUPLICATE_IN_INVOICE,
		# EXCESS_QUANTITY, and so on. Reporting PLAN_PAYLOAD_CHANGED for every one of them told
		# three different failures apart in the message and identically in the code, which is the
		# half of the error a client switches on. The first error's code stands for the response;
		# the message still lists all of them.
		raise OrchestrationError(
			errors[0].code if errors else CODE_PAYLOAD_CHANGED,
			refusal_message(errors),
		)

	deleted = 0

	for row_key in rewrite_rows:
		deleted += gateway.delete_row_assignments(plan.invoice_name, row_key)

	_check_delivery_conflicts(plan, result, gateway)

	# The rule set that produced each identity is carried with it, not looked up. A serial that
	# is created records the version that normalized *it*, so a later change of rules leaves the
	# earlier serials described by the version they were actually built with. Reading the current
	# constant at insert time would relabel every old serial as new on the first import after a
	# rule change, which is the one thing the field exists to prevent.
	requests = tuple(
		(item.item_code, item.serial.normalized, item.serial.original, item.serial.version)
		for item in result.accepted
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
				delivery_note=item.delivery_note,
				delivery_note_item=item.delivery_note_item,
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


def _check_delivery_conflicts(plan, result, gateway):
	"""Refuse a serial whose chosen delivery is already open in another shipment.

	Run after the rows being rewritten have been cleared and before anything is written, so the
	registrations compared against are exactly the ones that will remain: the row's own previous
	registration cannot conflict with itself, and it is gone either way.

	Only serials that resolved to a delivery are examined. A provisional registration names no
	shipment, and the pre-existing rule that a serial may appear on two invoices stays as it was;
	this refuses only the case where two **active deliveries** claim the same physical unit at
	once. A delivery that was cancelled or fully returned released the unit, and `conflicting_delivery`
	is what says so.
	"""
	selections = tuple(
		((item.item_code, item.serial.normalized), item.delivery_note)
		for item in result.accepted
		if item.delivery_note is not None
	)

	if not selections:
		return

	others = gateway.delivery_links(tuple(identity for identity, _note in selections))
	conflicts = frozenset(delivery_rules.find_conflicts(selections, others))

	if not conflicts:
		return

	named = {
		item.serial.original
		for item in result.accepted
		if (item.item_code, item.serial.normalized) in conflicts
	}
	shown = "، ".join(sorted(named)[:NAMED_SERIALS])
	rest = len(named) - NAMED_SERIALS
	suffix = f" و {rest} مورد دیگر" if rest > 0 else ""

	raise OrchestrationError(
		delivery_rules.CODE_DELIVERY_CONFLICT,
		f"این سریال‌ها روی رسید تحویل فعال دیگری ثبت شده‌اند و هم‌زمان در دو تحویل باز نمی‌شوند ({shown}{suffix}).",
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


def is_retryable(error):
	"""Whether a caller may send the same request again."""
	if isinstance(error, OrchestrationError):
		return error.code not in (
			CODE_PAYLOAD_CHANGED,
			CODE_NO_SUBMISSIONS,
			CODE_COMPANY_CHANGED,
			CODE_ROW_UNKNOWN,
			# A delivery decision is made from documents that did not change, so the same request
			# fails the same way. Sending it again without choosing a delivery note, or without
			# correcting a note that is not a candidate, cannot succeed.
			CODE_DELIVERY_CHOICE_REQUIRED,
			CODE_DELIVERY_NOT_A_CANDIDATE,
			# A conflict is read from deliveries that did not change either. The unit is held
			# elsewhere until that delivery is cancelled or returned, which is not something a
			# second identical request can bring about.
			delivery_rules.CODE_DELIVERY_CONFLICT,
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
