"""Query construction for the two reports. No database here.

Both reports are one query each. The alternative — fetching rows and asking for each row's
counts — is an N+1, which is why the registered count comes from a grouped subquery joined in
rather than from a call per row.

Every value a caller supplies becomes a **named parameter**, never text in the statement. An
`IN` list is expanded into one placeholder per value rather than relying on a driver to expand
a tuple, so nothing about the statement depends on the driver's own formatting rules:

	si.company IN (%(company_0)s, %(company_1)s)

The two reports differ in what they are *about*, and that difference drives most of the design:

- **Sales Serial Register** lists assignments. Its grain is one assignment, and its natural
  order is the sales date.
- **Missing Sales Serials** lists invoice item rows that should have serials and do not have
  enough of them. Its grain is one row. What it is short of is measured against what was
  **delivered**, not what was invoiced: serials label physical units, and a unit still in the
  warehouse needs no label. The delivered figure is net of submitted returns, and each
  registration's link state is decided by the same rules `core.delivery` applies in the dialog.

What the missing report can and cannot know
-------------------------------------------
A row qualifies when its **item's current** `sp_serial_required` flag is on, and for no other
reason. The invoice's age is not part of the question:

- **No date is an implicit filter.** The report once covered only invoices created at or after
  `Logistics Settings.installed_on`, which left every older invoice unaudited however short it
  was. What decides a shortage is the item's flag, read when the report runs, and that says
  nothing about when the invoice was written — so an invoice from before the app was installed is
  reported like any other, and stays reported across an upgrade, a reinstall, a restore, a move to
  another server, or a change to that timestamp. `core.eligibility` states the rule, and the
  install timestamp keeps the only job it ever really had: `setup/seeds.py` writes it and reads it
  back to know whether the app's own seeding finished.
- **Explicit filters stay explicit.** A date range the operator types is applied to the posting
  date, as it always was; nothing else is filtered by date at all.
- **An unmarked item cannot be short.** The flag is read as it is now, not as it was when the
  invoice was written, so marking an item adds its rows to the report and unmarking it removes
  them. That is the same rule the scan station applies when it calls a column optional.

A third thing the report deliberately does not do: an assignment whose invoice item row was
deleted is orphaned, it matches no row, and it therefore counts towards nothing. The Register
report shows it, with `is_orphaned` set, so it is visible rather than lost.
"""

from dataclasses import dataclass, field

from smartprocess_logistics.core.authz import visible_companies
from smartprocess_logistics.core.delivery import (
	FULLY_RETURNED_PERCENT,
	LINK_CANCELLED,
	LINK_DELIVERED,
	LINK_DRAFT,
	LINK_RETURNED,
	LINK_UNKNOWN,
	LINK_UNLINKED,
)

DEFAULT_PAGE_LENGTH = 50
MAX_PAGE_LENGTH = 500

# The requirement formula, as SQL. It mirrors `core.quantities.required_units` exactly: the
# absolute quantity, rounded up, with the same tolerance so a stored 2.0000000001 reads as 2.
# `tests/test_reports.py` compares this against the Python function so the two cannot drift.
REQUIRED_UNITS_SQL = "CEIL(GREATEST(ABS(sii.qty) - 0.000000001, 0))"

DOCSTATUS_DRAFT = 0
DOCSTATUS_SUBMITTED = 1
DOCSTATUS_CANCELLED = 2

CODE_BAD_PAGE = "REPORT_BAD_PAGE_LENGTH"
CODE_BAD_DATE_RANGE = "REPORT_BAD_DATE_RANGE"
CODE_BAD_DOCSTATUS = "REPORT_BAD_DOCSTATUS"
CODE_BAD_DOCSTATUS_IN_LIST = "REPORT_BAD_DOCSTATUS"
CODE_UNSAFE_CONDITION = "REPORT_UNSAFE_PERMISSION_CONDITION"
CODE_NO_CONDITION = "REPORT_NO_PERMISSION_CONDITIONS"

