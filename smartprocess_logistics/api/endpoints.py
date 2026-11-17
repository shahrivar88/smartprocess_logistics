"""The whitelisted endpoints.

Reachable as soon as the app is installed, and reachable only through `FrappeGateway`, which
is the one place in this app that talks to the ORM. An endpoint reads its arguments, asks
`core.authz`, and turns the answer into a `core` value; every read and every write it needs is a
method on the gateway, so the layer that could bypass a permission check is one file wide.

The endpoints below, and no helper, are whitelisted. An endpoint does four things and nothing
else:

1. Read the arguments it needs, and refuse anything it was not given.
2. Build a `Principal` and ask `core.authz` whether the request is allowed.
3. Turn the request into a `core` value — a request, a plan, a preview.
4. Hand the plan to the gateway, or hand the preview back.

Every decision lives in `core`, which is why the authorization, the reconciliation, the
replay rules and the failure behaviour are all tested without a site.

What each endpoint refuses, on the server, whatever the client sent:

- A caller without the level the endpoint needs. `core.authz.ENDPOINT_LEVELS` holds the table.
- An invoice in a company the caller cannot reach. The company is read from the stored
  invoice, never from the request, which is what stops a caller reaching another company's
  invoice by naming it.
- A request that carries `normalized_serial`, or any other field the server derives.
- A page size above the configured maximum.
- A file above the configured size, and a workbook that fails the archive preflight.
"""

import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from smartprocess_logistics import __version__
from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core import (
	authz,
	delivery,
	delimiters,
	eligibility,
	export,
	importer,
	orchestration,
	transfer,
)
from smartprocess_logistics.core.assignment import AssignmentIdentityError, reject_supplied_identity
from smartprocess_logistics.core.delimiters import DelimiterError, split_typed_list
from smartprocess_logistics.core.limits import (
	MAX_INVOICE_ASSIGNMENTS,
	payload_limits,
	status_read_is_capped,
)
from smartprocess_logistics.core.markers import MarkerPatternError
from smartprocess_logistics.core.normalization import (
	DEFAULT_MAX_LENGTH,
	NormalizationPolicy,
	SerialTextError,
	normalize_serial,
)
from smartprocess_logistics.core.quantities import coverage_summary, row_coverage
from smartprocess_logistics.core.scan import FIELDNAME as SCAN_COMMAND_FIELD
from smartprocess_logistics.core.scan import served_next_row_command
from smartprocess_logistics.core.textfile import TextFileError, decode_text, payload_bytes
from smartprocess_logistics.core.units import describe_bytes
from smartprocess_logistics.core.validation import MAX_SUBMISSIONS

# How many assignment names one delete request may carry. This is a request-size guard, not
# the page length the operator configures, so it is its own constant rather than a settings row.
MAX_BULK_NAMES = 500

# What a data URL adds to the bytes it carries: `data:<type>;base64,` plus the third the
# encoding costs. Generous on purpose — it is a ceiling on nonsense, not a size check.
MAX_DATA_URL_OVERHEAD = 1024


def _scan_command(gateway):
	"""The scan window's next-column command, as this site's settings row holds it.

	Checked where it is *served* rather than where it is saved, and that is a choice about where
	this app can enforce things at all: its DocType controllers are held empty by a test, and it
	registers no `doc_events` (also held by a test, for the reason in `hooks.py` — a handler that
	raises takes somebody's save down with it). So the moment the value would take effect is the
	moment it is checked — already validated when it reaches the window, and unusable values served
	as empty (see `core.scan.served_next_row_command`, which is where that decision is made and
	where it is tested).

	Nothing is decided here: this reads two fields and hands them to a rule.
	"""
	settings = gateway.settings()

	return served_next_row_command(
		settings.get(SCAN_COMMAND_FIELD),
		delimiters=gateway.enabled_delimiters(),
		max_length=settings.get("serial_max_length"),
	)


def _intake_rules(gateway):
	"""The two site rules the scan window has to apply to text it receives itself.

	A barcode is one value and the window takes it as one; a **paste** is a list, and the window
	splits it, judges each piece and stages it in the columns. Neither can wait for a server round
	trip per serial, so the two rules that decide it travel with this invoice — the same way the
	next-column command does, and for the same reason: the window already makes this call, is
	already authorized to make it, and the values are not this document's state.

	Both are the rules the server applies to the same text, interpreted here rather than stored, so
	the window splits on the characters the site actually splits on — including whether a space is
	one — instead of on a list of escape sequences it would have to interpret for itself.

	- `delimiters`: the site's configured separators **plus** the ones a typed list uses, which is
	  exactly what `core.delimiters.split_typed_list` splits on server-side.
	- `max_length`: the serial length this site allows, the same value `core.normalization` refuses
	  past. It is here so a pasted serial that is too long is refused by the window with a reason,
	  rather than staged and refused later as a whole column.
	"""
	settings = gateway.settings()

	return {
		"delimiters": list(delimiters.served_delimiters(gateway.enabled_delimiters())),
		"max_length": int(settings.get("serial_max_length") or DEFAULT_MAX_LENGTH),
	}


def _limits(gateway):
	"""The ceilings this request reads a payload under, from the operator's Settings row.

	Each value is capped by the default the readers were built against; see `core.limits`.
	"""
	settings = gateway.settings()

	return payload_limits(
		file_kb=settings.max_import_file_kb,
		rows=settings.max_import_rows,
		sheets=settings.max_import_sheets,
		default_page=settings.default_page_length,
		max_page=settings.max_page_length,
	)


def _fail(error, title=None):
	"""Turn a `core` error into a Frappe exception, keeping its code in the message."""
	frappe.throw(
		_("{0} [{1}]").format(error.message, error.code),
		title=title or _("درج سریال"),
	)


def _contention():
	"""A write that lost a race, said in words an operator can act on.

	`frappe.QueryDeadlockError` and `frappe.QueryTimeoutError` are raised by the framework when
	two requests touch the same invoice, session or serial at the same instant: MariaDB picks
	one of them to continue and aborts the other. The aborted one is rolled back whole — measured:
	a second confirmation of one session left `Applied` once and no extra rows — which is why
	`core.orchestration.is_retryable` already answers "yes, send it again" for this class. What
	it does not do is say so: the raw error names a table and an error number, and reaches the
	Desk as `QueryDeadlockError (1020) ...`. The transaction is already over, so the message
	replaces it rather than wrapping it.
	"""
	frappe.throw(
		_("درخواست دیگری هم‌زمان روی همین سند در حال ثبت بود. چیزی ثبت نشد؛ لطفاً دوباره تلاش کنید."),
		title=_("ثبت هم‌زمان"),
	)


def _read_page(limit, offset, limits):
	"""Bound a page request, whatever the caller asked for.

	Both values come from the request, so both are parsed rather than trusted: `int()` on a
	string that is not a number raises `ValueError`, which reaches the caller as a server error
	rather than as a sentence about the argument they sent.
	"""
	try:
		limit = int(limit) if str(limit or "").strip() else limits.default_page_length
		offset = int(offset) if str(offset or "").strip() else 0
	except (TypeError, ValueError):
		frappe.throw(_("تعداد ردیف و شروع صفحه باید عدد باشند."))

	if limit < 1 or limit > limits.max_page_length:
		frappe.throw(_("تعداد ردیف صفحه باید بین ۱ و {0} باشد.").format(limits.max_page_length))

	if offset < 0:
		frappe.throw(_("شروع صفحه نمی‌تواند منفی باشد."))

	return limit, offset


