"""Moving serial numbers from a cancelled invoice to the invoice that replaced it.

When an invoice is cancelled and amended, ERPNext keeps the cancelled document and its item
rows — and this app's assignments with them — and gives the amended invoice **new** item rows
with new names. Nothing is lost, and nothing is moved: the serials stay registered against a
document that is now history, and the amended invoice reads as a shortage. This module decides
what *can* move, and it decides it as a pure function over plain data, so every rule below is
testable without a database and the endpoint that applies it only wires.

**The source is never written to.** A transfer adds an assignment to the target invoice and
records what it did; the cancelled invoice's assignments are read, named in the record, and left
exactly as they were. A serial number an operator cannot scan again is the reason this feature
exists, and a feature that preserved one by deleting another would be the same loss with a
different name.

What decides a placement
------------------------
Three questions, in this order, because a reader asks them in this order:

1. **Which rows could take it?** The target's rows whose item is the serial's item and which may
   receive a registration at all (`core.eligibility.is_eligible` — the same flag the window and
   the report read). A row whose item is not marked is not a serial row, and a serial moved onto
   one would be invisible in the window that exists to show it.
2. **Is there room?** A row may never hold more than its invoiced quantity, counting what it
   already holds and what this plan has already placed on it. A target that invoices less than
   the source carried is the ordinary case, not an error: what fits moves, and the rest is
   reported as remaining on the cancelled invoice.
3. **Is it already there?** A serial the target already holds anywhere on itself is refused —
   which is also what makes applying the same transfer twice move nothing.

The operator may name the row for a serial instead of letting the plan choose, which is what
"selective" means when two rows of the target carry the same item. A mapping that names a row
which cannot take that serial is refused with the reason, never quietly re-aimed at another row.

A serial that is live on some **other** active invoice is a warning and an acknowledgment, not a
refusal. The app has always allowed a site to register the same serial text on two invoices, and
three identities on the operator's own site are exactly that today; inventing a global uniqueness
rule now would call existing data a conflict. So the operator is told, and has to say so.

Every serial the source holds ends up in exactly one of three places, and the split is what the
report the operator reads is built from:

* **placed** — it will be written on the target invoice;
* **refused** — it will not, and there is a reason to show for it, in `REASON_MESSAGES`. A source
  that carried more than the amendment invoices lands here, which is the ordinary case rather
  than a failure, and it is the case this feature was written for;
* **unselected** — the operator did not ask for it. Nothing was tried, so there is no reason and
  none is invented.

`refused` and `unselected` are both "still on the cancelled invoice", and they are reported apart
because they are different statements: one says the site would not allow the move, the other says
nobody asked for it. The union of the three is every serial on the source, with nothing left over
and nothing counted twice.
"""

import hashlib
from dataclasses import dataclass, field

from smartprocess_logistics.core import eligibility
from smartprocess_logistics.core.assignment import derive_assignment_values
from smartprocess_logistics.core.export import RESTORE_FIELDS, TRANSFER_RESULTS, canonical
from smartprocess_logistics.core.normalization import NormalizedSerial
from smartprocess_logistics.core.quantities import required_units

CODE_SOURCE_NOT_CANCELLED = "TRANSFER_SOURCE_NOT_CANCELLED"
CODE_TARGET_CANCELLED = "TRANSFER_TARGET_CANCELLED"
CODE_COMPANY_MISMATCH = "TRANSFER_COMPANY_MISMATCH"
CODE_SAME_INVOICE = "TRANSFER_SAME_INVOICE"
CODE_TARGET_NOT_AMENDMENT = "TRANSFER_TARGET_NOT_AMENDMENT"

REASON_UNKNOWN_SELECTION = "UNKNOWN_SELECTION"
REASON_ALREADY_ON_TARGET = "ALREADY_ON_TARGET"
REASON_ACKNOWLEDGEMENT_MISSING = "ACKNOWLEDGEMENT_MISSING"
REASON_ITEM_NOT_ON_TARGET = "ITEM_NOT_ON_TARGET"
REASON_TARGET_ROW_NOT_ELIGIBLE = "TARGET_ROW_NOT_ELIGIBLE"
REASON_NO_ROOM = "NO_ROOM"
REASON_ROW_MISMATCH = "ROW_MISMATCH"

