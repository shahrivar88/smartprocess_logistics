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
  enough of them. Its grain is one row, and its registered count is what the grouped subquery
  provides.

What the missing report can and cannot know
-------------------------------------------
The report covers only invoices whose `Sales Invoice.creation` is at or after
`Logistics Settings.installed_on`, and a row qualifies only when its **item's current**
`sp_serial_required` flag is on. Both halves of that are worth stating plainly:

- Before the app was installed, nothing recorded whether an item needed a serial, so an invoice
  created earlier cannot be judged and is left out. An invoice created earlier and submitted
  later stays out too: creation is the marker, and submission is not stored on the invoice.
- The item flag is read **as it is now**, not as it was when the invoice was written. Turning
  the flag off on an item removes its older invoices from the report; turning it on adds them.
  That is unresolved historical semantics, not a defect in the query, and it is listed in
  `docs/integrity-invariants.md` rather than papered over with a snapshot nobody approved.

A third thing the report deliberately does not do: an assignment whose invoice item row was
deleted is orphaned, it matches no row, and it therefore counts towards nothing. The Register
report shows it, with `is_orphaned` set, so it is visible rather than lost.
"""

from dataclasses import dataclass, field

from smartprocess_logistics.core.authz import visible_companies

DEFAULT_PAGE_LENGTH = 50
MAX_PAGE_LENGTH = 500

# The requirement formula, as SQL. It mirrors `core.quantities.required_units` exactly: the
# absolute quantity, rounded up, with the same tolerance so a stored 2.0000000001 reads as 2.
# `tests/test_reports.py` compares this against the Python function so the two cannot drift.
REQUIRED_UNITS_SQL = "CEIL(GREATEST(ABS(sii.qty) - 0.000000001, 0))"

DOCSTATUS_DRAFT = 0
DOCSTATUS_SUBMITTED = 1
DOCSTATUS_CANCELLED = 2

CODE_NOT_INSTALLED = "REPORT_NOT_INSTALLED"
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

		Two is normal for the missing report, whose registered count comes from a subquery. More
		than that means somebody has started building a per-row query, which is the N+1 this
		module exists to avoid.
		"""
		return self.sql.upper().count("SELECT")

	def is_single_statement(self):
		"""Whether the statement is one statement. A report never contains a statement separator."""
		return ";" not in self.sql


def missing_report_available(installed_on):
	"""Whether the missing report can be run at all.

	Before the first install there is no eligibility boundary, so the honest answer is that the
	report cannot be produced: not that it is empty, which a reader would take to mean every
	invoice is complete.
	"""
	return bool(installed_on)


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
	wants the complete rows too can ask for them.
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
	"registered",
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


def build_missing_query(filters, principal, *, installed_on, permission_conditions):
	"""The Missing Sales Serials report, as one parameterized statement.

	One row per serial-required invoice item row. Nothing is filtered in Python after the page
	is taken, so the shortage count the caller reads is the shortage count the database counted.
	"""
	if not missing_report_available(installed_on):
		raise ReportError(
			CODE_NOT_INSTALLED,
			"گزارش کسری سریال تا زمانی که تاریخ نصب ماژول ثبت نشده باشد قابل اجرا نیست.",
		)

	conditions = check_permission_conditions(permission_conditions)
	limit, offset = _page(filters.page_length, filters.offset)

	params = {"installed_on": installed_on, "limit": limit, "offset": offset}
	where = [
		"sii.parenttype = 'Sales Invoice'",
		"i.sp_serial_required = 1",
		# The eligibility rule. A row created before the install cannot be judged, so it is
		# left out rather than guessed at.
		"si.creation >= %(installed_on)s",
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
		where.append(f"COALESCE(a.registered, 0) < {REQUIRED_UNITS_SQL}")

	where += list(conditions)

	# The two placeholders are filled from values built in this module. Every value that came
	# from a caller is in `params`, not in the statement.
	return Query(
		sql=_MISSING_SQL.format(requirement=REQUIRED_UNITS_SQL, where=_where(where)),
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

_MISSING_SQL = """
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
	COALESCE(a.registered, 0) AS registered,
	si.status AS invoice_status,
	si.docstatus AS docstatus,
	si.company AS company
FROM `tabSales Invoice Item` sii
INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
INNER JOIN `tabItem` i ON i.name = sii.item_code
LEFT JOIN (
	SELECT sales_invoice, sales_invoice_item, COUNT(*) AS registered
	FROM `tabLogistics Serial Assignment`
	GROUP BY sales_invoice, sales_invoice_item
) a ON a.sales_invoice = sii.parent AND a.sales_invoice_item = sii.name
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
