"""Everything above combined into one registration plan, plus the configuration checks.

`plan_registration` answers a single question: given the rows of one invoice, what an operator
submitted, and what is already registered on that invoice, which serials may be written and
what is wrong with the rest? It reads nothing and writes nothing, so it can be run before a
single row is inserted and tested without a database.

Duplicate handling follows the approved rule exactly. A serial is identified by Item plus its
normalized text, so the same serial may be registered on several invoices. Inside one invoice
the normalized text may appear only once, **even across two different item rows**, which is a
stricter rule than the identity rule and is reported by its own code.

Structural limits are raised rather than returned, because a request that is too large to
examine is not a business problem to show an operator; everything about the serials
themselves comes back as an issue so a caller can show all of them at once.
"""

from dataclasses import dataclass, field

from smartprocess_logistics.core import delimiters as delimiter_rules
from smartprocess_logistics.core import delivery as delivery_rules
from smartprocess_logistics.core import eligibility, normalization, quantities
from smartprocess_logistics.core import markers as marker_rules

MAX_ROWS = 2000
MAX_SUBMISSIONS = 5000

CODE_UNKNOWN_ROW = "ROW_UNKNOWN"
CODE_ROW_NOT_ELIGIBLE = "ROW_NOT_ELIGIBLE"
CODE_DUPLICATE_IN_ROW = "DUPLICATE_IN_ROW"
CODE_DUPLICATE_IN_INVOICE = "DUPLICATE_IN_INVOICE"
CODE_ALREADY_REGISTERED = "ALREADY_REGISTERED"
CODE_CONTAINS_DELIMITER = "CONTAINS_DELIMITER"
CODE_EXCESS_QUANTITY = "EXCESS_QUANTITY"
CODE_DELIVERY_CHOICE_REQUIRED = delivery_rules.CODE_DELIVERY_CHOICE_REQUIRED
CODE_DELIVERY_NOT_A_CANDIDATE = delivery_rules.CODE_DELIVERY_NOT_A_CANDIDATE

CODE_CONFIG_DELIMITER = "CONFIG_DELIMITER"
CODE_CONFIG_DELIMITER_DUPLICATE = "CONFIG_DELIMITER_DUPLICATE"
CODE_CONFIG_DELIMITER_OVERLAP = "CONFIG_DELIMITER_OVERLAP"
CODE_CONFIG_MARKER = "CONFIG_MARKER"
CODE_CONFIG_MARKER_AMBIGUOUS = "CONFIG_MARKER_AMBIGUOUS"
CODE_CONFIG_DELIMITER_MARKER_CONFLICT = "CONFIG_DELIMITER_MARKER_CONFLICT"

CODE_TOO_MANY_ROWS = "TOO_MANY_ROWS"
CODE_TOO_MANY_SUBMISSIONS = "TOO_MANY_SUBMISSIONS"
CODE_DUPLICATE_ROW_KEY = "DUPLICATE_ROW_KEY"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


