"""Which delivery a serial belongs to, and how many units that delivery actually left with.

A serial is registered against an invoice item row, but the delivery it was handed over with is
a different fact: one invoice row can be delivered in several shipments, from several
warehouses, on several dates. This module answers the two questions the rest of the app asks:

- **Which delivery note?** `select_delivery` decides whether the row has one obvious answer, no
  answer, or more than one — and it refuses to pick when the answer is not obvious. Guessing
  when a row has two live deliveries would attach a serial to the wrong shipment, and nothing
  downstream could tell that it had happened.
- **How much was delivered?** `delivered_units` counts what actually left, net of returns, so a
  "missing serial" count is measured against the delivered quantity and not against the
  invoiced one. An invoice row of 10 delivered in two shipments of 5 is five labels short after
  the first shipment, not ten.

Three facts about the ERPNext documents shape the code below, and each was read from the
installed 16.35.0 source rather than assumed:

- A forward `Delivery Note Item` carries `si_detail`, the invoice row it fulfils.
- A **return** row does **not** copy `si_detail`. It carries `dn_detail`, the forward row it
  reverses, and `against_sales_invoice`, which names the invoice but not the row. So a return is
  found by walking `dn_detail` back to the forward row, and a candidate list therefore holds
  both forward rows and the returns of those rows.
- `Delivery Note.per_returned` is a percentage maintained by the standard controller, and it is
  the cheapest way to tell a closed delivery from one that can still be returned.

Draft deliveries are offered and recorded, because packing happens before the document is
submitted. Their link state is `DRAFT`, not `DELIVERED`: a serial attached to a draft shipment
is not confirmed as delivered, and the report says so.
"""

from dataclasses import dataclass

from smartprocess_logistics.core import quantities

CODE_DELIVERY_CHOICE_REQUIRED = "DELIVERY_CHOICE_REQUIRED"
CODE_DELIVERY_NOT_A_CANDIDATE = "DELIVERY_NOT_A_CANDIDATE"
CODE_DELIVERY_CONFLICT = "DELIVERY_CONFLICT"

# What a row's delivery decision is.
STATUS_LINKED = "LINKED"
STATUS_PROVISIONAL = "PROVISIONAL"
STATUS_CHOICE_REQUIRED = "CHOICE_REQUIRED"
STATUS_NOT_A_CANDIDATE = "NOT_A_CANDIDATE"

# What a stored link currently means. `DRAFT` and `CANCELLED` are not confirmed deliveries.
LINK_UNLINKED = "UNLINKED"
LINK_DRAFT = "DRAFT"
LINK_DELIVERED = "DELIVERED"
LINK_CANCELLED = "CANCELLED"
LINK_RETURNED = "RETURNED"
LINK_UNKNOWN = "UNKNOWN"

DOCSTATUS_DRAFT = 0
DOCSTATUS_SUBMITTED = 1
DOCSTATUS_CANCELLED = 2

FULLY_RETURNED_PERCENT = 100


@dataclass(frozen=True)
class DeliveryCandidate:
	"""One Delivery Note item row that carries, or reverses, this invoice row.

	`qty` is stored as the document holds it, so a return row carries a negative quantity.
	`units` is the magnitude, because the same `ceil` rule that decides how many labels a row
	needs decides how many units a delivery moved.
	"""

	delivery_note: str
	delivery_note_item: str
	invoice_item: str
	delivery_date: str | None = None
	qty: float = 0.0
	docstatus: int = DOCSTATUS_DRAFT
	is_return: bool = False
	per_returned: float = 0.0
	warehouse: str | None = None
	against_detail: str | None = None

	@property
	def units(self):
		return quantities.required_units(self.qty)

	@property
	def submitted(self):
		return self.docstatus == DOCSTATUS_SUBMITTED

	@property
	def cancelled(self):
		return self.docstatus == DOCSTATUS_CANCELLED

	@property
	def is_forward(self):
		return not self.is_return

	@property
	def is_open(self):
		"""A delivery that can still take a link: submitted or not, but not cancelled."""
		return self.is_forward and self.docstatus != DOCSTATUS_CANCELLED


@dataclass(frozen=True)
class DeliveryChoice:
	"""The answer `select_delivery` gives for one invoice row."""

	status: str
	candidate: DeliveryCandidate | None = None
	code: str | None = None

	@property
	def is_linked(self):
		return self.status == STATUS_LINKED and self.candidate is not None

	@property
	def delivery_note(self):
		return self.candidate.delivery_note if self.candidate else None

	@property
	def delivery_note_item(self):
		return self.candidate.delivery_note_item if self.candidate else None


def _forward_rows(candidates):
	"""Forward, non-cancelled rows, in the order the gateway returned them."""
	return tuple(candidate for candidate in candidates if candidate.is_open)


def _note_order(candidates):
	"""Distinct delivery notes, in the order they first appear."""
	ordered = []

	for candidate in candidates:
		if candidate.delivery_note not in ordered:
			ordered.append(candidate.delivery_note)

	return tuple(ordered)