# The two outcomes a record row can carry, and the values the DocType's `Select` offers.
RESULT_MOVED = "Moved"
RESULT_REFUSED = "Refused"

# The two outcomes a *preview* row can carry that are not a refusal. They are not record results:
# a serial the operator did not ask for reaches no row of the record at all, and naming it here
# keeps the window's one branch ("is this row a move, a question, or neither") reading a value the
# server chose rather than a code it looked up.
OUTCOME_MOVED = RESULT_MOVED
OUTCOME_UNSELECTED = "Unselected"

# What each refusal says to the operator. The reasons are stable codes and the sentences are the
# ones a person reads, kept in one table so a new reason cannot be added without a sentence.
REASON_MESSAGES = {
	REASON_UNKNOWN_SELECTION: "این سریال روی فاکتور لغو‌شده پیدا نشد",
	REASON_ALREADY_ON_TARGET: "این سریال از قبل روی فاکتور اصلاحی ثبت شده است",
	REASON_ACKNOWLEDGEMENT_MISSING: (
		"این سریال روی یک فاکتور فعال دیگر هم ثبت است و تأیید جداگانه لازم دارد"
	),
	REASON_ITEM_NOT_ON_TARGET: "کالای این سریال در فاکتور اصلاحی نیست",
	REASON_TARGET_ROW_NOT_ELIGIBLE: "برای کالای این ردیف ثبت سریال فعال نیست",
	REASON_NO_ROOM: "ردیف‌های این کالا در فاکتور اصلاحی جا ندارند",
	REASON_ROW_MISMATCH: "ردیف انتخاب‌شده برای این سریال مناسب نیست",
}

CODE_MESSAGES = {
	CODE_SOURCE_NOT_CANCELLED: "فاکتور مبدأ لغو نشده است؛ انتقال فقط از فاکتور لغو‌شده انجام می‌شود.",
	CODE_TARGET_CANCELLED: "فاکتور مقصد لغو شده است و سریال جدید نمی‌پذیرد.",
	CODE_COMPANY_MISMATCH: "شرکت فاکتور مبدأ و مقصد یکی نیست.",
	CODE_SAME_INVOICE: "مبدأ و مقصد یک فاکتور است.",
	CODE_TARGET_NOT_AMENDMENT: "فاکتور مقصد اصلاحیهٔ همین فاکتور لغو‌شده نیست.",
}

# The reasons a **record** can hold, in the order the DocType's `Select` offers them. The stale
# selection is not one of them: it names no source assignment, so a row carrying it would be a row
# about nothing. The schema test compares this tuple with the DocType's options in both
# directions, so a reason added here without a place to store it, or stored without a reason, is a
# failing test rather than a row nobody can read.
RECORDABLE_REASONS = tuple(reason for reason in sorted(REASON_MESSAGES) if reason != REASON_UNKNOWN_SELECTION)

# The stable codes a record issue carries. They are asserted by test, not read by the window: the
# window shows the Persian message, and the code is how a caller or a test names the failure
# without depending on the sentence.
CODE_RESTORE_MARKER_PARTIAL = "TRANSFER_RESTORE_MARKER_PARTIAL"
CODE_RESULT_INVALID = "TRANSFER_RESULT_INVALID"
CODE_REASON_INVALID = "TRANSFER_REASON_INVALID"
CODE_REFUSED_WITHOUT_REASON = "TRANSFER_REFUSED_WITHOUT_REASON"
CODE_MOVED_WITH_REASON = "TRANSFER_MOVED_WITH_REASON"
CODE_COUNT_MISMATCH = "TRANSFER_COUNT_MISMATCH"
CODE_ACKNOWLEDGED_MISMATCH = "TRANSFER_ACKNOWLEDGED_MISMATCH"
CODE_TOTAL_MISMATCH = "TRANSFER_TOTAL_MISMATCH"
CODE_IDENTITY_INCOMPLETE = "TRANSFER_IDENTITY_INCOMPLETE"
# A child row whose copies of the serial master disagree with the master it links to. This one is
# not raised by `record_issues` — it needs the master, which lives in the database — but it shares
# the same `RecordIssue` shape so the live validator reports it beside the pure checks.
CODE_CHILD_MASTER_MISMATCH = "TRANSFER_CHILD_MASTER_MISMATCH"