def _delivery_view(candidates, registrations):
	"""One row's shipment facts, as the station draws them.

	`state` is the decision the row's live delivery rows support: one obvious delivery, none at
	all, or several the station has to ask about. It comes from the same `core.delivery` function
	the write path uses, so the window's question and the server's answer cannot disagree.

	`missing` is measured against what actually left, not against the invoiced quantity: a row of
	ten delivered in two shipments of five is five labels short after the first, not ten.
	`confirmed` counts the registrations a completed delivery holds, and `provisional` those that
	named no delivery at all. A label on a draft or a cancelled shipment is neither — each
	registration carries its own `delivery_state`, so the window can say which of the two it is.
	"""
	delivered = delivery.delivered_units(candidates)
	choice = delivery.select_delivery(candidates)

	# One link state per delivery note, because `link_state` answers about the note — a forward row
	# and its return are the same shipment, not two.
	notes = {}
	options = []

	for candidate in candidates:
		note = candidate.delivery_note

		if note not in notes:
			notes[note] = delivery.link_state(candidates, note)

		options.append(
			{
				"delivery_note": note,
				"delivery_note_item": candidate.delivery_note_item,
				"delivery_date": candidate.delivery_date,
				"warehouse": candidate.warehouse,
				"qty": candidate.qty,
				"units": candidate.units,
				"is_return": candidate.is_return,
				"docstatus": candidate.docstatus,
				"link_state": notes[note],
			}
		)

	return {
		"state": choice.status,
		# The code the write path would refuse with, when the row cannot be written without an
		# answer: `None` for a row that is linked or provisional without being asked anything.
		"code": choice.code,
		# The delivery the row is linked to with no explicit choice, or `None` when the row is
		# provisional or the answer is ambiguous. The station sends it back only after an operator
		# picked one from a list of more than one.
		"note": choice.delivery_note,
		"delivered": delivered,
		"missing": delivery.missing_units(delivered, len(registrations)),
		"confirmed": sum(
			1 for entry in registrations if entry.get("delivery_state") == delivery.LINK_DELIVERED
		),
		"provisional": sum(
			1 for entry in registrations if entry.get("delivery_state") == delivery.LINK_UNLINKED
		),
		"candidates": options,
	}


def _status(gateway, invoice_name):
	"""The rows of an invoice with their coverage, built from stored assignments.

	The required count, the shortage, the excess and the totals all come from `core.quantities`,
	so the dialog that draws them holds no arithmetic of its own. Which rows are *required* comes
	from the item's `sp_serial_required` flag — the same flag the shortage report filters on — and
	a row whose item is not marked is reported as optional: registrations are still counted and
	still listed, but no requirement, no shortage and no completion is claimed for it.

	Each row also carries the serials registered on it, as the text that was typed, in the order
	they were registered. The dialog puts them back in the row's box, which is what makes
	correcting one serial an edit rather than a retype of the row's whole set. One read of the
	assignments answers both the counts and that list, so the page is one query rather than two.

	Each row carries its delivery facts as well, from `_delivery_view`: whether the shipment is
	obvious, absent or ambiguous; how many units actually left; and what each stored link means
	today. The rows are read here, in one call for the whole invoice, because the station is the
	place the answer is needed and a second round trip for it would be a second place to keep in
	step. Nothing is inferred: a row with more than one live delivery reports `CHOICE_REQUIRED`
	and the list to choose from.
	"""
	rows = gateway.read_rows(invoice_name)
	# Bounded, and refused rather than truncated when the bound is reached: every count in this
	# payload is a count of what was read, so a page that was cut short would be reported as an
	# invoice that is smaller than it is — a shortage about half an invoice. See
	# `core.limits.status_read_is_capped`.
	assignments = gateway.list_assignments(invoice_name, MAX_INVOICE_ASSIGNMENTS, 0)

	if status_read_is_capped(len(assignments)):
		frappe.throw(
			_("این فاکتور بیش از {0} سریال ثبت‌شده دارد و در این پنجره نمایش داده نمی‌شود.").format(
				MAX_INVOICE_ASSIGNMENTS
			),
			title=_("سریال‌ها"),
		)

	# The shipment rows behind this invoice's rows, read once for the whole page. Same reason the
	# rows are read here rather than asked of the caller: this is the invoice's own fulfilment, and
	# a row whose deliveries were never looked up must not be drawn as a row with none.
	candidates = gateway.delivery_candidates(invoice_name, [row.row_key for row in rows])

	counts = {}
	serials = {}
	# Each registration with the name it is stored under, because the station draws one cell per
	# registration and deleting a cell means naming that row. `serials` stays as the text the
	# operator typed — it is what the row's box used to be compared against — and this carries
	# what a delete needs, from the same single read.
	attached = {}

	for assignment in assignments:
		row_key = assignment["sales_invoice_item"]
		counts[row_key] = counts.get(row_key, 0) + 1
		attached.setdefault(row_key, []).append(
			{
				"name": assignment["name"],
				"serial_no": assignment.get("serial_no"),
				"normalized_serial": assignment.get("normalized_serial"),
				"company": assignment.get("company"),
				"delivery_note": assignment.get("delivery_note"),
				"delivery_note_item": assignment.get("delivery_note_item"),
				# What the stored link means **now**. A delivery cancelled or returned after the
				# serial was registered leaves the registration exactly where it was — nothing here
				# rewrites it — and this is how the window says so instead of showing it as delivered.
				"delivery_state": delivery.link_state(
					candidates.get(row_key, ()), assignment.get("delivery_note")
				),
			}
		)

		if assignment.get("serial_no"):
			serials.setdefault(row_key, []).append(assignment["serial_no"])

	facts = gateway.item_facts(row.item_code for row in rows)

	covered = []
	# The rows that cannot take serials, and what they already carry, kept for the sentence a
	# command naming one of them earns. `ineligible_registered` counts them for the same reason the
	# coverage totals do not: their labels are not a requirement of this invoice.
	ineligible = []
	ineligible_registered = 0
	# The totals are the totals of the rows this invoice can register on. A row whose item is not
	# marked has no requirement, so counting it among the "required" would raise a shortage that is
	# not one — the station would ask for labels the report is never going to want.
	required_coverages = []
	excess_rows = 0
	excess_serials = 0

	for position, row in enumerate(rows, start=1):
		registered = counts.get(row.row_key, 0)
		item = facts.get(row.item_code) or {}
		serial_required = bool(item.get("serial_required"))

		# The capacity is the row's invoiced quantity, for every row: registering past it is
		# refused by `core.validation` whatever the item is marked as, so the station shows the
		# same number the server enforces rather than one of its own.
		coverage = row_coverage(row.row_key, row.item_code, row.invoiced_qty, registered)
		capacity = coverage.required
		required = capacity if serial_required else 0
		excess = max(0, registered - capacity)

		# **A row that cannot take serials is not a row.** It is not served, so it is not drawn, it
		# is not navigated to and no paste, import, scan or command can put a label on it; it is
		# reported instead, with the position it holds on the invoice, so the one thing that must
		# never happen — a command naming it being quietly obeyed by a different row — is answered
		# with a sentence. Its registrations are left exactly where they are.
		if not eligibility.is_eligible(serial_required):
			ineligible.append(
				{
					"row_key": row.row_key,
					"row_index": position,
					"item_code": row.item_code,
					"model": item.get("model") or row.item_code,
					"registered": registered,
				}
			)
			ineligible_registered += registered
			continue

		required_coverages.append(coverage)

		# Which registrations are the excess ones follows the order they are read in, which the
		# gateway fixes at `(creation, name)`: the same rows are marked the same way on every
		# refresh, and deleting one of them leaves the rest where they were.
		registrations = [
			{**entry, "is_excess": index >= capacity}
			for index, entry in enumerate(attached.get(row.row_key, []))
		]

		excess_rows += 1 if excess else 0
		excess_serials += excess

		covered.append(
			{
				"row_key": row.row_key,
				# The row's **position on the invoice**, not its position in this list: `*N` and `#N`
				# are the numbers an operator reads off the printed invoice, and renumbering the
				# served rows would silently aim a command at a different product.
				"row_index": position,
				"item_code": row.item_code,
				# The model this site keeps on the item, or the item code when it keeps none: a blank
				# heading would leave an operator with no way to tell two columns apart.
				"model": item.get("model") or row.item_code,
				"invoiced_qty": row.invoiced_qty,
				"registered": registered,
				"serial_required": serial_required,
				"capacity": capacity,
				"required": required,
				"shortage": max(0, required - registered),
				"excess": excess,
				# The one state this row is in — the same function the window calls with the labels it
				# holds in hand, and the server's answer with none, because it cannot know about a
				# label nobody has sent it yet. See `core.eligibility.row_state`.
				"state": eligibility.row_state(required, capacity, registered),
				"serials": serials.get(row.row_key, []),
				"assignments": registrations,
				# The shipment this row's labels belong to, and how much of it has actually left.
				"delivery": _delivery_view(candidates.get(row.row_key, ()), registrations),
			}
		)

	summary = coverage_summary(required_coverages)

	return {
		"sales_invoice": invoice_name,
		"rows": covered,
		# Rows this invoice has that cannot take serials, in the invoice's order: enough for the
		# window to answer a command naming one of them by name, and enough for an operator to see
		# that the row exists and how many registrations it is already carrying.
		"ineligible_rows": ineligible,
		# The window's own rule, served with the invoice it is about. It is not invoice state and
		# does not vary by row: it is the one thing the window needs to know about the site rather
		# than about this document, and it travels here because the window already makes this call,
		# is already authorized to make it, and would otherwise need a second one for a value that
		# cannot be read without a permission check of its own.
		"scan_command": _scan_command(gateway),
		"intake": _intake_rules(gateway),
		"summary": {
			"rows": summary.rows,
			"required": summary.required,
			"registered": summary.registered,
			"shortage": summary.shortage,
			# The excess of the rows this dialog can act on. A row that cannot take serials is not
			# counted here: its excess could never be seen or fixed in this window, and holding the
			# invoice's own completeness open for it would be a chip that nothing on screen explains.
			"excess": excess_serials,
			"incomplete_rows": summary.incomplete_rows,
			# A row count of zero would otherwise make the invoice vacuously complete, which reads
			# as "nothing is missing here" about an invoice where nothing was ever required — and an
			# excess holds the invoice open no matter which kind of row carries it.
			"is_complete": summary.is_complete and not excess_serials and bool(required_coverages),
			# Rows the dialog does not show, and the labels they already carry. Reported rather than
			# forgotten: they are real records on this invoice, they are readable in the Desk list
			# and through the API, and an operator who asks why a row is missing gets a number.
			"ineligible_rows": len(ineligible),
			"ineligible_registered": ineligible_registered,
			"excess_rows": excess_rows,
			"excess_serials": excess_serials,
		},
	}


