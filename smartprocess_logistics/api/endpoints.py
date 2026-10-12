"""The whitelisted endpoints.

**Inactive.** The app is not installed, so none of these is reachable. They are written so the
wiring can be reviewed before it is switched on.

Six endpoints, and no helper is whitelisted. An endpoint does four things and nothing else:

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

from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core import authz, importer, orchestration
from smartprocess_logistics.core.assignment import AssignmentIdentityError, reject_supplied_identity
from smartprocess_logistics.core.delimiters import DelimiterError
from smartprocess_logistics.core.markers import MarkerPatternError
from smartprocess_logistics.core.normalization import NormalizationPolicy, SerialTextError
from smartprocess_logistics.core.textfile import TextFileError

MAX_PAGE_LENGTH = 500
DEFAULT_PAGE_LENGTH = 50


def _fail(error, title=None):
	"""Turn a `core` error into a Frappe exception, keeping its code in the message."""
	frappe.throw(
		_("{0} [{1}]").format(error.message, error.code),
		title=title or _("درج سریال"),
	)


def _read_page(limit, offset):
	"""Bound a page request, whatever the caller asked for."""
	limit = int(limit or DEFAULT_PAGE_LENGTH)
	offset = int(offset or 0)

	if limit < 1 or limit > MAX_PAGE_LENGTH:
		frappe.throw(_("تعداد ردیف صفحه باید بین ۱ و {0} باشد.").format(MAX_PAGE_LENGTH))

	if offset < 0:
		frappe.throw(_("شروع صفحه نمی‌تواند منفی باشد."))

	return limit, offset


def _status(gateway, invoice_name):
	"""The rows of an invoice with their coverage, built from stored assignments."""
	rows = gateway.read_rows(invoice_name)
	existing = gateway.read_existing(invoice_name)

	counts = {}

	for assignment in existing:
		counts[assignment.row_key] = counts.get(assignment.row_key, 0) + 1

	covered = []

	for position, row in enumerate(rows, start=1):
		covered.append(
			{
				"row_key": row.row_key,
				"row_index": position,
				"item_code": row.item_code,
				"invoiced_qty": row.invoiced_qty,
				"registered": counts.get(row.row_key, 0),
			}
		)

	return {"sales_invoice": invoice_name, "rows": covered}


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
	limit, offset = _read_page(limit, offset)

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)
		authz.authorize(principal, "get_serial_assignments", invoice=invoice)
	except authz.AuthorizationError as error:
		_fail(error)
	except frappe.PermissionError:
		_fail(authz.AuthorizationError(authz.CODE_NOT_PERMITTED, _("به این فاکتور دسترسی ندارید.")))

	gateway.assert_can_read_invoice(invoice.name)

	return {
		"sales_invoice": invoice.name,
		"limit": limit,
		"offset": offset,
		"assignments": [dict(row) for row in gateway.list_assignments(invoice.name, limit, offset)],
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def set_row_serials(sales_invoice, sales_invoice_item, serials):
	"""Set the serials of one invoice row: add, replace and remove in one transaction.

	The row's whole set is replaced. Sending the serials it already has plus the new ones adds
	to it; sending fewer replaces it; sending none removes them all.
	"""
	gateway = FrappeGateway()

	try:
		reject_supplied_identity(frappe.local.form_dict)
		serials = _parse_serials(serials)
	except AssignmentIdentityError as error:
		_fail(error)

	settings = gateway.settings()
	submissions = tuple((sales_invoice_item, text) for text in serials)

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)
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
			allow_over_quantity=bool(settings.allow_over_quantity),
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

	return {
		"sales_invoice": invoice.name,
		"sales_invoice_item": sales_invoice_item,
		"accepted": outcome.accepted,
		"deleted": outcome.deleted,
		"created_serials": list(outcome.created_serials),
		"serials_reused": outcome.serials_reused,
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
		stored = frappe.db.get_value(
			"Logistics Serial Assignment", name, ["sales_invoice", "company"], as_dict=True
		)

		if not stored:
			frappe.throw(_("ارجاع {0} پیدا نشد.").format(name))

		# The company is read from the stored row, never from the request.
		authz.require_company(principal, stored.company)

		if stored.sales_invoice not in invoices:
			gateway.assert_can_read_invoice(stored.sales_invoice)
			invoices[stored.sales_invoice] = True

	removed = gateway.delete_assignments(names)

	return {"deleted": removed, "names": list(names)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def prepare_import_preview(
	sales_invoice,
	content=None,
	filename=None,
	layout=None,
	row_mapping_confirmed=None,
):
	"""Parse a TXT or XLSX payload and return a preview. Nothing is written."""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)
		authz.authorize(principal, "prepare_import_preview", invoice=invoice)
	except authz.AuthorizationError as error:
		_fail(error)

	gateway.assert_can_read_invoice(invoice.name)

	rows = gateway.read_rows(invoice.name)
	existing = gateway.read_existing(invoice.name)
	request = gateway.import_request(
		invoice,
		rows=rows,
		existing=existing,
		principal=principal,
		layout=layout or importer.LAYOUT_GROUPED,
		row_mapping_confirmed=bool(row_mapping_confirmed),
	)

	try:
		preview = _preview(content, filename, request)
	except (TextFileError, DelimiterError, MarkerPatternError) as error:
		_fail(error)
	except ValueError as error:
		frappe.throw(str(error))

	session_name = None

	if preview.is_valid:
		session_name = _store_session(gateway, invoice, preview, principal)

	return {
		"sales_invoice": invoice.name,
		"layout": preview.layout,
		"candidates": preview.candidates,
		"sheets": list(preview.sheets),
		"encoding": preview.encoding,
		"is_valid": preview.is_valid,
		"row_mapping": list(preview.row_mapping),
		"requires_row_mapping_confirmation": preview.requires_row_mapping_confirmation,
		"groups": [group.__dict__ for group in preview.groups],
		"accepted": [
			{"row_key": item.row_key, "serial": item.serial.original, "identity": item.serial.normalized}
			for item in preview.accepted
		],
		"issues": [
			{
				"code": issue.code,
				"message": issue.message,
				"severity": issue.severity,
				"row_key": issue.row_key,
				"serials": list(issue.serials),
			}
			for issue in preview.issues
		],
		"session": session_name,
		"session_expires_in_minutes": int(gateway.settings().preview_ttl_minutes or 10),
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=60)
def confirm_import(token):
	"""Apply a prepared preview, once. The transaction ends with the request."""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, "confirm_import")
		stored = gateway.read_session(token)
	except authz.AuthorizationError as error:
		_fail(error)

	if not stored:
		frappe.throw(_("این پیش‌نمایش پیدا نشد."))

	authz.require_session_owner(principal, stored.owner)
	authz.require_company(principal, stored.company)

	invoice = gateway.read_invoice(stored.sales_invoice)
	authz.authorize(principal, "confirm_import", invoice=invoice, session_owner=stored.owner)
	gateway.assert_can_read_invoice(invoice.name)

	rows = gateway.read_rows(invoice.name)
	existing = gateway.read_existing(invoice.name)
	encoded, payload = _load_payload(stored)

	rewrite = orchestration.build_rewrite_plan(
		invoice=invoice,
		rows=rows,
		existing=existing,
		submissions=tuple((item["row_key"], item["serial"]) for item in payload["submissions"]),
		principal=principal,
		delimiters=tuple(payload.get("delimiters", ())),
		policy=_policy(payload.get("max_length", 60)),
		allow_over_quantity=bool(payload.get("allow_over_quantity")),
	)
	# The hash is recomputed from the payload that was just read, and the acting user is the
	# caller. Passing the session's own values would make both checks compare a value with
	# itself, which is a check that can never fail.
	plan = orchestration.build_confirmation_plan(
		session=stored,
		rewrite=rewrite,
		owner=principal.user,
		payload_hash=orchestration.payload_hash(encoded),
		now=frappe.utils.now(),
	)

	try:
		outcome = orchestration.run_confirmation(plan, gateway)
	except orchestration.OrchestrationError as error:
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.throw(_("این سریال از قبل روی همین فاکتور ثبت شده است."))

	return {
		"sales_invoice": invoice.name,
		"accepted": outcome.accepted,
		"deleted": outcome.deleted,
		"created_serials": list(outcome.created_serials),
		"serials_reused": outcome.serials_reused,
		"message": outcome.summary,
	}


# --- helpers, not whitelisted -------------------------------------------------------


def _policy(max_length):
	return NormalizationPolicy(max_length=max_length)


def _parse_serials(serials):
	"""Accept a list, or a JSON array in a string, and nothing else."""
	if isinstance(serials, str):
		try:
			serials = json.loads(serials)
		except json.JSONDecodeError:
			frappe.throw(_("فهرست سریال‌ها خوانده نشد."))

	if not isinstance(serials, (list, tuple)):
		frappe.throw(_("سریال‌ها باید فهرست باشند."))

	if len(serials) > importer.MAX_CANDIDATES:
		frappe.throw(_("تعداد سریال‌ها بیش از حد مجاز است."))

	return tuple(serials)


def _parse_names(names):
	"""Accept a list, or a JSON array in a string."""
	if isinstance(names, str):
		try:
			names = json.loads(names)
		except json.JSONDecodeError:
			frappe.throw(_("فهرست ارجاع‌ها خوانده نشد."))

	if not isinstance(names, (list, tuple)):
		frappe.throw(_("ارجاع‌ها باید فهرست باشند."))

	if len(names) > MAX_PAGE_LENGTH:
		frappe.throw(_("تعداد ارجاع‌ها بیش از حد مجاز است."))

	return tuple(str(name) for name in names)


def _preview(content, filename, request):
	"""Read the payload as the file it claims to be."""
	if not content:
		frappe.throw(_("محتوای فایل فرستاده نشده است."))

	if isinstance(content, str):
		content = content.encode()

	name = (filename or "").lower()

	if name.endswith((".xlsx", ".xlsm")):
		return importer.preview_from_workbook(content, request)

	return importer.preview_from_text(_decode(content), request)


def _decode(content):
	from smartprocess_logistics.core.textfile import decode_text

	return decode_text(content).text


def _store_session(gateway, invoice, preview, principal):
	"""Persist a prepared preview so confirmation can be single-use.

	The payload is stored as the **validated** result, not the raw file: the row each serial
	belongs to is decided now, and confirmation re-checks it against the database rather than
	trusting this copy.
	"""
	import secrets

	from frappe.utils import add_to_date

	settings = gateway.settings()
	submissions = [
		{"row_key": item.row_key, "serial": item.serial.original} for item in preview.accepted
	]
	payload = {
		"submissions": submissions,
		"delimiters": list(preview.delimiters),
		"max_length": int(settings.serial_max_length or 60),
		"allow_over_quantity": bool(settings.allow_over_quantity),
	}
	encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)

	return gateway.create_session(
		{
			"token": secrets.token_urlsafe(32),
			"status": "Prepared",
			"company": invoice.company,
			"sales_invoice": invoice.name,
			"payload": encoded,
			"payload_hash": orchestration.payload_hash(encoded),
			"row_count": len(submissions),
			"expires_at": add_to_date(
				frappe.utils.now_datetime(), minutes=int(settings.preview_ttl_minutes or 10)
			),
		}
	)


def _load_payload(session):
	"""The stored payload and its text, or a refusal.

	The text is returned as well as the parsed value so the caller can recompute the hash from
	exactly the bytes that were stored.
	"""
	stored = frappe.db.get_value("Logistics Import Session", session.name, "payload")

	if not stored:
		frappe.throw(_("محتوای این پیش‌نمایش خالی است."))

	try:
		return stored, json.loads(stored)
	except json.JSONDecodeError:
		frappe.throw(_("محتوای این پیش‌نمایش خوانده نشد."))