# The child-row fields that name a record row's identity. A record row is evidence because it says
# *which serial* moved or was refused and *which source assignment* it came from; a row missing any
# one of these is a row about nothing in particular, and a forged one can be edited to point at a
# different serial without any of the other checks noticing.
IDENTITY_FIELDS = (
    "logistics_serial",
    "serial_no",
    "item_code",
    "normalized_serial",
    "source_assignment",
)


class TransferError(ValueError):
	"""A request that cannot be planned at all. `code` is stable, `message` is Persian."""

	def __init__(self, code, message=None):
		super().__init__(message or CODE_MESSAGES.get(code, code))
		self.code = code
		self.message = message or CODE_MESSAGES.get(code, code)


@dataclass(frozen=True)
class SourceSerial:
	"""One registration on the cancelled invoice, as it is stored there.

	`serial_ref` and `version` are the referenced `Logistics Serial` and the rule set that produced
	its identity. They are carried rather than looked up again at apply time so that what gets
	written is what the preview read: a second read is a second chance to find something else, and
	`core.assignment.derive_assignment_values` needs both to build the new row.
	"""

	assignment: str
	serial: str
	normalized: str
	item_code: str
	row_key: str
	row_index: int | None = None
	serial_ref: str = ""
	version: int = 0


@dataclass(frozen=True)
class TargetRow:
	"""One item row of the amended invoice, and what it can still take.

	`serial_required` is the item's own flag, carried here rather than reduced to a boolean in the
	caller, so that the one predicate the rest of the app reads for "may this row receive a
	registration" (`core.eligibility.is_eligible`) is this module's rule too and not a second
	opinion about it.
	"""

	row_key: str
	item_code: str
	capacity: int
	serial_required: bool
	held: int = 0
	index: int | None = None

	@property
	def room(self):
		return max(0, self.capacity - self.held)


@dataclass(frozen=True)
class TransferRequest:
	"""Everything the decision needs. Plain data: the endpoint builds it, the tests build it.

	The four facts about the two documents — their companies, their lifecycles, and whether one
	amends the other — have no defaults, and that is deliberate in both directions. `False` is the
	permitting value for a cancelled target and the refusing value for a cancelled source, so
	whichever default they took would be the wrong answer for a caller that forgot to read them;
	and a caller that has two invoices to describe has already read all three off the database.
	"""

	source_invoice: str
	target_invoice: str
	source_company: str
	target_company: str
	source_cancelled: bool
	target_cancelled: bool
	target_amends_source: bool
	serials: tuple = ()
	rows: tuple = ()
	# Identities the target already holds anywhere on itself, and identities live on some other
	# invoice that is not cancelled.
	on_target: frozenset = frozenset()
	live_elsewhere: frozenset = frozenset()
	# `None` means every serial the source holds.
	selection: frozenset | None = None
	mapping: dict = field(default_factory=dict)
	acknowledged: frozenset = frozenset()


@dataclass(frozen=True)
class Placement:
	"""One serial that will be written on the target invoice.

	Everything the write needs and nothing about the source's own storage: what is written is a
	**new** registration on the target, and the source's row is only named in the audit record.
	"""

	source_assignment: str
	serial_ref: str
	serial: str
	normalized: str
	item_code: str
	target_row: str
	target_index: int | None = None
	version: int = 0


@dataclass(frozen=True)
class Refusal:
	"""One serial that will not move, and why."""

	source_assignment: str
	serial: str
	normalized: str
	reason: str
	detail: str = ""

	def describe(self):
		return REASON_MESSAGES.get(self.reason, self.reason)

	def as_row(self):
		return {
			"assignment": self.source_assignment,
			"serial": self.serial,
			"reason": self.reason,
			"message": self.describe(),
			"detail": self.detail,
		}


