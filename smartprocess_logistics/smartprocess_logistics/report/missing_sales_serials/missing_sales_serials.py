"""The Missing Sales Serials report.

The statement is built by `core.reports`, where the eligibility rule, the company isolation and
the permission conditions live, and the shortage is read from `core.quantities`, where the
requirement rule lives. This module is the four things a report has to supply and nothing else:
the filters, the acting principal, the installation timestamp, and the invoice permission
conditions the framework would apply to a list of the same rows.

Two things about it are worth stating.

**An invoice permission condition is required, and an empty one is a real answer.** ERPNext
registers no `permission_query_conditions` hook for `Sales Invoice` on a stock site, so the
condition is the empty string, and that is passed on rather than replaced with something of this
module's own invention. `core.reports.check_permission_conditions` refuses `None` — a caller that
forgot to ask — and accepts `""` — a framework that answered "no extra conditions".

**Nothing is filtered in Python.** The page the database returns is the page the reader sees, so
the shortage on a row is the shortage the database counted.
"""

import frappe
from frappe import _

from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core.quantities import row_coverage
from smartprocess_logistics.core.reports import (
	CODE_NOT_INSTALLED,
	MISSING_COLUMNS,
	MissingFilters,
	ReportError,
	build_missing_query,
)

# The column labels, in the order `core.reports` selects them, plus the shortage this module adds.
LABELS = {
	"sales_invoice": _("فاکتور فروش"),
	"sales_date": _("تاریخ سند"),
	"invoice_created": _("زمان ایجاد فاکتور"),
	"customer": _("مشتری"),
	"customer_name": _("نام مشتری"),
	"item_code": _("کالا"),
	"item_name": _("نام کالا"),
	"sales_invoice_item": _("ردیف فاکتور"),
	"row_index": _("شمارهٔ ردیف"),
	"invoiced_qty": _("تعداد فاکتور"),
	"required_units": _("نیاز"),
	"registered": _("ثبت‌شده"),
	"invoice_status": _("وضعیت"),
	"docstatus": _("وضعیت سند"),
	"company": _("شرکت"),
}

FIELD_TYPES = {
	"sales_invoice": ("Link", "Sales Invoice"),
	"sales_date": ("Date", None),
	"invoice_created": ("Datetime", None),
	"customer": ("Link", "Customer"),
	"item_code": ("Link", "Item"),
	"row_index": ("Int", None),
	"invoiced_qty": ("Float", None),
	"required_units": ("Int", None),
	"registered": ("Int", None),
	"docstatus": ("Int", None),
	"company": ("Link", "Company"),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})

	gateway = FrappeGateway()
	settings = gateway.settings()

	try:
		query = build_missing_query(
			_missing_filters(filters),
			gateway.principal(),
			installed_on=settings.installed_on,
			permission_conditions=_invoice_permission_conditions(),
		)
	except ReportError as error:
		# A report with no installation timestamp cannot be answered, and an empty table would
		# read as "every invoice is complete". The refusal says which of the two it is.
		if error.code == CODE_NOT_INSTALLED:
			frappe.throw(error.message, title=_("کسری سریال"))

		frappe.throw(error.message, title=_("کسری سریال"))

	rows = frappe.db.sql(query.sql, query.params, as_dict=True)

	return _columns(), _with_shortage(rows)


def _missing_filters(filters):
	"""The report's own filters, as `core.reports` expects them."""
	docstatuses = filters.get("docstatuses") or ()

	if isinstance(docstatuses, str):
		docstatuses = tuple(int(value) for value in docstatuses.split(",") if value.strip())

	return MissingFilters(
		sales_invoice=filters.get("sales_invoice"),
		customer=filters.get("customer"),
		item_code=filters.get("item_code"),
		from_date=filters.get("from_date"),
		to_date=filters.get("to_date"),
		company=filters.get("company"),
		docstatuses=tuple(docstatuses),
		include_cancelled=bool(filters.get("include_cancelled")),
		only_incomplete=True
		if filters.get("only_incomplete") is None
		else bool(filters.get("only_incomplete")),
		page_length=filters.get("page_length"),
		offset=filters.get("offset"),
	)


def _invoice_permission_conditions():
	"""What the framework would add to a list of these invoices, obtained the official way.

	`DatabaseQuery.get_permission_query_conditions` is the method the framework's own list
	queries use; it evaluates every `permission_query_conditions` hook for the DocType and
	returns what the caller's user is subject to. It answers with a **single string**, its own
	fragments already joined with ` and `, and it answers `""` on a stock site.

	That string is passed on as **one fragment**, not split back apart. Splitting looks harmless
	and is not: a fragment can contain a quoted literal that itself contains ` and `, and the
	caller wraps every fragment it is given in parentheses, so re-joining the pieces would move
	a bracket inside a string literal and change the statement. `core.reports` guards fragments
	against `;`, `--` and comment markers, none of which a condition of the framework's own
	contains, so one fragment is checked exactly as well as two.
	"""
	from frappe.model.db_query import DatabaseQuery

	conditions = DatabaseQuery("Sales Invoice").get_permission_query_conditions()

	return (conditions,) if conditions else ()


def _columns():
	columns = []

	for name in MISSING_COLUMNS:
		fieldtype, options = FIELD_TYPES.get(name, ("Data", None))

		column = {"fieldname": name, "label": LABELS.get(name, name), "fieldtype": fieldtype}

		if options:
			column["options"] = options

		columns.append(column)

	columns.append({"fieldname": "shortage", "label": _("کسری"), "fieldtype": "Int"})

	return columns


def _with_shortage(rows):
	"""Add the shortage, through the same function the rest of the app counts with."""
	for row in rows:
		coverage = row_coverage(
			row.get("sales_invoice_item") or row.get("sales_invoice"),
			row.get("item_code") or "",
			row.get("invoiced_qty") or 0,
			int(row.get("registered") or 0),
		)
		row["shortage"] = coverage.shortage

	return rows
