"""Which invoices the missing-serial report may cover.

The rule is deliberately narrow, because the app cannot know whether an item required a
serial before it was installed. An invoice is eligible only when the site recorded an
installation timestamp and the invoice was created at or after it:

- `Sales Invoice.creation` is the reference, not the posting date and not the submission
  date. A posting date is typed by a person and can be backdated, so it cannot decide
  eligibility, and the submission date is not stored on the invoice at all.
- An invoice created before the installation stays out even when it is submitted afterwards.
  Nothing distinguishes it from an invoice that predates the app.
- A cancelled invoice is out unless the caller asks for cancelled invoices explicitly.
- Before the first install there is no timestamp, and nothing is eligible. The report is
  empty rather than guessing.

Registration itself is not limited by any of this. An operator may register serial numbers on
an older invoice by hand; only the report stays out of the period it cannot vouch for.
"""

from datetime import datetime

DOCSTATUS_DRAFT = 0
DOCSTATUS_SUBMITTED = 1
DOCSTATUS_CANCELLED = 2

UNKNOWN_DOCSTATUS = "DOCSTATUS_UNKNOWN"

REASON_NOT_INSTALLED = "NOT_INSTALLED"
REASON_CREATED_BEFORE_INSTALL = "CREATED_BEFORE_INSTALL"
REASON_CANCELLED = "CANCELLED"

CODE_NOT_A_TIMESTAMP = "TIMESTAMP_NOT_A_TIMESTAMP"
CODE_TIMESTAMP_MISMATCH = "TIMESTAMP_AWARENESS_MISMATCH"
CODE_NOT_A_DOCSTATUS = "DOCSTATUS_NOT_AN_INTEGER"


class EligibilityError(ValueError):
	"""An input that cannot decide eligibility. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


def parse_timestamp(value):
	"""Accept a `datetime` or an ISO-8601 string and return a `datetime`.

	Only `datetime.fromisoformat` is used. No dynamic evaluation of any kind happens here.
	"""
	if isinstance(value, datetime):
		return value

	if isinstance(value, str):
		try:
			return datetime.fromisoformat(value.strip())
		except ValueError as error:
			raise EligibilityError(CODE_NOT_A_TIMESTAMP, f"زمان نامعتبر: {value!r}") from error

	raise EligibilityError(CODE_NOT_A_TIMESTAMP, "زمان باید datetime یا رشتهٔ ISO-8601 باشد.")


def check_comparable(created, installed):
	"""Refuse to compare a naive timestamp with an aware one."""
	if (created.tzinfo is None) != (installed.tzinfo is None):
		raise EligibilityError(
			CODE_TIMESTAMP_MISMATCH,
			"یکی از زمان‌ها منطقهٔ زمانی دارد و دیگری ندارد و قابل مقایسه نیستند.",
		)


def check_docstatus(docstatus):
	"""Refuse anything that is not Frappe's 0, 1 or 2."""
	if isinstance(docstatus, bool) or not isinstance(docstatus, int):
		raise EligibilityError(CODE_NOT_A_DOCSTATUS, "وضعیت سند باید عدد صحیح باشد.")

	if docstatus not in (DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED, DOCSTATUS_CANCELLED):
		raise EligibilityError(CODE_NOT_A_DOCSTATUS, f"وضعیت سند ناشناخته است: {docstatus}")

	return docstatus


def ineligibility_reason(*, invoice_creation, installed_on, docstatus, include_cancelled=False):
	"""Why an invoice is not eligible, or `None` when it is.

	Returning the reason rather than only a boolean keeps a report able to explain itself
	instead of silently dropping rows.
	"""
	if not installed_on:
		return REASON_NOT_INSTALLED

	check_docstatus(docstatus)

	if docstatus == DOCSTATUS_CANCELLED and not include_cancelled:
		return REASON_CANCELLED

	created = parse_timestamp(invoice_creation)
	installed = parse_timestamp(installed_on)
	check_comparable(created, installed)

	if created < installed:
		return REASON_CREATED_BEFORE_INSTALL

	return None


def is_report_eligible(**kwargs):
	"""Whether an invoice may appear in the missing-serial report."""
	return ineligibility_reason(**kwargs) is None