@dataclass(frozen=True)
class TransferPlan:
	"""What a transfer would do: the placements, the refusals, and what nobody asked to move."""

	placements: tuple = ()
	refusals: tuple = ()
	unselected: tuple = ()
	acknowledged: tuple = ()
	source_total: int = 0

	def counts(self):
		"""The numbers the result summary and the audit record are made of.

		`unresolved` is counted apart from `refused` because it names no source assignment and so
		reaches no row of the record; it is the count an apply refuses on, not a result.
		"""
		unresolved = len(self.unknown_selection)

		return {
			"source": self.source_total,
			"moved": len(self.placements),
			"refused": len(self.refusals) - unresolved,
			"unselected": len(self.unselected),
			"unresolved": unresolved,
			"acknowledged": len(self.acknowledged),
		}

	@property
	def unknown_selection(self):
		"""Selections naming a serial the source does not hold.

		Not an outcome of a transfer: the source is where a transfer reads from, so a serial that
		is not there cannot be moved from it, and the operator's view was stale. It reaches no row
		of the record — the child table names the source's own assignments, and this names none —
		and the apply refuses the whole request when one is present, so that the serials the
		operator confirmed are exactly the serials that move.
		"""
		return tuple(row for row in self.refusals if row.reason == REASON_UNKNOWN_SELECTION)

	@property
	def warnings(self):
		"""Serials that are live on another active invoice, and have not been acknowledged yet.

		Told apart from the rest because they are the one refusal an answer from the operator
		changes: acknowledging one turns it into a move, and the acknowledgment is what the record
		keeps. Nothing here is an error — this app has never had a site-wide uniqueness rule over
		serial text, and calling one a conflict now would call the site's own existing data a
		conflict.
		"""
		return tuple(row for row in self.refusals if row.reason == REASON_ACKNOWLEDGEMENT_MISSING)

	@property
	def blocked(self):
		"""Serials that cannot move and that no answer from the operator would move.

		The ordinary case is a row with no room left, which is what an amendment that invoices
		less than its predecessor produces; the rest are the target not carrying the item at all
		and a row named for a serial it cannot take. All of them are reported with the reason, so
		the operator is told what happened to every serial rather than left to count.
		"""
		settled = {REASON_ACKNOWLEDGEMENT_MISSING, REASON_UNKNOWN_SELECTION}

		return tuple(row for row in self.refusals if row.reason not in settled)


def _check_invoices(request):
	"""The invoice-level gates: two documents, a cancelled source, its amended successor, one
	company.

	The amendment gate is what makes this a transfer and not a copy. Two unrelated invoices can
	carry the same item in the same company, and moving a serial between them would be a claim
	about a transaction that never happened; the serial on the cancelled invoice is there because
	of the invoice that replaced it. `amended_from` is the site's own record of that, and it
	points at exactly one predecessor.
	"""
	if request.source_invoice == request.target_invoice:
		raise TransferError(CODE_SAME_INVOICE)

	if not request.source_cancelled:
		raise TransferError(CODE_SOURCE_NOT_CANCELLED)

	if request.target_cancelled:
		raise TransferError(CODE_TARGET_CANCELLED)

	if not request.target_amends_source:
		raise TransferError(CODE_TARGET_NOT_AMENDMENT)

	if request.source_company != request.target_company:
		raise TransferError(CODE_COMPANY_MISMATCH)


def preview_token(request):
	"""What the operator confirmed, as one string.

	Computed over the **request** — which source, which target, which serials, which rows, which
	acknowledgments — and not over the plan, because the plan is a function of the site's state as
	well and the site can change between the preview and the apply. The apply revalidates every
	condition against the database and refuses when the answer differs; this token's job is only
	to prove that the operator confirmed *this* selection, so a stale dialog cannot move a
	different set of serials than the one it showed.
	"""
	return hashlib.sha256(
		canonical(
			{
				"source": request.source_invoice,
				"target": request.target_invoice,
				"selection": sorted(request.selection or [serial.normalized for serial in request.serials]),
				"mapping": {key: value for key, value in sorted(request.mapping.items())},
				"acknowledged": sorted(request.acknowledged),
			}
		).encode("utf-8")
	).hexdigest()