class RegistrationError(ValueError):
	"""A request that cannot be examined at all. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class InvoiceRow:
	"""One item row of the invoice under registration.

	`serial_required` is the row's item's `sp_serial_required` flag, and it is **required rather
	than defaulted**: a row built without it would be a row whose eligibility nobody decided, and
	a default would decide it silently — in the direction that writes or in the direction that
	refuses, either way without anybody saying so. The gateway reads it from the item, which is
	the only place it exists.
	"""

	row_key: str
	item_code: str
	invoiced_qty: float
	serial_required: bool


@dataclass(frozen=True)
class ExistingAssignment:
	"""A serial already registered on this invoice."""

	row_key: str
	normalized_serial: str


@dataclass(frozen=True)
class SerialIssue:
	"""One thing wrong, or worth knowing, about one part of a request.

	`severity` separates a problem that must stop the write from a configuration fact the
	operator should simply be told about. An overlapping delimiter is the second kind: the
	longest match resolves it, so nothing is refused, but the operator should know that `,,`
	was read as one separator.
	"""

	code: str
	message: str
	row_key: str | None = None
	serials: tuple = field(default=())
	severity: str = SEVERITY_ERROR


@dataclass(frozen=True)
class AcceptedSerial:
	"""A serial that may be written, and the delivery it is written against.

	Both delivery values are empty together: a serial registered before any delivery exists is
	provisional, and the report keeps it apart from a confirmed delivery rather than inventing
	one for it.
	"""

	row_key: str
	item_code: str
	serial: normalization.NormalizedSerial
	delivery_note: str | None = None
	delivery_note_item: str | None = None


@dataclass(frozen=True)
class RegistrationPlan:
	"""The outcome of one registration request."""

	accepted: tuple
	issues: tuple
	coverages: tuple

	@property
	def errors(self):
		return tuple(issue for issue in self.issues if issue.severity == SEVERITY_ERROR)

	@property
	def warnings(self):
		return tuple(issue for issue in self.issues if issue.severity == SEVERITY_WARNING)

	@property
	def is_valid(self):
		return not self.errors

	def accepted_by_row(self):
		"""The normalized serials accepted for each row, keyed by row."""
		grouped = {}

		for item in self.accepted:
			grouped.setdefault(item.row_key, []).append(item.serial.normalized)

		return grouped

	def issue_codes(self):
		return tuple(issue.code for issue in self.issues)


def _check_request_size(rows, submissions):
	if len(rows) > MAX_ROWS:
		raise RegistrationError(CODE_TOO_MANY_ROWS, f"تعداد ردیف‌های فاکتور بیش از {MAX_ROWS} است.")

	if len(submissions) > MAX_SUBMISSIONS:
		raise RegistrationError(
			CODE_TOO_MANY_SUBMISSIONS,
			f"تعداد سریال‌های ارسالی بیش از {MAX_SUBMISSIONS} است.",
		)


def _index_rows(rows):
	indexed = {}

	for row in rows:
		if row.row_key in indexed:
			raise RegistrationError(CODE_DUPLICATE_ROW_KEY, f"ردیف تکراری در ورودی: {row.row_key}")
		indexed[row.row_key] = row

	return indexed


def _index_existing(existing):
	"""Counts per row and the set of serials already on the invoice."""
	counts = {}
	serials = set()
	rows_by_serial = {}

	for assignment in existing:
		counts[assignment.row_key] = counts.get(assignment.row_key, 0) + 1
		serials.add(assignment.normalized_serial)
		rows_by_serial[assignment.normalized_serial] = assignment.row_key

	return counts, serials, rows_by_serial


DELIVERY_MESSAGES = {
	delivery_rules.CODE_DELIVERY_CHOICE_REQUIRED: (
		"این ردیف بیش از یک رسید تحویل معتبر دارد؛ ابتدا رسید تحویل را انتخاب کنید."
	),
	delivery_rules.CODE_DELIVERY_NOT_A_CANDIDATE: (
		"رسید تحویل انتخاب‌شده برای این ردیف فاکتور معتبر یا قابل انتخاب نیست."
	),
}


def _missing_row_deliveries(rows_by_key, deliveries):
	"""Refuse a delivery lookup that skipped an invoice row.

	Reading a missing row as "no delivery" would silently turn a linked shipment into a
	provisional registration, which is the one direction the rule must never fail in.
	"""
	for row_key in rows_by_key:
		if row_key not in deliveries:
			raise RegistrationError(
				CODE_DELIVERY_NOT_A_CANDIDATE,
				f"رسیدهای تحویل ردیف {row_key} خوانده نشده‌اند.",
			)


def _resolve_row_delivery(deliveries, selections, row_key, cache):
	"""The delivery decision for one row, decided once and reused for its serials."""
	if row_key in cache:
		return cache[row_key]

	if deliveries is None:
		choice = delivery_rules.DeliveryChoice(status=delivery_rules.STATUS_PROVISIONAL)
	else:
		choice = delivery_rules.select_delivery(
			deliveries.get(row_key, ()),
			requested_note=selections.get(row_key),
		)

	cache[row_key] = choice

	return choice


def _delimiter_conflict(serial, delimiter_set):
	"""The delimiter that appears inside a serial, if any.

	Only the normalized text is examined, and that is the stricter check: NFKC folds
	full-width punctuation, so a full-width comma in the input becomes the ASCII comma the
	delimiter is and is caught here even though the raw text did not contain it.
	"""
	for delimiter in delimiter_set:
		if delimiter in serial.normalized:
			return delimiter

	return None


def plan_registration(
	*,
	rows,
	submissions,
	existing=(),
	replaced_rows=(),
	delimiters=(),
	policy=None,
	deliveries=None,
	selections=None,
):
	"""Decide what may be registered and what is wrong.

	`rows` are the invoice item rows, `submissions` are `(row_key, serial text)` pairs in the
	order the operator supplied them, and `existing` are the assignments already stored on
	this invoice.

	`replaced_rows` are the rows whose whole set is about to be removed and written again: the
	one row `set_row_serials` names, and none at all for an import, which adds to the rows a
	file names. A replaced row's own assignments are counted out below, because they are gone
	before the new set is written — the set being judged is that row's whole set, so its own
	serials are not duplicates and are not still on the row when its requirement is compared.
	A serial that lives on a row which is **not** being replaced stays in the set of
	already-registered serials, and stays refused.

	`deliveries` maps a row key to its `DeliveryCandidate` rows and `selections` maps a row key
	to the delivery note an operator named for it. When `deliveries` is `None` nothing is known
	about deliveries — the file import path, which carries no delivery — and every accepted
	serial is provisional. When it is a mapping, every row of `rows` must have a key, empty
	tuple included: an absent key would be a row whose delivery nobody looked up, and reading
	that as "no delivery" would mark a linked shipment as provisional.
	"""
	policy = policy or normalization.NormalizationPolicy()
	delimiter_set = delimiter_rules.DelimiterSet.of(delimiters)

	_check_request_size(rows, submissions)

	rows_by_key = _index_rows(rows)
	existing_counts, existing_serials, _ = _index_existing(existing)

	if deliveries is not None:
		_missing_row_deliveries(rows_by_key, deliveries)

	selections = selections or {}
	resolved = {}

	if replaced_rows:
		replaced = frozenset(replaced_rows)
		existing_counts = {
			row_key: count for row_key, count in existing_counts.items() if row_key not in replaced
		}
		# Rebuilt from the assignments, not from `_index_existing`'s serial-to-row map: that map
		# keeps one row per serial, so a serial stored on two rows would be judged by whichever
		# row it happened to record last. A serial that is still on a row nobody is replacing has
		# to keep refusing the submission, in either iteration order.
		existing_serials = {item.normalized_serial for item in existing if item.row_key not in replaced}

	accepted = []
	issues = []
	seen_in_submission = {}
	accepted_counts = {}

	for row_key, raw_serial in submissions:
		row = rows_by_key.get(row_key)

		if row is None:
			issues.append(
				SerialIssue(
					code=CODE_UNKNOWN_ROW,
					message=f"ردیف فاکتور پیدا نشد: {row_key}",
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		# A row whose item is not marked for serials is not a row this app registers on, and the
		# window is not the authority on that: the window does not draw it, and this refuses it, so
		# a request that names one — an old client, a script, a window that was not reloaded — is
		# answered rather than obeyed. It is a rule about **adding** a label: `delete_assignments`
		# does not come through here, so the registrations such a row already holds stay removable,
		# and a row is never silently given to another row's item.
		if not eligibility.is_eligible(row.serial_required):
			issues.append(
				SerialIssue(
					code=CODE_ROW_NOT_ELIGIBLE,
					message="برای کالای این ردیف ثبت سریال فعال نیست.",
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		# One decision per row, made once: the same row's serials all belong to the same
		# delivery, and a row whose delivery cannot be decided refuses all of them for the same
		# reason rather than reporting a different problem per serial.
		choice = _resolve_row_delivery(deliveries, selections, row_key, resolved)

		if choice.code is not None:
			issues.append(
				SerialIssue(
					code=choice.code,
					message=DELIVERY_MESSAGES[choice.code],
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		try:
			serial = normalization.normalize_serial(raw_serial, policy=policy)
		except normalization.SerialTextError as error:
			issues.append(
				SerialIssue(
					code=error.code,
					message=error.message,
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		conflict = _delimiter_conflict(serial, delimiter_set)

		if conflict is not None:
			issues.append(
				SerialIssue(
					code=CODE_CONTAINS_DELIMITER,
					message=f"شمارهٔ سریال جداکنندهٔ فعال «{conflict}» دارد.",
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		if serial.normalized in existing_serials:
			issues.append(
				SerialIssue(
					code=CODE_ALREADY_REGISTERED,
					message="این سریال از قبل روی همین فاکتور ثبت شده است.",
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		first_row = seen_in_submission.get(serial.normalized)

		if first_row is not None:
			same_row = first_row == row_key
			issues.append(
				SerialIssue(
					code=CODE_DUPLICATE_IN_ROW if same_row else CODE_DUPLICATE_IN_INVOICE,
					message=(
						"این سریال در همین ردیف دو بار آمده است."
						if same_row
						else "این سریال در یک فاکتور فقط یک بار مجاز است، حتی در ردیف‌های مختلف."
					),
					row_key=row_key,
					serials=(raw_serial,),
				)
			)
			continue

		seen_in_submission[serial.normalized] = row_key
		accepted_counts[row_key] = accepted_counts.get(row_key, 0) + 1
		accepted.append(
			AcceptedSerial(
				row_key=row_key,
				item_code=row.item_code,
				serial=serial,
				delivery_note=choice.delivery_note,
				delivery_note_item=choice.delivery_note_item,
			)
		)

	# Unconditional: a row may never hold more serials than its invoiced quantity. This used to be
	# switched off by `allow_over_quantity`, and it is not optional any more — the cap is the rule,
	# not a site preference, and every registration path comes through here.
	issues += _excess_issues(rows_by_key, existing_counts, accepted_counts, accepted)

	coverages = _coverages(rows_by_key, existing_counts, accepted_counts)

	return RegistrationPlan(accepted=tuple(accepted), issues=tuple(issues), coverages=coverages)


def _excess_issues(rows_by_key, existing_counts, accepted_counts, accepted):
	"""Rows that would hold more serials than their quantity allows.

	The first serials in submission order fill the row and the ones after that are the excess,
	so which serials are named in the message does not depend on dictionary order.
	"""
	issues = []

	for row_key, row in rows_by_key.items():
		required = quantities.required_units(row.invoiced_qty)
		already = existing_counts.get(row_key, 0)

		# A row that is already full leaves no room, so every new serial is excess. Clamping
		# at zero keeps the slice below from reading backwards into the accepted list.
		allowed_for_row = max(0, required - already)
		newly_accepted = accepted_counts.get(row_key, 0)

		if newly_accepted <= allowed_for_row:
			continue

		accepted_for_row = [item for item in accepted if item.row_key == row_key]
		overflow = [item.serial.original for item in accepted_for_row[allowed_for_row:]]

		issues.append(
			SerialIssue(
				code=CODE_EXCESS_QUANTITY,
				message=(f"تعداد سریال بیش از تعداد فاکتور است؛ {len(overflow)} سریال اضافه است."),
				row_key=row_key,
				serials=tuple(overflow),
			)
		)

	return issues


def _coverages(rows_by_key, existing_counts, accepted_counts):
	coverages = []

	for row_key, row in rows_by_key.items():
		registered = existing_counts.get(row_key, 0) + accepted_counts.get(row_key, 0)
		coverages.append(quantities.row_coverage(row_key, row.item_code, row.invoiced_qty, registered))

	return tuple(coverages)


def check_configuration(*, delimiters=(), patterns=()):
	"""Everything wrong with the delimiter and marker configuration, and nothing else.

	The last check is the one that is easy to miss: a delimiter that appears inside a marker
	pattern splits the marker apart before it is ever recognised, so the marker can never be
	read and the grouped import silently becomes a flat list of serials.
	"""
	issues = []
	usable_delimiters = []

	for stored in delimiters:
		try:
			usable_delimiters.append(delimiter_rules.validate_delimiter(stored))
		except delimiter_rules.DelimiterError as error:
			issues.append(SerialIssue(code=CODE_CONFIG_DELIMITER, message=error.message))

	for duplicate in delimiter_rules.find_duplicates(usable_delimiters):
		issues.append(
			SerialIssue(
				code=CODE_CONFIG_DELIMITER_DUPLICATE,
				message=f"جداکنندهٔ تکراری در تنظیمات: {duplicate!r}",
			)
		)

	for longer, shorter in delimiter_rules.find_overlaps(usable_delimiters):
		issues.append(
			SerialIssue(
				code=CODE_CONFIG_DELIMITER_OVERLAP,
				message=(f"جداکنندهٔ {shorter!r} داخل {longer!r} قرار دارد؛ بلندترین جداکننده برنده می‌شود."),
				severity=SEVERITY_WARNING,
			)
		)

	parsed_patterns = []

	for pattern in patterns:
		try:
			parsed_patterns.append(marker_rules.parse_pattern(pattern))
		except marker_rules.MarkerPatternError as error:
			issues.append(SerialIssue(code=CODE_CONFIG_MARKER, message=error.message))

	for first, second in marker_rules.find_ambiguous_patterns(pattern.pattern for pattern in parsed_patterns):
		issues.append(
			SerialIssue(
				code=CODE_CONFIG_MARKER_AMBIGUOUS,
				message=f"دو الگوی نشانگر یک نشانگر را توصیف می‌کنند: {first!r} و {second!r}",
			)
		)

	for pattern in parsed_patterns:
		for delimiter in usable_delimiters:
			literal = pattern.pattern.replace(marker_rules.PLACEHOLDER, "")
			if delimiter in literal:
				issues.append(
					SerialIssue(
						code=CODE_CONFIG_DELIMITER_MARKER_CONFLICT,
						message=(
							f"جداکنندهٔ {delimiter!r} داخل الگوی نشانگر {pattern.pattern!r} است"
							" و نشانگر را از هم می‌شکند."
						),
					)
				)

	return tuple(issues)