@frappe.whitelist(methods=["GET"])
def get_invoice_serial_status(sales_invoice):
	"""The rows of one invoice and how many serials each already has."""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)
		authz.authorize(principal, "get_invoice_serial_status", invoice=invoice)
	except authz.AuthorizationError as error:
		_fail(error)
	except frappe.PermissionError:
		_fail(authz.AuthorizationError(authz.CODE_NOT_PERMITTED, _("به این فاکتور دسترسی ندارید.")))

	gateway.assert_can_read_invoice(invoice.name)

	return _status(gateway, invoice.name)


@frappe.whitelist(methods=["GET"])
def get_serial_assignments(sales_invoice, limit=None, offset=None):
	"""One page of the invoice's serial assignments."""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)
		authz.authorize(principal, "get_serial_assignments", invoice=invoice)
	except authz.AuthorizationError as error:
		_fail(error)
	except frappe.PermissionError:
		_fail(authz.AuthorizationError(authz.CODE_NOT_PERMITTED, _("به این فاکتور دسترسی ندارید.")))

	# The page the caller asked for, read after the request was allowed: `_limits` reads the site's
	# settings, and this file's rule is that nothing is read on behalf of a request that has not
	# been authorized. A caller who is refused now learns that, rather than how many rows a page is.
	limit, offset = _read_page(limit, offset, _limits(gateway))

	gateway.assert_can_read_invoice(invoice.name)

	return {
		"sales_invoice": invoice.name,
		"limit": limit,
		"offset": offset,
		"assignments": [dict(row) for row in gateway.list_assignments(invoice.name, limit, offset)],
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def set_row_serials(sales_invoice, sales_invoice_item, serials, delivery_note=None):
	"""Set the serials of one invoice row: add, replace and remove in one transaction.

	The row's whole set is replaced. Sending the serials it already has plus the new ones adds
	to it; sending fewer replaces it; sending none removes them all. `serials` is a list, or the
	text an operator typed, which the server splits with the configured delimiters.

	`delivery_note` is the operator's answer to "which shipment did these leave with?". It is
	needed only when the row has more than one live delivery: the row with one is linked to it
	without being asked, a row with none is registered provisionally, and a row with several is
	refused rather than guessed at. Naming a note the row does not belong to is refused the same
	way — see `core.delivery.select_delivery`.
	"""
	gateway = FrappeGateway()

	try:
		reject_supplied_identity(frappe.local.form_dict)
	except AssignmentIdentityError as error:
		_fail(error)

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)

		# Authorization first, and the invoice's rows only once it has passed. The rows would not
		# be returned to an unauthorized caller either way, but nothing this app reads on behalf
		# of a request is read before the request is allowed to make it — the site's own settings
		# included, which this endpoint reads for the serial-length policy and not for the answer,
		# and the configured delimiters it splits the caller's own text with.
		authz.authorize(principal, "set_row_serials", invoice=invoice)

		settings = gateway.settings()
		serials = _row_serials(serials, gateway.enabled_delimiters())
		submissions = tuple((sales_invoice_item, text) for text in serials)
		rows = gateway.read_rows(sales_invoice) if invoice else ()
		existing = gateway.read_existing(sales_invoice) if invoice else ()

		plan = orchestration.build_rewrite_plan(
			invoice=invoice,
			rows=rows,
			existing=existing,
			submissions=submissions,
			principal=principal,
			only_rows=(sales_invoice_item,),
			delimiters=(),
			policy=_policy(int(settings.serial_max_length or 60)),
			# The operator's chosen delivery, if there was one to choose. `core.orchestration`
			# resolves it against the row's live deliveries under the lock; an empty note is no
			# selection at all, which is what a row with a single delivery needs.
			selections=((sales_invoice_item, delivery_note),) if delivery_note else (),
		)
	except authz.AuthorizationError as error:
		_fail(error)
	except orchestration.OrchestrationError as error:
		_fail(error)
	except (AssignmentIdentityError, DelimiterError) as error:
		# The caller's own text, parsed once the request was allowed to make it: a delimiter this
		# site does not configure, or an identity supplied in the payload rather than resolved here.
		_fail(error)

	gateway.assert_can_read_invoice(invoice.name)

	try:
		outcome = orchestration.run_rewrite(plan, gateway)
	except orchestration.OrchestrationError as error:
		_fail(error)
	except (SerialTextError, DelimiterError, MarkerPatternError) as error:
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		# The validator refuses a serial already on the invoice, so reaching the index means two
		# writers raced, or a stored identity no longer matches what normalization produces. The
		# framework's own message names a MariaDB index, which is not something to show anyone.
		frappe.throw(_("این سریال از قبل روی همین فاکتور ثبت شده است."))
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	return {
		"sales_invoice": invoice.name,
		"sales_invoice_item": sales_invoice_item,
		"accepted": outcome.accepted,
		"deleted": outcome.deleted,
		"created_serials": list(outcome.created_serials),
		"serials_reused": outcome.serials_reused,
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60)
def add_row_serial(sales_invoice, sales_invoice_item, serial, delivery_note=None):
	"""Add one serial to one invoice row, leaving the rest of that row's registrations alone.

	This is what a scan station calls, once per label, and the difference from `set_row_serials`
	is the reason it exists at all. That endpoint replaces a row's set, so two operators scanning
	into the same row at the same time would each write the list they hold and the later one would
	drop the earlier one's labels without a word. An append cannot: the row's stored serials are
	read under the invoice lock and kept, one serial is added, and a label that is already there
	is refused by the validator rather than overwritten.

	The rate limit is higher than the other writes because a person with a barcode scanner
	legitimately reaches this endpoint once per label, and a warehouse can scan faster than it can
	type.

	A refusal says which refusal it was: a duplicate on the invoice, a row over its quantity, a
	serial longer than the site allows. The scanned text is the caller's to keep — the station
	holds it on a refusal so it can be corrected rather than retyped.

	`delivery_note` names the shipment this label left with, and is required only when the row has
	more than one live delivery. A row with one is linked to it here, and a row whose goods have
	not shipped yet is registered provisionally — marked as such and excluded from the
	confirmed-delivery status until it is reconciled.
	"""
	gateway = FrappeGateway()

	try:
		reject_supplied_identity(frappe.local.form_dict)
	except AssignmentIdentityError as error:
		_fail(error)

	if not isinstance(serial, str) or not serial.strip():
		frappe.throw(_("سریالی وارد نشده است."))

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)

		# Authorization first, and the invoice's rows only once it has passed — the same order
		# `set_row_serials` uses, and for the same reason.
		authz.authorize(principal, "add_row_serial", invoice=invoice)

		settings = gateway.settings()
		rows = gateway.read_rows(sales_invoice) if invoice else ()
		existing = gateway.read_existing(sales_invoice) if invoice else ()

		plan = orchestration.build_append_plan(
			invoice=invoice,
			rows=rows,
			existing=existing,
			row_key=sales_invoice_item,
			serials=(serial,),
			principal=principal,
			policy=_policy(int(settings.serial_max_length or 60)),
			# Resolved against the row's live deliveries under the lock, exactly as `set_row_serials`
			# does it. An empty note is no selection, which is what a row with a single delivery needs.
			delivery_note=delivery_note,
		)
	except authz.AuthorizationError as error:
		_fail(error)
	except orchestration.OrchestrationError as error:
		_fail(error)

	gateway.assert_can_read_invoice(invoice.name)

	try:
		outcome = orchestration.run_rewrite(plan, gateway)
	except orchestration.OrchestrationError as error:
		_fail(error)
	except (SerialTextError, DelimiterError, MarkerPatternError) as error:
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.throw(_("این سریال از قبل روی همین فاکتور ثبت شده است."))
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	# The row as it now stands, read back after the write. The station draws its counters and its
	# cells from this and not from the request it sent, so what an operator sees is what the
	# database holds — including a registration another user made a moment ago.
	status = _status(gateway, invoice.name)
	row = next(
		(entry for entry in status["rows"] if entry["row_key"] == sales_invoice_item),
		None,
	)

	return {
		"sales_invoice": invoice.name,
		"sales_invoice_item": sales_invoice_item,
		"accepted": outcome.accepted,
		"created_serials": list(outcome.created_serials),
		"serials_reused": outcome.serials_reused,
		"row": row,
		"summary": status["summary"],
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60)
def add_row_serials(sales_invoice, sales_invoice_item, serials, delivery_note=None):
	"""Add several serials to one invoice row, in one transaction, in the order given.

	The same act as `add_row_serial`, for a batch: the station's Save sends one row's waiting
	scans in one request instead of one request per label. What it buys is not speed — a person
	with a scanner cannot outrun either — but **one answer for one row**. A batch that the
	validator refuses writes nothing at all, so a row can never end up holding the first half of
	what an operator asked for and reporting a failure about the second half; and the rate limit
	that a long save could otherwise reach is no longer a ceiling on how many labels a row may
	hold.

	It is not `set_row_serials`: an append names no replaced row, so nothing another operator
	registered on this row is deleted, and the serials are added under the invoice lock. The
	row's quantity limit is the one in `core.validation`, unchanged, applied to the batch as a
	whole — see `core.orchestration.build_append_plan`.

	`serials` is taken as a list because that is what a caller has; it is bounded here, because a
	request is a request and a list of ten thousand labels is not a save.

	`delivery_note` names the shipment the labels left with, and is required only when the row has
	more than one live delivery — the same rule as `add_row_serial`. The whole batch is linked to
	one shipment or refused as a batch; half a batch on one delivery and half on another is two
	saves, because that is two different answers to the same question.
	"""
	gateway = FrappeGateway()

	try:
		reject_supplied_identity(frappe.local.form_dict)
	except AssignmentIdentityError as error:
		_fail(error)

	texts = _parse_serials(serials)

	if not texts:
		frappe.throw(_("سریالی وارد نشده است."))

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)

		# Authorization first, and the invoice's rows only once it has passed — the order every
		# write here uses, and for the same reason.
		authz.authorize(principal, "add_row_serials", invoice=invoice)

		settings = gateway.settings()
		rows = gateway.read_rows(sales_invoice) if invoice else ()
		existing = gateway.read_existing(sales_invoice) if invoice else ()

		plan = orchestration.build_append_plan(
			invoice=invoice,
			rows=rows,
			existing=existing,
			row_key=sales_invoice_item,
			serials=texts,
			principal=principal,
			policy=_policy(int(settings.serial_max_length or 60)),
			delivery_note=delivery_note,
		)
	except authz.AuthorizationError as error:
		_fail(error)
	except orchestration.OrchestrationError as error:
		_fail(error)

	gateway.assert_can_read_invoice(invoice.name)

	try:
		outcome = orchestration.run_rewrite(plan, gateway)
	except orchestration.OrchestrationError as error:
		_fail(error)
	except (SerialTextError, DelimiterError, MarkerPatternError) as error:
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.throw(_("این سریال از قبل روی همین فاکتور ثبت شده است."))
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	status = _status(gateway, invoice.name)
	row = next(
		(entry for entry in status["rows"] if entry["row_key"] == sales_invoice_item),
		None,
	)

	return {
		"sales_invoice": invoice.name,
		"sales_invoice_item": sales_invoice_item,
		"accepted": outcome.accepted,
		"created_serials": list(outcome.created_serials),
		"serials_reused": outcome.serials_reused,
		"row": row,
		"summary": status["summary"],
	}