def _candidates(request, serial_row, chosen):
	"""The target rows this serial could go to, best first.

	An explicit choice from the request is the whole answer when it is there — and it is the whole
	answer for that serial even when it is wrong, because the operator named a row and is owed the
	reason that row cannot take it rather than a placement somewhere else. Otherwise the target's
	own rows come in the order they appear on the invoice, so a row is filled before the next.
	"""
	if chosen:
		return tuple(row for row in request.rows if row.row_key == chosen)

	return tuple(sorted(request.rows, key=lambda row: (row.index or 0, row.row_key)))


def _refusal_for(request, serial_row, chosen):
	"""Why this serial has no row to go to, named as precisely as the site allows."""

	if chosen:
		named = next((row for row in request.rows if row.row_key == chosen), None)

		if named is None:
			return REASON_ROW_MISMATCH, chosen

		if named.item_code != serial_row.item_code:
			return REASON_ROW_MISMATCH, f"{named.item_code} ≠ {serial_row.item_code}"

		if not eligibility.is_eligible(named.serial_required):
			return REASON_TARGET_ROW_NOT_ELIGIBLE, named.item_code

		return REASON_NO_ROOM, named.item_code

	same_item = tuple(row for row in request.rows if row.item_code == serial_row.item_code)

	if not same_item:
		return REASON_ITEM_NOT_ON_TARGET, serial_row.item_code

	if not any(eligibility.is_eligible(row.serial_required) for row in same_item):
		return REASON_TARGET_ROW_NOT_ELIGIBLE, serial_row.item_code

	return REASON_NO_ROOM, serial_row.item_code


def plan_transfer(request):
	"""Decide what moves, what does not, and why. Never touches the source.

	The serials are walked in the order the caller gives them, which is the order they were
	registered on the cancelled invoice, so the same request always produces the same plan and a
	half-filled row is filled the same way on every preview.
	"""
	_check_invoices(request)

	rows = tuple(request.rows)
	on_target = set(request.on_target)
	placed_on_row = {row.row_key: 0 for row in rows}
	placements = []
	refusals = []
	acknowledged = []
	seen_selection = set()

	for serial_row in request.serials:
		if request.selection is not None and serial_row.normalized not in request.selection:
			continue

		seen_selection.add(serial_row.normalized)

		if serial_row.normalized in on_target:
			# The target already holds it — which is what applying the same transfer twice finds,
			# and what a second operator adding the same label finds. Refused, never duplicated.
			refusals.append(
				Refusal(
					serial_row.assignment,
					serial_row.serial,
					serial_row.normalized,
					REASON_ALREADY_ON_TARGET,
				)
			)
			continue

		if serial_row.normalized in request.live_elsewhere:
			if serial_row.normalized not in request.acknowledged:
				refusals.append(
					Refusal(
						serial_row.assignment,
						serial_row.serial,
						serial_row.normalized,
						REASON_ACKNOWLEDGEMENT_MISSING,
					)
				)
				continue

			acknowledged.append(serial_row.normalized)

		chosen = request.mapping.get(serial_row.normalized)
		target = None

		for candidate in _candidates(request, serial_row, chosen):
			if candidate.item_code != serial_row.item_code:
				continue

			if not eligibility.is_eligible(candidate.serial_required):
				continue

			if candidate.capacity - candidate.held - placed_on_row[candidate.row_key] <= 0:
				continue

			target = candidate
			break

		if target is None:
			reason, detail = _refusal_for(request, serial_row, chosen)
			refusals.append(
				Refusal(serial_row.assignment, serial_row.serial, serial_row.normalized, reason, detail)
			)
			continue

		placed_on_row[target.row_key] += 1
		placements.append(
			Placement(
				source_assignment=serial_row.assignment,
				serial_ref=serial_row.serial_ref,
				serial=serial_row.serial,
				normalized=serial_row.normalized,
				item_code=serial_row.item_code,
				target_row=target.row_key,
				target_index=target.index,
				version=serial_row.version,
			)
		)

	if request.selection is not None:
		for wanted in sorted(request.selection - seen_selection):
			refusals.append(Refusal("", wanted, wanted, REASON_UNKNOWN_SELECTION))

	moved = {placement.normalized for placement in placements}
	refused = {refusal.normalized for refusal in refusals}
	unselected = tuple(
		serial_row
		for serial_row in request.serials
		if serial_row.normalized not in moved and serial_row.normalized not in refused
	)

	return TransferPlan(
		placements=tuple(placements),
		refusals=tuple(refusals),
		unselected=unselected,
		acknowledged=tuple(acknowledged),
		source_total=len(request.serials),
	)


