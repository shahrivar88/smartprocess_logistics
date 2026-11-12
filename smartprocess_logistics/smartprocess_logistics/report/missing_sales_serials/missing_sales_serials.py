"""The Missing Sales Serials report.

The statement is built by `core.reports`, where the requirement rule, the company isolation,
the delivery attribution and the permission conditions live. This module is the three things a
report has to supply and nothing else: the filters, the acting principal, and the invoice
permission conditions the framework would apply to a list of the same rows.

No installation timestamp is read here, and none is passed on. The report covers every invoice
the site holds; an invoice older than the app is audited like any other, because what decides a
shortage is the item's own flag, not when the invoice was written.

Two things about it are worth stating.

**An invoice permission condition is required, and an empty one is a real answer.** ERPNext
registers no `permission_query_conditions` hook for `Sales Invoice` on a stock site, so the
condition is the empty string, and that is passed on rather than replaced with something of this
module's own invention. `core.reports.check_permission_conditions` refuses `None` — a caller that
forgot to ask — and accepts `""` — a framework that answered "no extra conditions".

**Nothing is filtered in Python.** The page the database returns is the page the reader sees, so
the shortage on a row is the shortage the database counted. It is counted against the units the
row actually delivered — net of submitted returns — because a unit still in the warehouse needs
no label. `serial_state` names the case in one word, and the five link counts say why.
"""

import frappe
from frappe import _

from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core.reports import (
	MISSING_COLUMNS,
	MissingFilters,
	ReportError,
	build_missing_query,
)

# The column labels, in the order `core.reports` selects them.
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
	"required_units": _("نیاز فاکتور"),
	"delivered_units": _("تحویل‌شده"),
	"registered": _("ثبت‌شده"),
	"missing_units": _("کسری تحویل"),
	"confirmed": _("تأییدشده با تحویل"),
	"unlinked": _("موقت، بدون تحویل"),
	"cancelled_or_returned": _("لغو یا برگشتی"),
	"draft": _("رسید پیش‌نویس"),
	"unknown": _("نامشخص"),
	"serial_state": _("وضعیت سریال"),
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
	"delivered_units": ("Int", None),
	"registered": ("Int", None),
	"missing_units": ("Int", None),
	"confirmed": ("Int", None),
	"unlinked": ("Int", None),
	"cancelled_or_returned": ("Int", None),
	"draft": ("Int", None),
	"unknown": ("Int", None),
	"docstatus": ("Int", None),
	"company": ("Link", "Company"),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})

	gateway = FrappeGateway()

	try:
		query = build_missing_query(
			_missing_filters(filters),
			gateway.principal(),
			permission_conditions=_invoice_permission_conditions(),
		)
	except ReportError as error:
		frappe.throw(error.message, title=_("کسری سریال"))

	rows = frappe.db.sql(query.sql, query.params, as_dict=True)

	return _columns(), rows


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

	return columns