@frappe.whitelist(methods=["POST"])
def delete_assignments(names):
	"""Remove assignments by name. Administrative, so a manager is required."""
	gateway = FrappeGateway()
	names = _parse_names(names)

	if not names:
		frappe.throw(_("هیچ ارجاعی برای حذف مشخص نشده است."))

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, "delete_assignments")
	except authz.AuthorizationError as error:
		_fail(error)

	invoices = {}

	for name in names:
		stored = gateway.read_assignment(name)

		if not stored:
			frappe.throw(_("ارجاع {0} پیدا نشد.").format(name))

		# The company is read from the stored row, never from the request.
		authz.require_company(principal, stored.company)

		if stored.sales_invoice not in invoices:
			gateway.assert_can_read_invoice(stored.sales_invoice)
			invoices[stored.sales_invoice] = True

	try:
		removed = gateway.delete_assignments(names)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	return {"deleted": removed, "names": list(names)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def prepare_import_preview(sales_invoice, content=None, filename=None):
	"""Read a TXT or XLSX payload and return the values it holds. Nothing is written.

	**A file is a stream of values, not a routing decision.** The answer is the nonempty cells
	of the workbook — column by column, top to bottom — or the whole decoded text of a `.txt`,
	and nothing has been decided about any of it: no row has been chosen, no cell has been
	interpreted, and no serial has been validated against the database. The window runs those
	values through the same intake a keyboard, a barcode scanner and a paste go through, which
	is what makes `*N`, `#N`, the configured next-row command and the site's own separators
	behave identically wherever the text came from.

	That is also why this endpoint writes nothing at all. It used to store a preview session so
	that `confirm_import` could apply it, which made a file import a second path that wrote to
	the invoice; the window stages into its columns now and `ذخیره سریال‌ها` is the only thing
	that writes, so nothing here needs a session and the site's `Logistics Import Session` table
	stops growing on every look at a file.
	"""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)
		authz.authorize(principal, "prepare_import_preview", invoice=invoice)
	except authz.AuthorizationError as error:
		_fail(error)

	gateway.assert_can_read_invoice(invoice.name)

	try:
		values, facts = _file_values(content, filename, _limits(gateway))
	except (TextFileError, DelimiterError, MarkerPatternError) as error:
		_fail(error)
	except ValueError as error:
		frappe.throw(str(error))

	return {
		"sales_invoice": invoice.name,
		"values": list(values),
		"sheets": list(facts.get("sheets", ())),
		"encoding": facts.get("encoding"),
		"numeric_cells": facts.get("numeric_cells", 0),
	}