def capacity_of(invoiced_qty):
	"""How many serials a row of this quantity may hold — the rule every write path uses."""
	return required_units(invoiced_qty)


def record_of(plan, request):
	"""The audit record a plan becomes, as the dictionary the gateway writes.

	A pure description rather than a write: what the record says is decided here and tested here,
	and the endpoint that calls it only hands the result on. The alternative — building the
	document in the gateway — is the one place in this app where a decision would be reachable by
	neither a test nor a reviewer without a site.

	The rows are emitted by walking the **source's** serials in their own order, so the table reads
	in the order the operator saw it and every serial that was decided about appears exactly once,
	whichever of the two lists it came from. A serial that was neither placed nor refused is not
	here at all: it was not selected, and a row saying so would be a row about a decision nobody
	made. That is also why the header carries `source_total` — with the selection counted out, the
	rows alone cannot say how many the source held.

	`serial_no` and `item_code` are written even though the DocType fetches them from the linked
	serial: the fetch happens inside the framework's `_validate_links`, which runs before its
	mandatory-field check, so the fetch would satisfy `item_code`'s `reqd` — and a record whose
	display column depends on the order of two calls inside `insert` is a record that reads as
	blank the day that order changes. Both values come from the serial master that was read for
	the plan, so writing them decides nothing.
	"""
	# Both lists are indexed by identity, and the loop below only ever looks up an identity a
	# **source** serial has. A stale selection names one the source does not hold, so it is in
	# `refused` under a key nothing here asks for and reaches no row — which is the property, and
	# the reason there is no filter on this line saying so. Written this way after a control that
	# removed exactly that filter and changed no test's answer.
	placed = {placement.normalized: placement for placement in plan.placements}
	refused = {refusal.normalized: refusal for refusal in plan.refusals}
	acknowledged = set(plan.acknowledged)
	items = []

	for serial_row in request.serials:
		placement = placed.get(serial_row.normalized)
		refusal = refused.get(serial_row.normalized)

		if placement is None and refusal is None:
			continue

		item = {
			"logistics_serial": serial_row.serial_ref,
			"serial_no": serial_row.serial,
			"item_code": serial_row.item_code,
			"normalized_serial": serial_row.normalized,
			"source_assignment": serial_row.assignment,
			"source_row": serial_row.row_key,
			"source_index": serial_row.row_index,
			"acknowledged": 1 if serial_row.normalized in acknowledged else 0,
			"result": RESULT_REFUSED if refusal is not None else RESULT_MOVED,
			"reason": "",
			"reason_message": "",
			"target_row": "",
			"target_index": None,
		}

		if refusal is not None:
			item["reason"] = refusal.reason
			item["reason_message"] = refusal.describe()
		else:
			item["target_row"] = placement.target_row
			item["target_index"] = placement.target_index

		items.append(item)

	counts = plan.counts()

	return {
		"source_invoice": request.source_invoice,
		"target_invoice": request.target_invoice,
		"company": request.target_company,
		"confirmed_token": preview_token(request),
		"source_total": counts["source"],
		"moved_count": counts["moved"],
		"refused_count": counts["refused"],
		"unselected_count": counts["unselected"],
		"acknowledged_count": counts["acknowledged"],
		"items": items,
	}