# Fragments that must never appear in a framework-supplied condition. The conditions come from
# `frappe.permissions.get_permission_query_conditions`, never from a request, and this is a
# cheap guard against the day somebody passes one that came from somewhere else.
FORBIDDEN_IN_CONDITION = (";", "--", "/*", "*/")

# What a row's serials add up to, as one label. It follows the delivery, not the invoice: a row
# whose units were never delivered cannot be missing a serial for them.
SERIAL_STATE_NO_SERIALS = "NO_SERIALS"
SERIAL_STATE_CANCELLED_OR_RETURNED = "CANCELLED_OR_RETURNED"
SERIAL_STATE_PARTIAL = "PARTIAL"
SERIAL_STATE_UNCONFIRMED = "UNCONFIRMED"
SERIAL_STATE_COMPLETE = "COMPLETE"

SERIAL_STATES = (
	SERIAL_STATE_NO_SERIALS,
	SERIAL_STATE_CANCELLED_OR_RETURNED,
	SERIAL_STATE_PARTIAL,
	SERIAL_STATE_UNCONFIRMED,
	SERIAL_STATE_COMPLETE,
)


class ReportError(ValueError):
	"""A report request that cannot be built. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class Query:
	"""A statement, its parameters, and the page it asks for."""

	sql: str
	params: dict = field(default_factory=dict)
	limit: int = DEFAULT_PAGE_LENGTH
	offset: int = 0

	def select_count(self):
		"""How many `SELECT` keywords the statement has.

		The count is a fixed property of the statement: `tests/test_reports.py` pins it. It is
		read as a guard against a statement assembled per row — the N+1 this module exists to
		avoid — because every extra `SELECT` here is a grouped CTE joined once, never a query
		whose shape depends on how many rows the page holds.
		"""
		return self.sql.upper().count("SELECT")

	def is_single_statement(self):
		"""Whether the statement is one statement. A report never contains a statement separator."""
		return ";" not in self.sql


def required_units_sql():
	"""The requirement formula as the reports use it."""
	return REQUIRED_UNITS_SQL


def check_permission_conditions(conditions):
	"""Refuse an empty condition set, and refuse an unsafe fragment.

	`permission_conditions` is required rather than optional on purpose. An optional argument
	defaulting to no conditions is an argument somebody forgets, and forgetting it means a
	report that ignores invoice permissions entirely. Passing `()` is a deliberate act.
	"""
	if conditions is None:
		raise ReportError(
			CODE_NO_CONDITION,
			"شرط‌های مجوز فاکتور باید صریحاً داده شوند؛ رشتهٔ خالی به‌معنای حذف بررسی دسترسی است.",
		)

	for condition in conditions:
		if not isinstance(condition, str) or not condition.strip():
			raise ReportError(CODE_UNSAFE_CONDITION, "شرط مجوز خالی یا نامعتبر است.")

		for forbidden in FORBIDDEN_IN_CONDITION:
			if forbidden in condition:
				raise ReportError(
					CODE_UNSAFE_CONDITION,
					f"شرط مجوز نویسهٔ ناایمن دارد: {forbidden!r}",
				)

	return tuple(conditions)


def _page(page_length, offset):
	"""Bound a page, whatever the caller asked for."""
	length = DEFAULT_PAGE_LENGTH if page_length in (None, "") else int(page_length)
	start = 0 if offset in (None, "") else int(offset)

	if length < 1 or length > MAX_PAGE_LENGTH:
		raise ReportError(
			CODE_BAD_PAGE,
			f"تعداد ردیف صفحه باید بین ۱ و {MAX_PAGE_LENGTH} باشد.",
		)

	if start < 0:
		raise ReportError(CODE_BAD_PAGE, "شروع صفحه نمی‌تواند منفی باشد.")

	return length, start


def _company_condition(principal, params, alias="si"):
	"""A fragment restricting the query to the companies the principal may see."""
	companies = visible_companies(principal)

	if companies is None:
		return None

	if not companies:
		# Strict company permissions with no company assigned. Nothing is visible, and saying so
		# with a condition that is always false is clearer than returning an empty page.
		return "1 = 0"

	placeholders = []

	for index, company in enumerate(sorted(companies)):
		key = f"company_{index}"
		placeholders.append(f"%({key})s")
		params[key] = company

	return f"{alias}.company IN ({', '.join(placeholders)})"


def _docstatus_condition(docstatuses, include_cancelled, params, alias="si"):
	"""A fragment for the requested statuses, with cancelled excluded unless asked for."""
	if docstatuses:
		values = tuple(docstatuses)

		for value in values:
			if isinstance(value, bool) or not isinstance(value, int):
				raise ReportError(CODE_BAD_DOCSTATUS_IN_LIST, "وضعیت سند باید عدد صحیح باشد.")

			if value not in (DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED, DOCSTATUS_CANCELLED):
				raise ReportError(
					CODE_BAD_DOCSTATUS_IN_LIST,
					f"وضعیت سند ناشناخته است: {value}",
				)

		placeholders = []

		for index, value in enumerate(sorted(set(values))):
			key = f"docstatus_{index}"
			placeholders.append(f"%({key})s")
			params[key] = value

		return f"{alias}.docstatus IN ({', '.join(placeholders)})"

	if include_cancelled:
		return None

	return f"{alias}.docstatus <> {DOCSTATUS_CANCELLED}"


def _date_condition(field_name, from_date, to_date, params, alias="si"):
	"""A fragment for a sales date range, or `None` when no range was asked for.

	There is no default window. A report that silently covers ninety days answers a question
	nobody asked, and it hides the rows outside it.
	"""
	column = f"{alias}.{field_name}"

	if from_date and to_date:
		if str(from_date) > str(to_date):
			raise ReportError(CODE_BAD_DATE_RANGE, "تاریخ شروع بعد از تاریخ پایان است.")

		params["from_date"] = from_date
		params["to_date"] = to_date

		return f"{column} BETWEEN %(from_date)s AND %(to_date)s"

	if from_date:
		params["from_date"] = from_date

		return f"{column} >= %(from_date)s"

	if to_date:
		params["to_date"] = to_date

		return f"{column} <= %(to_date)s"

	return None


def _equal_condition(column, value, key, params):
	if not value:
		return None

	params[key] = value

	return f"{column} = %({key})s"


def _like_condition(column, value, key, params):
	"""A contains-search, with the wildcards a user typed treated as ordinary characters.

	Without escaping, a serial containing `_` would match any character there and a serial
	containing `%` would match anything at all, so a search would return rows the operator did
	not ask for and cannot explain.
	"""
	if not value:
		return None

	escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	params[key] = f"%{escaped}%"

	return f"{column} LIKE %({key})s ESCAPE '\\\\'"


@dataclass(frozen=True)
class RegisterFilters:
	"""What the Sales Serial Register can be narrowed by."""

	sales_invoice: str = None
	customer: str = None
	item_code: str = None
	serial_no: str = None
	from_date: str = None
	to_date: str = None
	company: str = None
	docstatuses: tuple = ()
	is_return: bool = None
	include_cancelled: bool = True
	page_length: int = None
	offset: int = None


@dataclass(frozen=True)
class MissingFilters:
	"""What the Missing Sales Serials report can be narrowed by.

	`only_incomplete` defaults on: the report exists to show what is missing, and a caller that
	wants the complete rows too can ask for them. Incomplete means fewer serials than delivered
	units; a row nothing was delivered for is not short.
	"""

	sales_invoice: str = None
	customer: str = None
	item_code: str = None
	from_date: str = None
	to_date: str = None
	company: str = None
	docstatuses: tuple = ()
	is_return: bool = None
	only_incomplete: bool = True
	# A cancelled invoice is excluded unless the caller asks for it. The register does the
	# opposite, because a cancelled invoice's registrations stay viewable.
	include_cancelled: bool = False
	page_length: int = None
	offset: int = None


REGISTER_COLUMNS = (
	"sales_invoice",
	"sales_date",
	"customer",
	"customer_name",
	"item_code",
	"item_name",
	"serial_no",
	"registered_on",
	"registered_by",
	"invoice_status",
	"docstatus",
	"company",
	"assignment",
	"invoice_item_idx",
	"is_orphaned",
)

MISSING_COLUMNS = (
	"sales_invoice",
	"sales_date",
	"invoice_created",
	"customer",
	"customer_name",
	"item_code",
	"item_name",
	"sales_invoice_item",
	"row_index",
	"invoiced_qty",
	"required_units",
	"delivered_units",
	"registered",
	"missing_units",
	"confirmed",
	"unlinked",
	"cancelled_or_returned",
	"draft",
	"unknown",
	"serial_state",
	"invoice_status",
	"docstatus",
	"company",
)


def build_register_query(filters, principal, *, permission_conditions):
	"""The Sales Serial Register, as one parameterized statement.

	One row per assignment. A cancelled invoice's serials are listed unless the caller says
	otherwise, because a cancelled invoice's registrations stay viewable and editable.
	"""
	conditions = check_permission_conditions(permission_conditions)
	limit, offset = _page(filters.page_length, filters.offset)

	params = {"limit": limit, "offset": offset}
	where = []

	if filters.sales_invoice:
		where.append("lsa.sales_invoice = %(sales_invoice)s")
		params["sales_invoice"] = filters.sales_invoice

	where += [
		_company_condition(principal, params),
		_equal_condition("lsa.company", filters.company, "company", params),
		_equal_condition("si.customer", filters.customer, "customer", params),
		_equal_condition("lsa.item_code", filters.item_code, "item_code", params),
		_like_condition("ls.serial_no", filters.serial_no, "serial_no", params),
		_date_condition("posting_date", filters.from_date, filters.to_date, params),
		_docstatus_condition(filters.docstatuses, filters.include_cancelled, params),
	]

	if filters.is_return is not None:
		params["is_return"] = 1 if filters.is_return else 0
		where.append("COALESCE(si.is_return, 0) = %(is_return)s")

	where += list(conditions)

	return Query(
		sql=_REGISTER_SQL.format(where=_where(where)),
		params=params,
		limit=limit,
		offset=offset,
	)


def build_missing_query(filters, principal, *, permission_conditions):
	"""The Missing Sales Serials report, as one parameterized statement.

	One row per serial-required invoice item row. Nothing is filtered in Python after the page
	is taken, so the shortage count the caller reads is the shortage count the database counted.

	There is no installation-timestamp input, and that is the point: the report covers every
	invoice the site holds, whatever its age. The only date in the statement is a date range the
	operator typed.
	"""
	conditions = check_permission_conditions(permission_conditions)
	limit, offset = _page(filters.page_length, filters.offset)

	params = {"limit": limit, "offset": offset}
	where = [
		"sii.parenttype = 'Sales Invoice'",
		# The requirement rule: only a marked item can be short, so only a marked item is a row.
		# `core.eligibility` states it; this is where it reaches the statement.
		"i.sp_serial_required = 1",
		_company_condition(principal, params),
		_equal_condition("si.company", filters.company, "company", params),
		_equal_condition("si.customer", filters.customer, "customer", params),
		_equal_condition("sii.item_code", filters.item_code, "item_code", params),
		_date_condition("posting_date", filters.from_date, filters.to_date, params),
		_docstatus_condition(filters.docstatuses, filters.include_cancelled, params),
	]

	if filters.sales_invoice:
		where.append("si.name = %(sales_invoice)s")
		params["sales_invoice"] = filters.sales_invoice

	if filters.is_return is not None:
		params["is_return"] = 1 if filters.is_return else 0
		where.append("COALESCE(si.is_return, 0) = %(is_return)s")

	if filters.only_incomplete:
		# Short means "fewer serials than delivered units", so a row nothing was delivered for
		# is not short. Deliveries are matched by invoice item row, which is what the CTE keys on.
		where.append(
			"COALESCE(a.registered, 0) < GREATEST(COALESCE(u.delivered_units, 0), 0)"
		)

	where += list(conditions)

	# The placeholders are filled from values built in this module. Every value that came from a
	# caller is in `params`, not in the statement.
	return Query(
		sql=_MISSING_SQL.format(
			requirement=REQUIRED_UNITS_SQL,
			net_delivered="GREATEST(COALESCE(u.delivered_units, 0), 0)",
			shortfall=(
				"GREATEST(GREATEST(COALESCE(u.delivered_units, 0), 0)"
				" - COALESCE(a.registered, 0), 0)"
			),
			unlinked=LINK_UNLINKED,
			unknown=LINK_UNKNOWN,
			cancelled=LINK_CANCELLED,
			draft=LINK_DRAFT,
			returned=LINK_RETURNED,
			delivered=LINK_DELIVERED,
			returned_percent=FULLY_RETURNED_PERCENT,
			no_serials=SERIAL_STATE_NO_SERIALS,
			cancelled_state=SERIAL_STATE_CANCELLED_OR_RETURNED,
			partial_state=SERIAL_STATE_PARTIAL,
			unconfirmed_state=SERIAL_STATE_UNCONFIRMED,
			complete_state=SERIAL_STATE_COMPLETE,
			where=_where(where),
		),
		params=params,
		limit=limit,
		offset=offset,
	)


def _where(conditions):
	"""Join the conditions that survived, in a stable order."""
	surviving = [condition for condition in conditions if condition]

	if not surviving:
		return "1 = 1"

	return " AND ".join(f"({condition})" for condition in surviving)


_REGISTER_SQL = """
SELECT
	si.name AS sales_invoice,
	si.posting_date AS sales_date,
	si.customer AS customer,
	si.customer_name AS customer_name,
	lsa.item_code AS item_code,
	sii.item_name AS item_name,
	ls.serial_no AS serial_no,
	lsa.creation AS registered_on,
	lsa.owner AS registered_by,
	si.status AS invoice_status,
	si.docstatus AS docstatus,
	si.company AS company,
	lsa.name AS assignment,
	lsa.invoice_item_idx AS invoice_item_idx,
	CASE WHEN sii.name IS NULL THEN 1 ELSE 0 END AS is_orphaned