# --- helpers, not whitelisted -------------------------------------------------------


def _policy(max_length):
	return NormalizationPolicy(max_length=max_length)


def _parse_serials(serials):
	"""Accept a list, or a JSON array in a string, and nothing else.

	Bounded by the ceiling the validator itself applies to a request — `MAX_SUBMISSIONS`, the
	number of serials one call may carry — rather than by the file ceiling, which is ten times
	larger and belongs to a file. A list between the two used to be accepted here and refused
	during validation, where the refusal is a `ValueError` no endpoint catches: the caller got a
	server error for a request this function could have answered in a sentence.

	A member that is not text is refused rather than stringified. `str({"a": 1})` is a perfectly
	registerable serial named `{'a': 1}`, and the caller would have created a row nobody typed
	instead of being told the request was malformed.
	"""
	if isinstance(serials, str):
		try:
			serials = json.loads(serials)
		except json.JSONDecodeError:
			frappe.throw(_("فهرست سریال‌ها خوانده نشد."))

	if not isinstance(serials, (list, tuple)):
		frappe.throw(_("سریال‌ها باید فهرست باشند."))

	if len(serials) > MAX_SUBMISSIONS:
		frappe.throw(_("تعداد سریال‌ها بیش از حد مجاز است."))

	for serial in serials:
		if not isinstance(serial, str):
			frappe.throw(_("هر سریال باید متن باشد."))

	return tuple(serials)


def _row_serials(raw, delimiters):
	"""The serials of one row, from the list or the typed text a caller sent.

	A list means the caller has already decided where one serial ends: it is taken as it is. A
	bare string is split here, with the delimiters configured in `Logistics Settings` **plus**
	`TYPED_SEPARATORS`, and blank tokens are dropped because separators typed twice are a
	keystroke and not a serial.

	The split used to live in the dialog, on a fixed set that included the comma, and the two
	rules disagreed: the shipped configuration does not make a comma a delimiter, so `A,B` was one
	serial in an uploaded file and two in the box. Both facts are wanted — a list typed into a box
	is separated with a comma by habit, and a comma is part of a serial in a file — so the
	difference is stated once, here, where a server test can reach it.
	"""
	if not isinstance(raw, str):
		return _parse_serials(raw)

	text = raw.strip()

	if not text:
		return ()

	if text.startswith("["):
		return _parse_serials(text)

	try:
		serials = split_typed_list(text, delimiters, max_tokens=importer.MAX_TOKENS)
	except DelimiterError as error:
		_fail(error)

	if len(serials) > importer.MAX_CANDIDATES:
		frappe.throw(_("تعداد سریال‌ها بیش از حد مجاز است."))

	return serials


def _parse_names(names):
	"""Accept a list, or a JSON array in a string."""
	if isinstance(names, str):
		try:
			names = json.loads(names)
		except json.JSONDecodeError:
			frappe.throw(_("فهرست ارجاع‌ها خوانده نشد."))

	if not isinstance(names, (list, tuple)):
		frappe.throw(_("ارجاع‌ها باید فهرست باشند."))

	if len(names) > MAX_BULK_NAMES:
		frappe.throw(_("تعداد ارجاع‌ها بیش از حد مجاز است."))

	return tuple(str(name) for name in names)


def _file_values(content, filename, limits):
	"""The values a payload holds, and what was noticed while reading it.

	`payload_bytes` is what turns a data URL — the only way a browser can put an `.xlsx` into a
	JSON request — back into bytes, and encodes plain text as UTF-8 for `decode_text` to detect.

	The text is measured **before** it is decoded, not after. Every other limit in this app bounds
	what has already been read; this one bounds what is about to be. A data URL is at most a third
	larger than the bytes it carries plus its own prefix, so a string longer than that is a payload
	no client could have sent honestly, and refusing it here costs one `len()` instead of a base64
	decode of whatever the request body happens to hold.

	A workbook is read **column by column, top to bottom** and returns its nonempty cells as
	texts; a text file returns its whole decoded text as one value. Nothing is interpreted here.
	"""
	if not content:
		frappe.throw(_("محتوای فایل فرستاده نشده است."))

	if not isinstance(content, str):
		frappe.throw(_("محتوای فایل باید متن باشد."))

	if len(content) > limits.max_bytes * 2 + MAX_DATA_URL_OVERHEAD:
		frappe.throw(_("حجم فایل بیش از حد مجاز است: {0}.").format(describe_bytes(limits.max_bytes)))

	content = payload_bytes(content)
	name = (filename or "").lower()

	if name.endswith((".xlsx", ".xlsm")):
		values, report = importer.values_from_workbook(
			content, archive_limits=limits.archive, sheet_limits=limits.sheets
		)

		return values, {
			"sheets": report.sheets,
			"numeric_cells": report.numeric_cells,
		}

	decoded = decode_text(content, max_bytes=limits.max_bytes)

	return (decoded.text,), {"encoding": decoded.encoding}


# --- exporting and importing this app's records --------------------------------------
#
# Three endpoints, in the order an operator uses them: take an export, look at what an import
# would do, then do it. The export is never written to the server's disk — the browser turns the
# document it receives into a download — which is what lets this app run on a stack whose app
# directory is mounted read-only.