@dataclass(frozen=True)
class RecordIssue:
	"""One way a transfer record disagrees with itself.

	The live validator refuses a record that carries one of these; the message is what an operator
	reads and the code is what a test asserts. `code` is stable, `message` is Persian.
	"""

	code: str
	message: str


def record_issues(record):
	"""Every way a transfer record can disagree with itself, as plain issues.

	The evidential document's worth is that it agrees with itself whatever else changed: a header
	whose counts name its rows, rows whose reasons match their outcomes, and a provenance marker
	that is either there in full or not there at all. A record that fails one of these was not
	written by `record_of` — it was made by hand, or edited — and the live validator refuses it on
	the way in.

	Read-only and pure: it takes the plain record `record_of` returns (a header dict whose `items`
	list holds the child rows) and returns a tuple of issues, never raising, so a caller can show
	them all at once. The database-side question — does the linked serial master agree with the
	row's copies — is not answered here; that read belongs to the validator that has a database,
	because it must be skipped when the record is the app's own archive coming back.

	`restored_from`/`restored_on`/`restored_by` are the three `RESTORE_FIELDS` the archive carries:
	an original record has all three empty and a restored one has all three set. Any other
	combination is a record claiming to be two things at once.
	"""
	issues = []
	items = record.get("items") or ()

	marker = [field for field in RESTORE_FIELDS if record.get(field)]
	if marker and len(marker) != len(RESTORE_FIELDS):
		issues.append(
			RecordIssue(
				CODE_RESTORE_MARKER_PARTIAL,
				"نشانهٔ بازآوری سابقه ناقص است؛ یا هر سه مقدار باید باشند یا هیچ‌کدام.",
			)
		)

	moved = 0
	refused = 0
	acknowledged = 0

	for index, item in enumerate(items):
		result = item.get("result")

		if result not in TRANSFER_RESULTS:
			issues.append(
				RecordIssue(
					CODE_RESULT_INVALID,
					f"نتیجهٔ «{result}» در ردیف {index + 1} شناخته‌شده نیست.",
				)
			)
			continue

		if result == RESULT_REFUSED:
			refused += 1
			reason = item.get("reason")

			if not reason:
				issues.append(
					RecordIssue(
						CODE_REFUSED_WITHOUT_REASON,
						f"ردیف {index + 1} رد شده است و دلیلی ندارد.",
					)
				)
			elif reason not in RECORDABLE_REASONS:
				issues.append(
					RecordIssue(
						CODE_REASON_INVALID,
						f"دلیل «{reason}» در ردیف {index + 1} در این قالب شناخته‌شده نیست.",
					)
				)
		else:
			moved += 1
			if item.get("reason"):
				issues.append(
					RecordIssue(
						CODE_MOVED_WITH_REASON,
						f"ردیف {index + 1} منتقل شده است و دلیل رد دارد.",
					)
				)

		if int(item.get("acknowledged") or 0):
			acknowledged += 1

		missing = [field for field in IDENTITY_FIELDS if not item.get(field)]
		if missing:
			issues.append(
				RecordIssue(
					CODE_IDENTITY_INCOMPLETE,
					f"ردیف {index + 1} شناسهٔ ناقصی دارد («{missing[0]}» خالی است).",
				)
			)

	if moved != int(record.get("moved_count") or 0):
		issues.append(
			RecordIssue(
				CODE_COUNT_MISMATCH,
				f"سابقه می‌گوید {record.get('moved_count')} سریال منتقل شده و ردیف‌هایش {moved} تا هستند.",
			)
		)

	if refused != int(record.get("refused_count") or 0):
		issues.append(
			RecordIssue(
				CODE_COUNT_MISMATCH,
				f"سابقه می‌گوید {record.get('refused_count')} سریال رد شده و ردیف‌هایش {refused} تا هستند.",
			)
		)

	if acknowledged != int(record.get("acknowledged_count") or 0):
		issues.append(
			RecordIssue(
				CODE_ACKNOWLEDGED_MISMATCH,
				f"سابقه می‌گوید {record.get('acknowledged_count')} سریال تأییدشده دارد و ردیف‌هایش {acknowledged} تا هستند.",
			)
		)

	total = moved + refused + int(record.get("unselected_count") or 0)

	if total != int(record.get("source_total") or 0):
		issues.append(
			RecordIssue(
				CODE_TOTAL_MISMATCH,
				"منتقل‌شده، رد‌شده و انتخاب‌نشده با سریال‌های مبدأ جمع نمی‌شود.",
			)
		)

	return tuple(issues)