FROM `tabLogistics Serial Assignment` lsa
INNER JOIN `tabSales Invoice` si ON si.name = lsa.sales_invoice
INNER JOIN `tabLogistics Serial` ls ON ls.name = lsa.logistics_serial
LEFT JOIN `tabSales Invoice Item` sii
	ON sii.name = lsa.sales_invoice_item AND sii.parent = lsa.sales_invoice
WHERE {where}
ORDER BY si.posting_date DESC, si.name ASC, lsa.sales_invoice_item ASC, lsa.name ASC
LIMIT %(limit)s OFFSET %(offset)s
"""

# One statement, six grouped CTEs, and no per-row query. The shapes, in order:
#
# - `shipments` walks the invoice row's delivery note rows in both directions. A forward row
#   carries `si_detail` and is keyed under its own note; a return row carries `dn_detail`, no
#   `si_detail`, and is keyed under the note of the forward row it reverses. Splitting the two
#   is what lets a row's delivered units be net of its returns without counting a return as
#   shipped.
# - `row_supply` sums those note-level totals per invoice row.
# - `assignments` counts registrations per invoice row — the grouped subquery the report has
#   always had, kept so its cost does not grow with the page.
# - `link_states` repeats, per registration, the decision `core.delivery.link_state` makes for
#   one assignment. Both must agree, so its precedence is the same, including `per_returned`.
# - `link_counts` folds those states back into five counts per row.
#
# The final projection then reports the row, the delivery it is measured against, and the state.
# A cancelled invoice is still a row: its units were delivered, and the serials registered for
# them still exist.
_MISSING_SQL = """
WITH shipments AS (
	SELECT
		links.sales_invoice_item AS sales_invoice_item,
		links.delivery_note AS delivery_note,
		SUM(CASE WHEN links.is_forward = 1 AND links.note_docstatus = 1 THEN links.units ELSE 0 END) AS shipped_units,
		SUM(CASE WHEN links.is_forward = 0 AND links.note_docstatus = 1 THEN links.units ELSE 0 END) AS returned_units,
		SUM(CASE WHEN links.is_forward = 1 THEN 1 ELSE 0 END) AS forward_rows,
		SUM(CASE WHEN links.is_forward = 1 AND links.note_docstatus = 2 THEN 1 ELSE 0 END) AS cancelled_forwards,
		SUM(CASE WHEN links.is_forward = 1 AND links.note_docstatus = 1 THEN 1 ELSE 0 END) AS submitted_forwards,
		MAX(CASE WHEN links.is_forward = 1 THEN links.per_returned END) AS per_returned
	FROM (
		SELECT
			fwd.si_detail AS sales_invoice_item,
			dn.name AS delivery_note,
			1 AS is_forward,
			dn.docstatus AS note_docstatus,
			dn.per_returned AS per_returned,
			CEIL(GREATEST(ABS(fwd.qty) - 0.000000001, 0)) AS units
		FROM `tabDelivery Note Item` fwd
		INNER JOIN `tabDelivery Note` dn ON dn.name = fwd.parent
		WHERE fwd.parenttype = 'Delivery Note'
			AND COALESCE(dn.is_return, 0) = 0
			AND COALESCE(fwd.si_detail, '') <> ''
			AND COALESCE(fwd.qty, 0) <> 0
		UNION ALL
		SELECT
			fwd.si_detail AS sales_invoice_item,
			fwd.parent AS delivery_note,
			0 AS is_forward,
			ret_note.docstatus AS note_docstatus,
			0 AS per_returned,
			CEIL(GREATEST(ABS(ret.qty) - 0.000000001, 0)) AS units
		FROM `tabDelivery Note Item` ret
		INNER JOIN `tabDelivery Note` ret_note ON ret_note.name = ret.parent
		INNER JOIN `tabDelivery Note Item` fwd
			ON fwd.name = ret.dn_detail AND fwd.parenttype = 'Delivery Note'
		WHERE ret.parenttype = 'Delivery Note'
			AND COALESCE(ret_note.is_return, 0) = 1
			AND COALESCE(fwd.si_detail, '') <> ''
			AND COALESCE(ret.qty, 0) <> 0
	) links
	GROUP BY links.sales_invoice_item, links.delivery_note
),
row_supply AS (
	SELECT
		sales_invoice_item AS sales_invoice_item,
		SUM(shipped_units - returned_units) AS delivered_units
	FROM shipments
	GROUP BY sales_invoice_item
),
assignments AS (
	SELECT sales_invoice, sales_invoice_item, COUNT(*) AS registered
	FROM `tabLogistics Serial Assignment`
	GROUP BY sales_invoice, sales_invoice_item
),
link_states AS (
	SELECT
		lsa.sales_invoice AS sales_invoice,
		lsa.sales_invoice_item AS sales_invoice_item,
		CASE
			WHEN COALESCE(lsa.delivery_note, '') = '' THEN '{unlinked}'
			WHEN s.delivery_note IS NULL THEN '{unknown}'
			WHEN s.cancelled_forwards = s.forward_rows THEN '{cancelled}'
			WHEN s.submitted_forwards = 0 THEN '{draft}'
			WHEN COALESCE(s.per_returned, 0) >= {returned_percent}
				OR s.shipped_units - s.returned_units <= 0 THEN '{returned}'
			ELSE '{delivered}'
		END AS state
	FROM `tabLogistics Serial Assignment` lsa
	LEFT JOIN shipments s
		ON s.sales_invoice_item = lsa.sales_invoice_item
		AND s.delivery_note = lsa.delivery_note
),
link_counts AS (
	SELECT
		sales_invoice,
		sales_invoice_item,
		SUM(CASE WHEN state = '{delivered}' THEN 1 ELSE 0 END) AS confirmed,
		SUM(CASE WHEN state = '{unlinked}' THEN 1 ELSE 0 END) AS unlinked,
		SUM(CASE WHEN state IN ('{cancelled}', '{returned}') THEN 1 ELSE 0 END) AS cancelled_or_returned,
		SUM(CASE WHEN state = '{draft}' THEN 1 ELSE 0 END) AS draft,
		SUM(CASE WHEN state = '{unknown}' THEN 1 ELSE 0 END) AS unknown
	FROM link_states
	GROUP BY sales_invoice, sales_invoice_item
)
SELECT
	si.name AS sales_invoice,
	si.posting_date AS sales_date,
	si.creation AS invoice_created,
	si.customer AS customer,
	si.customer_name AS customer_name,
	sii.item_code AS item_code,
	sii.item_name AS item_name,
	sii.name AS sales_invoice_item,
	sii.idx AS row_index,
	sii.qty AS invoiced_qty,
	{requirement} AS required_units,
	{net_delivered} AS delivered_units,
	COALESCE(a.registered, 0) AS registered,
	{shortfall} AS missing_units,
	COALESCE(c.confirmed, 0) AS confirmed,
	COALESCE(c.unlinked, 0) AS unlinked,
	COALESCE(c.cancelled_or_returned, 0) AS cancelled_or_returned,
	COALESCE(c.draft, 0) AS draft,
	COALESCE(c.unknown, 0) AS unknown,
	CASE
		WHEN COALESCE(a.registered, 0) = 0 THEN '{no_serials}'
		WHEN COALESCE(c.cancelled_or_returned, 0) > 0
			AND COALESCE(c.cancelled_or_returned, 0) >= COALESCE(a.registered, 0)
			THEN '{cancelled_state}'
		WHEN {shortfall} > 0 THEN '{partial_state}'
		WHEN COALESCE(c.confirmed, 0) < {net_delivered}
			OR COALESCE(c.confirmed, 0) < COALESCE(a.registered, 0)
			THEN '{unconfirmed_state}'
		ELSE '{complete_state}'
	END AS serial_state,
	si.status AS invoice_status,
	si.docstatus AS docstatus,
	si.company AS company
FROM `tabSales Invoice Item` sii
INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
INNER JOIN `tabItem` i ON i.name = sii.item_code
LEFT JOIN assignments a ON a.sales_invoice = sii.parent AND a.sales_invoice_item = sii.name
LEFT JOIN row_supply u ON u.sales_invoice_item = sii.name
LEFT JOIN link_counts c ON c.sales_invoice = sii.parent AND c.sales_invoice_item = sii.name
WHERE {where}
ORDER BY si.posting_date DESC, si.name ASC, sii.idx ASC
LIMIT %(limit)s OFFSET %(offset)s
"""


def describe_reports():
	"""What each report answers, for a README or a report description."""
	return {
		"sales_serial_register": {
			"grain": "one row per serial assignment",
			"columns": REGISTER_COLUMNS,
		},
		"missing_sales_serials": {
			"grain": "one row per serial-required invoice item row",
			"columns": MISSING_COLUMNS,
		},
	}