@frappe.whitelist(methods=["GET"])
@rate_limit(limit=10, seconds=60)
def export_serials():
	"""Every serial, every assignment and every transfer record this caller may see, as one document.

	The document carries the site name it came from, the format and application versions, the
	normalisation version in force, and a digest over its own contents. The digest is what makes
	an import able to tell a file that was edited in a text editor from one that was not.

	A Manager is required, and a Manager is still only shown their own companies: the export is
	scoped to the companies the caller's `User Permission` rows allow, and the reads that build it
	are filtered rather than the finished document. A caller the site has restricted to no company
	at all is refused instead — a successful export of nothing is the one answer an operator would
	mistake for a copy of their history.
	"""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, "export_serials")
		authz.require_any_company(principal)
	except authz.AuthorizationError as error:
		_fail(error)

	rows = gateway.export_rows(principal)
	settings = gateway.settings()
	document = export.build(
		rows["serials"],
		rows["assignments"],
		rows["transfers"],
		rows["transfer_items"],
		app_version=__version__,
		site=frappe.local.site,
		exported_by=principal.user,
		exported_on=frappe.utils.now(),
		normalization_version=settings.normalization_version,
	)

	return {
		"filename": f"smartprocess-logistics-serials-{frappe.local.site}-{frappe.utils.now()[:10]}.json",
		"counts": document["counts"],
		"document": document,
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def preview_serial_restore(content=None):
	"""What an import of this file would do, without writing anything.

	The ordinary restore, so a row the file places on a cancelled invoice is reported rather than
	placed. The report names the endpoint that does place those rows, because a preview that only
	said "refused" would leave an operator with a file they cannot use and no way to find out why
	— see `preview_cancelled_serial_history`, which is the same file seen the other way.

	The `token` it returns is the file's own digest, and the apply endpoint requires it back. It
	is not a secret and not a session: it is the answer to "is this still the file I previewed",
	which matters because the preview and the apply are two separate requests over a file the
	browser holds.
	"""
	return _preview_export("preview_serial_restore", export.MODE_LIVE, content)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def preview_cancelled_serial_history(content=None):
	"""What the historical restore would do with this file, without writing anything.

	The same read of the same file as `preview_serial_restore`, and it needs to be an endpoint of
	its own rather than a flag on that one: the plan it reports is a *different* plan. A preview
	that described the ordinary restore and then handed its token to the historical one would tell
	an operator that eight rows cannot be placed and then place eight rows, which is the exact
	confusion the token exists to prevent.

	It asks for the archivist role, as the apply does. A preview is not a write, but it is a read
	of which rows of the file sit on cancelled invoices and what would become of them, and a
	person who may not perform the act has no business being told what it would do.
	"""
	return _preview_export("preview_cancelled_serial_history", export.MODE_HISTORY, content)


def _stamp(principal, document):
	"""Who is restoring, and when — the two facts about the act that the file cannot know.

	The site the record came from is not here: it is the document's own `site`, read in
	`core.export` where it can be tested, and the site being restored *into* is the one confusion
	this field exists to prevent.
	"""
	return export.RestoreStamp(by=principal.user, on=frappe.utils.now())


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def preview_restore_audit_records(content=None):
	"""What restoring this file's audit records would do, without writing anything.

	The third restore, and the one that places the transfer records themselves — a record and its
	rows, as documents. It places nothing else: the serials and the assignments are the other two
	restores' business, and a record whose serials are not on the site yet is reported as
	`SERIAL_UNRESOLVED` with the identity named, so the operator learns the order to do things in.

	Every record is placed whole or refused whole, and the pre-flight names every obstacle before a
	single write: a name this site already uses, a target invoice that is missing or has since been
	cancelled, a row whose serial is absent. Nothing is overwritten and nothing is renamed — the
	naming series is per site, so two sites both produce `LST-2026-00001`, and resolving that by
	overwriting would destroy an audit record while resolving it by renaming would lose the identity
	the archive exists to carry.
	"""
	return _preview_export("preview_restore_audit_records", export.MODE_RECORDS, content)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def restore_audit_records(content=None, token=None):
	"""Place this file's audit records — each one whole, with its own rows — and count what it could not.

	The third restore: it writes `Logistics Serial Transfer` documents from an archive, and nothing
	else. Three decisions shape it, and each is here rather than implicit in some flag:

	**A restored record says it was restored.** `restored_from`, `restored_on` and `restored_by` are
	written from the file's own `site` and from this request, and they are the whole of what tells a
	record made here from one carried in. The record's *act* still belongs to whoever performed it —
	`creation` and `owner` are written back from the file, as every restore does — so the history is
	preserved and the provenance of the *document* is not forged.

	**A name collision is refused, never resolved.** The naming series is per site
	(`LST-.YYYY.-.#####`), so two sites both produce `LST-2026-00001` for different transfers.
	Overwriting would destroy an audit record; renaming would lose the identity the archive exists to
	carry. So the record is reported as `RECORD_EXISTS`, with its name, and left alone.

	**All or nothing, with a pre-flight.** Every obstacle is found before a single write — a taken
	name, a target invoice that is missing or has since been cancelled, a row whose serial is not on
	this site — so an operator learns what stands in the way rather than watching a file half apply.
	One transaction (I14), and a record is placed with all of its rows or not at all, because a
	record missing a row is a record that lies about what happened.

	Its own rows are stamped with where the archive came from, when it arrived and who brought it.
	This is the only path in this app that creates a submitted document it did not itself perform, and
	that is exactly why it asks for a named role (`core.authz.ENDPOINT_ROLES`).
	"""
	return _restore_export("restore_audit_records", export.MODE_RECORDS, content, token)


def _preview_export(endpoint, mode, content):
	"""The one implementation every preview shares, differing only in its mode and its name."""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, endpoint)
		authz.require_any_company(principal)
	except authz.AuthorizationError as error:
		_fail(error)

	document = _read_export(content)

	try:
		# The names the file mentions are the ones the plan asks about: a world built only from
		# what is already stored would call every row of an import into an empty site a conflict.
		#
		# The stamp is passed here for the same reason the apply passes it, and it is the same
		# stamp: the record mode places **records**, and a record that cannot say it was restored
		# is a record claiming the act happened here. Without it the planner refuses the file
		# outright — measured on the lab, the records preview answered
		# `EXPORT_BAD_SHAPE` for every file, so the button could never reach the apply.
		plan = export.plan(
			document,
			gateway.transfer_world(principal, **export.world_names(document)),
			mode=mode,
			stamp=_stamp(principal, document),
		)
	except export.ExportError as error:
		_fail(error)

	return {
		"token": document.get("digest"),
		"source_site": document.get("site"),
		"exported_on": document.get("exported_on"),
		"exported_by": document.get("exported_by"),
		"format_version": document.get("format_version"),
		"file_counts": document.get("counts"),
		**plan.summary(),
		"sample": list(export.sample(plan.conflicts)),
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def apply_serial_restore(content=None, token=None):
	"""Create the rows this site can place, in one transaction, and report the ones it cannot.

	This is the ordinary restore, and it places nothing on a **cancelled** invoice: a cancelled
	invoice's accounting has been reversed, so a registration added to it records a claim nothing
	supports. Those rows are reported with `INVOICE_CANCELLED`, and the one path that may place
	them is `restore_cancelled_serial_history`, which asks for a named role.

	The company of every assignment is taken from the live invoice rather than from the file, and
	`core.authz` decides whether this caller may write to it. A row whose invoice, item, company
	or invoice row is missing is reported and left alone — this endpoint never creates an Item, a
	Company or a Sales Invoice, and never edits one.

	`token` and `content` take a default so that a request missing one is **refused in words**
	rather than by a `TypeError` from the dispatcher: a browser that sent only the file got
	`missing 1 required positional argument: 'token'`, which says nothing to the operator and
	nothing about why. Neither default relaxes anything — a missing token is refused below, and a
	token that does not match the document is refused with it.
	"""
	return _restore_export("apply_serial_restore", export.MODE_LIVE, content, token)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def restore_cancelled_serial_history(content=None, token=None):
	"""Place a cancelled invoice's own registrations back onto it, from one of this app's exports.

	The one path in this app that may add a registration to a cancelled invoice, and the reason
	it is a path of its own rather than a flag on the ordinary restore. Every other write refuses
	one (I22): a cancelled invoice's accounting has been reversed, so a registration added to it
	records a claim nothing supports. What makes this different is what the rows *are* — a
	cancelled invoice's registrations are a faithful account of what a warehouse scanned, they
	were exported before the app was removed, and losing them across a reinstall loses scan
	history that cannot be reconstructed. Re-placing them restores a record of the past; it does
	not assert anything new, and this app never touches the invoice's accounting either way.

	Three things keep it from being a door into the ordinary path:

	- **It places only cancelled-invoice rows.** A row of the file that belongs to a live invoice
	  is reported as `NOT_HISTORY` and left to `apply_serial_restore`. The two endpoints are
	  disjoint; this one is not a superset of the other.
	- **It needs a named role on top of its level.** `core.authz.ENDPOINT_ROLES`: a Manager who
	  does not hold `Logistics Serial Archivist` is refused, so a site with several Managers
	  decides which of them may do this.
	- **The ordinary restore is pinned by its own tests.** It passes `CANCELLED_REFUSED` and
	  reports those rows as `INVOICE_CANCELLED`; the export and authorization tests fail if that
	  changes.

	What it does not have is a record of its own. A restore leaves each row's exported provenance
	on it — whose hands saw that serial, and when — and the file it was performed from is
	digest-sealed. That is exactly what `apply_serial_restore` does, and an act-record for one
	restore and not the other would be the inconsistency rather than the audit.
	"""
	return _restore_export("restore_cancelled_serial_history", export.MODE_HISTORY, content, token)


def _restore_export(endpoint, mode, content, token):
	"""The one implementation both restores share, differing only in their policy and their name.

	Written once because the ordering is the whole of the safety: authorize site-wide, read the
	file, plan it, refuse a token that is not the file's digest, authorize every company the plan
	will write to **from the stored invoices**, lock those invoices in name order, plan again
	under the locks, and write once. A second copy of that would be a second place for one of
	the steps to be dropped.
	"""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, endpoint)
		authz.require_any_company(principal)
	except authz.AuthorizationError as error:
		_fail(error)

	document = _read_export(content)

	if not token:
		# A request with no token is a request that never had a preview, and saying so is more
		# use to an operator than "the file is not the one you previewed" would be.
		frappe.throw(
			_("این درخواست نشانهٔ تأیید پیش‌نمایش را همراه ندارد. پیش‌نمایش را دوباره بگیرید."),
			title=_("بازیابی سریال‌ها"),
		)

	if token != document.get("digest"):
		frappe.throw(
			_("این فایل همان فایلی نیست که پیش‌نمایش شد. پیش‌نمایش را دوباره بگیرید."),
			title=_("بازیابی سریال‌ها"),
		)

	world = gateway.transfer_world(principal, **export.world_names(document))
	plan = export.plan(document, world, mode=mode, stamp=_stamp(principal, document))

	# Every invoice either list names, authorized from the **stored** document: an assignment
	# names the invoice it is written on, and a record names the one it points at. Asked of both,
	# because a caller who may not reach a record's target may not be told the record exists.
	named = {row.get("sales_invoice") for row in plan.assignments} | {
		row.get("target_invoice") for row in plan.transfers
	}

	for company in sorted({world.invoices.get(invoice) for invoice in named}):
		if company:
			authz.require_company(principal, company)

	gateway.open_transaction()

	# That plan was built from a world read before anything was locked, and a restore is a write
	# like any other: two of them over one invoice could each place up to the quantity the row had
	# when its world was read, and the row would end over it. So the invoices this file will write
	# to are locked — in name order, so two restores naming the same invoices take them in the
	# same order — and the world is read and the plan rebuilt under those locks. Only invoices with
	# something to write are locked: a file whose rows are all conflicts locks nothing.
	#
	# The second plan can only place fewer rows than the first, never more: the file is the same
	# and the world it is measured against has only gained the assignments of whoever went first,
	# so the companies authorized above are still the only ones that can be written to.
	for locked in sorted(invoice for invoice in named if invoice):
		gateway.lock_invoice(locked)

	plan = export.plan(
		document,
		gateway.transfer_world(principal, **export.world_names(document)),
		mode=mode,
		stamp=_stamp(principal, document),
	)

	try:
		created_serials = gateway.insert_exported(export.SERIAL_DOCTYPE, plan.serials)
		created_assignments = gateway.insert_exported(export.ASSIGNMENT_DOCTYPE, plan.assignments)
	except export.ExportError as error:
		frappe.db.rollback()
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.db.rollback()
		frappe.throw(
			_("هم‌زمان درخواست دیگری همین سریال‌ها را ثبت کرد. چیزی دوباره ثبت نشد؛ دوباره تلاش کنید."),
			title=_("بازیابی سریال‌ها"),
		)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	# The records, in their own transaction step and with their own sentence. A name that is
	# already taken is the one collision the pre-flight cannot always see: `world.transfers` holds
	# the names of the caller's own companies, because a record of another company is not a name
	# this caller may be told about — so a record named after one of those reaches the insert and
	# collides there. Reported as what it is rather than as a concurrent request, which is a
	# sentence that invites a retry that can never succeed.
	try:
		created_records = gateway.insert_transfers(plan.transfers, plan.transfer_items)
	except export.ExportError as error:
		frappe.db.rollback()
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.db.rollback()
		frappe.throw(
			_("نام یکی از سابقه‌های این فایل روی این سایت گرفته شده است. هیچ چیز بازنویسی نشد."),
			title=_("بازیابی سابقه‌ها"),
		)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	return {
		"created": {
			"serials": list(created_serials),
			"assignments": list(created_assignments),
			"transfers": list(created_records),
		},
		"counts": {
			"created": {"serials": len(created_serials), "assignments": len(created_assignments)},
			"skipped": {
				"serials": len(plan.skipped_serials),
				"assignments": len(plan.skipped_assignments),
			},
			"conflicts": len(plan.conflicts),
			"by_reason": export.count_by_reason(plan.conflicts),
		},
		"conflicts": list(export.sample(plan.conflicts, limit=len(plan.conflicts) or 1)),
		"message": _("بازیابی انجام شد: {0} سریال و {1} ردیف ثبت شد.").format(
			len(created_serials), len(created_assignments)
		),
	}