def select_delivery(candidates, requested_note=None, requested_item=None):
	"""Decide which delivery note one invoice row's serials belong to.

	An operator's explicit request is honoured only when it names something among the row's
	live candidates. With no request, a row with exactly one live delivery is linked to it; a
	row with none is registered provisionally; and a row with more than one is refused rather
	than guessed. Several item rows of the *same* delivery note are not an ambiguity about
	which delivery — the first is taken, and it is the same row every time because the gateway
	orders candidates deterministically.
	"""
	candidates = tuple(candidates)
	live = _forward_rows(candidates)

	if requested_note is not None and not str(requested_note).strip():
		requested_note = None

	if requested_item is not None and not str(requested_item).strip():
		requested_item = None

	if requested_note is not None:
		matching = tuple(
			candidate for candidate in live if candidate.delivery_note == requested_note
		)

		if requested_item is not None:
			matching = tuple(
				candidate
				for candidate in matching
				if candidate.delivery_note_item == requested_item
			)

		if not matching:
			return DeliveryChoice(
				status=STATUS_NOT_A_CANDIDATE,
				code=CODE_DELIVERY_NOT_A_CANDIDATE,
			)

		return DeliveryChoice(status=STATUS_LINKED, candidate=matching[0])

	if not live:
		return DeliveryChoice(status=STATUS_PROVISIONAL)

	if len(_note_order(live)) > 1:
		return DeliveryChoice(status=STATUS_CHOICE_REQUIRED, code=CODE_DELIVERY_CHOICE_REQUIRED)

	return DeliveryChoice(status=STATUS_LINKED, candidate=live[0])


def delivered_units(candidates):
	"""Units that actually left for this row, net of submitted returns.

	A draft delivery has not left and a cancelled one has been undone, so neither counts. The
	floor at zero guards stored data where returns exceed what was shipped, which registration
	must not read as a negative requirement.
	"""
	forward = 0
	returned = 0

	for candidate in candidates:
		if not candidate.submitted:
			continue

		if candidate.is_return:
			returned += candidate.units
		else:
			forward += candidate.units

	return max(0, forward - returned)


def _note_units(candidates, delivery_note):
	"""Net units of one delivery note, its returns included."""
	forward = 0
	returned = 0

	for candidate in candidates:
		if candidate.delivery_note != delivery_note or not candidate.submitted:
			continue

		if candidate.is_return:
			returned += candidate.units
		else:
			forward += candidate.units

	return max(0, forward - returned)


def link_state(candidates, delivery_note=None):
	"""What a stored delivery link currently means.

	`delivery_note` is the value stored on the assignment, which may be empty (provisional) or
	may name a document that has since been cancelled or fully returned. A link is never
	rewritten because the source document moved; the state simply stops meaning "delivered".
	"""
	if delivery_note is None or not str(delivery_note).strip():
		return LINK_UNLINKED

	candidates = tuple(candidate for candidate in candidates if candidate.delivery_note == delivery_note)

	# The link names a document that does not carry this row any more. It is not cancelled — the
	# row was moved, or the delivery's rows changed — and calling it cancelled would state
	# something the data does not say.
	if not candidates:
		return LINK_UNKNOWN

	forward = tuple(candidate for candidate in candidates if candidate.is_forward)

	if not forward:
		return LINK_CANCELLED

	if all(candidate.cancelled for candidate in forward):
		return LINK_CANCELLED

	if not any(candidate.submitted for candidate in forward):
		return LINK_DRAFT

	# A forward note's returns live on their own return notes, so they never appear among this
	# note's candidates and `_note_units` cannot see them. The standard controller keeps the
	# answer on the note itself, and `conflicting_delivery` already reads the same percentage.
	if max((candidate.per_returned or 0) for candidate in forward) >= FULLY_RETURNED_PERCENT:
		return LINK_RETURNED

	if _note_units(candidates, delivery_note) <= 0:
		return LINK_RETURNED

	return LINK_DELIVERED


def conflicting_delivery(delivery_note, others):
	"""Another active delivery that already holds a registration of the same serial.

	`others` are `(delivery_note, docstatus, per_returned)` triples read from the other
	assignments of the same serial identity. A conflict needs a **different** delivery note
	that is submitted and not fully returned: a provisional registration names no delivery, a
	cancelled delivery was undone, and a fully returned one released the unit. A serial that is
	merely also registered on another invoice without any delivery is not a conflict — that is
	the pre-existing acknowledgement case, and refusing it here would be a new rule.
	"""
	if delivery_note is None or not str(delivery_note).strip():
		return None

	for other_note, docstatus, per_returned in others:
		if not other_note or not str(other_note).strip():
			continue

		if other_note == delivery_note:
			continue

		if docstatus != DOCSTATUS_SUBMITTED:
			continue

		if (per_returned or 0) >= FULLY_RETURNED_PERCENT:
			continue

		return CODE_DELIVERY_CONFLICT

	return None


def find_conflicts(selections, others):
	"""The identities whose chosen delivery is already held by another active delivery.

	`selections` are `(identity, delivery_note)` pairs — one per serial about to be registered
	with a delivery — and `others` maps an identity to the
	`(delivery_note, docstatus, per_returned)` triples of its other registrations. The identities
	come back in the order they were given, so the refusal names the serials in the order the
	operator supplied them.
	"""
	conflicts = []

	for identity, delivery_note in selections:
		if conflicting_delivery(delivery_note, others.get(identity, ())):
			conflicts.append(identity)

	return tuple(conflicts)


def missing_units(delivered, registered):
	"""How many delivered units still have no label."""
	return max(0, delivered - registered)