def preview_of(plan, request):
	"""What the operator is shown: one row per serial the source holds, in the source's order.

	One list and not four. The plan keeps placements and refusals apart because they are acted on
	differently and it keeps the three refusal kinds apart because they are answered differently,
	but a person reviewing eighteen serials reads **the invoice**, in the order the rows are
	printed on it. Rebuilding that order in the window from four lists is the window deciding
	something, and it got it wrong the first time it was tried: the table came out grouped by
	outcome, which is the one order an operator cannot check against the paper in front of them.

	So the order is decided here, by walking the serials the source holds, and the window draws the
	list it is given. `answerable` says whether an answer from the operator would change the
	outcome — true for a serial live on another invoice, false for one there is no room for — so
	the window never has to know a reason code to decide whether to offer a checkbox.

	`stale` is the one thing not on this list: a selection naming a serial the source does not
	hold has no source row to sit on, so it is reported beside the list rather than in it.
	"""
	placed = {placement.normalized: placement for placement in plan.placements}
	refused = {refusal.normalized: refusal for refusal in plan.refusals}
	acknowledged = set(plan.acknowledged)
	rows = []

	for order, serial_row in enumerate(request.serials):
		placement = placed.get(serial_row.normalized)
		refusal = refused.get(serial_row.normalized)
		row = {
			"serial": serial_row.serial,
			"item_code": serial_row.item_code,
			"source_row": serial_row.row_key,
			"source_index": serial_row.row_index,
			"order": order,
			"acknowledged": serial_row.normalized in acknowledged,
			"answerable": False,
			"message": "",
			"detail": "",
			"target_row": None,
			"target_index": None,
			"outcome": OUTCOME_UNSELECTED,
		}

		if placement is not None:
			row["outcome"] = OUTCOME_MOVED
			row["target_row"] = placement.target_row
			row["target_index"] = placement.target_index
		elif refusal is not None:
			row["outcome"] = refusal.reason
			row["message"] = refusal.describe()
			row["detail"] = refusal.detail
			row["answerable"] = refusal.reason == REASON_ACKNOWLEDGEMENT_MISSING

		rows.append(row)

	return {
		"serials": rows,
		"stale": [
			{"serial": refusal.serial, "message": refusal.describe()} for refusal in plan.unknown_selection
		],
		"counts": plan.counts(),
	}


def assignment_values(plan, request):
	"""The new registrations a plan will write, built by the one function that builds them.

	`core.assignment.derive_assignment_values` is the single place an assignment's stored values
	are derived, and a transfer is not entitled to a second one: the identity a transfer writes
	has to be derived from the serial exactly as a typed registration's is, or the copy on the
	new row is a value this app made up. So the transfer hands it the serial's own stored
	identity — the rule set version included — and the row on the filled invoice is
	indistinguishable from one the operator scanned.

	Empty when the plan moves nothing, which is the ordinary case for a preview that has already
	run once.
	"""
	values = []

	for placement in plan.placements:
		values.append(
			derive_assignment_values(
				serial=NormalizedSerial(
					original=placement.serial,
					normalized=placement.normalized,
					version=placement.version,
				),
				serial_name=placement.serial_ref,
				item_code=placement.item_code,
				sales_invoice=request.target_invoice,
				sales_invoice_item=placement.target_row,
				company=request.target_company,
				row_index=placement.target_index,
			)
		)

	return tuple(values)