def _read_export(content):
	"""Parse an uploaded export, turning every refusal into a sentence rather than a traceback."""
	if isinstance(content, (bytes, bytearray)):
		size = len(content)
	else:
		size = len(str(content or "").encode("utf-8"))

	if not size:
		frappe.throw(_("فایلی فرستاده نشد."), title=_("بازیابی سریال‌ها"))

	if size > export.MAX_DOCUMENT_BYTES:
		frappe.throw(
			_("حجم فایل بیش از حد مجاز است."),
			title=_("بازیابی سریال‌ها"),
		)

	try:
		return export.read(content)
	except export.ExportError as error:
		frappe.throw(
			_("فایل پذیرفته نشد: {0} [{1}]").format(error.message, error.code),
			title=_("بازیابی سریال‌ها"),
		)


# --- moving serials onto the invoice that replaced a cancelled one -----------------------
#
# The only feature here that is about **two** invoices. Each end is authorized for what it is
# (`core.authz.require_transfer_invoices` — the source is read and is cancelled, the target is
# written to and is not), every rule is decided by `core.transfer`, which is a pure function and
# therefore testable without a site, and the write is one transaction: the registrations on the
# target and the record of what was done with them.

RECOVERY_HINT = "پیش‌نمایش را دوباره بگیرید."


def _transfer_selection(serials, policy):
	"""The identities a caller asked to move, from the texts it named.

	Called only when the caller named some. `None` and an empty list mean different things and both
	are meaningful: `None` is "every serial the source holds", which is what an operator who
	pressed the button and confirmed the default selected, and an empty list is a request to move
	nothing. The distinction is carried into the plan rather than collapsed here.

	A text that cannot be normalized at all is refused in words, the way the write paths refuse
	it: the alternative is a serial silently missing from the operation, and a list of ten that
	moved nine without saying so.
	"""
	if serials is None:
		return None

	chosen = set()

	for text in _parse_serials(serials):
		try:
			chosen.add(normalize_serial(text, policy=policy).normalized)
		except SerialTextError as error:
			_fail(error)

	return frozenset(chosen)


def _transfer_rows(mapping, policy):
	"""The target row each named serial was aimed at, keyed the way the planner reads it.

	Absent means "choose for me", which is the ordinary case: the plan fills the invoice's own rows
	in order. A caller that sent a mapping named rows for particular serials, and each is obeyed or
	refused with a reason — never quietly re-aimed somewhere else, which would be a transfer the
	operator did not ask for.
	"""
	if mapping is None:
		return {}

	if isinstance(mapping, str):
		try:
			mapping = json.loads(mapping)
		except json.JSONDecodeError:
			frappe.throw(_("نگاشت ردیف‌ها خوانده نشد."))

	if not isinstance(mapping, dict):
		frappe.throw(_("نگاشت ردیف‌ها باید یک شیء باشد."))

	aimed = {}

	for text, row_key in mapping.items():
		try:
			normalized = normalize_serial(str(text), policy=policy).normalized
		except SerialTextError as error:
			_fail(error)

		if not isinstance(row_key, str) or not row_key.strip():
			frappe.throw(_("ردیف مقصد باید نام ردیف فاکتور باشد."))

		aimed[normalized] = row_key

	return aimed


def _transfer_request(
	gateway,
	principal,
	endpoint,
	sales_invoice,
	target_invoice,
	serials=None,
	mapping=None,
	acknowledged=None,
):
	"""Read the site as a `core.transfer.TransferRequest`.

	Everything about the two documents comes off the database — their companies, their lifecycles,
	and whether one amends the other — and nothing about them comes off the request. What the
	caller supplies is which serials and which rows, which is the operator's decision to make and
	the only decision here that is.

	The two invoices are read first and authorized before anything else is read, in the order every
	write path in this file uses: an unauthorized caller learns nothing about the site, not even
	how many rows an invoice they cannot see has.
	"""
	source = gateway.read_invoice(sales_invoice)
	target = gateway.read_invoice(target_invoice)

	try:
		authz.require_transfer_invoices(principal, endpoint, source, target)
	except authz.AuthorizationError as error:
		_fail(error)

	# The framework's own read permission on each document, after the company check above: a caller
	# in the right company who still cannot read one of the two invoices must get the same refusal
	# as a caller naming a foreign invoice, so the answer never reveals which name is real.
	try:
		gateway.assert_can_read_invoice(source.name)
		gateway.assert_can_read_invoice(target.name)
	except frappe.PermissionError:
		_fail(authz.AuthorizationError(authz.CODE_INVOICE_NOT_FOUND, authz.INVOICE_REFUSAL))

	# Read here rather than handed in, so that no caller reads the site before the request it is
	# serving has been allowed: these are this app's own settings, and the policy built from them
	# decides nothing about whether the caller may act.
	settings = gateway.settings()
	policy = _policy(int(settings.serial_max_length or 60))
	held = gateway.read_source_serials(source.name)

	return transfer.TransferRequest(
		source_invoice=source.name,
		target_invoice=target.name,
		source_company=source.company,
		target_company=target.company,
		source_cancelled=source.docstatus == authz.DOCSTATUS_CANCELLED,
		target_cancelled=target.docstatus == authz.DOCSTATUS_CANCELLED,
		target_amends_source=gateway.read_amendment(source.name, target.name),
		serials=held,
		rows=gateway.read_target_rows(target.name),
		on_target=frozenset(row.normalized_serial for row in gateway.read_existing(target.name)),
		live_elsewhere=gateway.read_live_elsewhere(
			principal, [serial.normalized for serial in held], target.name
		),
		selection=_transfer_selection(serials, policy),
		mapping=_transfer_rows(mapping, policy),
		acknowledged=_transfer_selection(acknowledged, policy) or frozenset(),
	)


def _transfer_preview(plan, request):
	"""The operator's view of a plan: `core.transfer.preview_of` plus what it is drawn against.

	The rows and the counts are the planner's own answer, decided and tested there. What is added
	here is the target's rows as the window needs them, with `room` computed once on the server —
	so the select that aims a serial at a row compares a number it was given rather than working
	out how much room a row has, which is the rule `core.transfer` decides on.
	"""
	return {"target_rows": _transfer_rows_of(request.rows), **transfer.preview_of(plan, request)}


def _transfer_rows_of(rows):
	"""The target's rows as the window needs them: what each may take and what it holds.

	`room` is the subtraction, done here rather than in the window. The select that aims a serial
	at a row has to know which rows have space, and a window that worked it out for itself would be
	the second place the capacity rule is written — the first being `core.transfer`, which is the
	one that decides.
	"""
	return [
		{
			"row_key": row.row_key,
			"item_code": row.item_code,
			"index": row.index,
			"capacity": row.capacity,
			"held": row.held,
			"room": row.room,
			"serial_required": row.serial_required,
		}
		for row in rows
	]


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def preview_serial_transfer(sales_invoice, target_invoice, serials=None, mapping=None, acknowledged=None):
	"""What moving this invoice's serials onto its amendment would do, and what it would not.

	Read-only: it writes nothing, takes no lock and stores no session. The token that comes back
	is what the apply is checked against, so a window left open while somebody else used the site
	cannot move a different set of serials than the one it displayed.

	Nothing here refuses a serial that will not move. A row with no room left, an item the
	amendment no longer carries, a serial the target already holds — each is reported with its
	reason, because "eighteen went in and ten came out" is the situation this endpoint exists to
	make legible, and a single refusal would hide the other ten.
	"""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		request = _transfer_request(
			gateway,
			principal,
			"preview_serial_transfer",
			sales_invoice,
			target_invoice,
			serials=serials,
			mapping=mapping,
			acknowledged=acknowledged,
		)
		plan = transfer.plan_transfer(request)
	except authz.AuthorizationError as error:
		_fail(error)
	except transfer.TransferError as error:
		_fail(error, title=_("انتقال سریال"))

	return {
		"source_invoice": request.source_invoice,
		"target_invoice": request.target_invoice,
		"company": request.source_company,
		"token": transfer.preview_token(request),
		**_transfer_preview(plan, request),
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def apply_serial_transfer(
	sales_invoice, target_invoice, serials=None, mapping=None, acknowledged=None, token=None
):
	"""Move the confirmed serials onto the amendment, and record what was done with each.

	The plan is rebuilt from the database and revalidated under the target invoice's own lock,
	because the preview's answer was measured against a site that has since had time to change.
	Nothing is trusted from the preview except the token, which proves only *which selection* was
	confirmed — every condition behind the decision is asked again here, of the stored documents,
	and each new registration is derived by `core.assignment.derive_assignment_values` exactly as
	a typed one is.

	A selection naming a serial the source does not hold refuses the whole request. That is the one
	state in which the confirmed set and the moved set would differ, and a transfer that moved nine
	of the ten serials an operator confirmed is a transfer nobody agreed to. Every other refusal —
	no room, the item is gone, the target already holds it — is an outcome the preview showed and
	is written to the record with its reason.

	The source is not written to. Its registrations are read, named in the record, and left exactly
	as they were; a feature that preserved a serial by deleting the row that held it would be the
	same loss under a different name.
	"""
	if not token:
		frappe.throw(
			_("این درخواست نشانهٔ تأیید پیش‌نمایش را همراه ندارد. ") + _(RECOVERY_HINT),
			title=_("انتقال سریال"),
		)

	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		request = _transfer_request(
			gateway,
			principal,
			"apply_serial_transfer",
			sales_invoice,
			target_invoice,
			serials=serials,
			mapping=mapping,
			acknowledged=acknowledged,
		)
	except authz.AuthorizationError as error:
		_fail(error)

	if token != transfer.preview_token(request):
		frappe.throw(
			_("این درخواست همان انتخابی نیست که تأیید شد. ") + _(RECOVERY_HINT),
			title=_("انتقال سریال"),
		)

	try:
		gateway.open_transaction()
		gateway.lock_invoice(request.target_invoice)

		# Rebuilt under the lock rather than reused. How many serials a row has room for is
		# decided by what the target holds *now*, and a plan measured before the lock is a plan
		# measured against a site that has moved on — the same reason `run_rewrite` reads its rows
		# and its assignments again instead of trusting the preview's copy of them.
		request = _transfer_request(
			gateway,
			principal,
			"apply_serial_transfer",
			sales_invoice,
			target_invoice,
			serials=serials,
			mapping=mapping,
			acknowledged=acknowledged,
		)
		plan = transfer.plan_transfer(request)

		if plan.unknown_selection:
			frappe.throw(
				_("شماری از سریال‌های انتخاب‌شده روی فاکتور لغو‌شده نیستند. ") + _(RECOVERY_HINT),
				title=_("انتقال سریال"),
			)

		# Called whether or not the plan moves anything, and the record is written either way: a
		# preview that moved nothing is still an attempt an operator made, and the record of it
		# is what says which serials could not go and why. An empty tuple is zero inserts.
		gateway.insert_assignments(transfer.assignment_values(plan, request))

		record = transfer.record_of(plan, request)
	except authz.AuthorizationError as error:
		_fail(error)
	except transfer.TransferError as error:
		_fail(error, title=_("انتقال سریال"))
	except AssignmentIdentityError as error:
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.db.rollback()
		frappe.throw(
			_("هم‌زمان درخواست دیگری همین سریال‌ها را روی این فاکتور ثبت کرد. چیزی منتقل نشد."),
			title=_("انتقال سریال"),
		)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

	name = gateway.insert_transfer(record)
	counts = plan.counts()

	return {
		"transfer": name,
		"source_invoice": request.source_invoice,
		"target_invoice": request.target_invoice,
		"company": request.source_company,
		"token": token,
		**_transfer_preview(plan, request),
		"message": _("انتقال انجام شد: {0} سریال منتقل شد و {1} سریال روی فاکتور لغو‌شده ماند.").format(
			counts["moved"], counts["refused"] + counts["unselected"]
		),
	}
