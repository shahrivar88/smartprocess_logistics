"""The whitelisted endpoints.

Reachable as soon as the app is installed, and reachable only through `FrappeGateway`, which
is the one place in this app that talks to the ORM. An endpoint reads its arguments, asks
`core.authz`, and turns the answer into a `core` value; every read and every write it needs is a
method on the gateway, so the layer that could bypass a permission check is one file wide.

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

from smartprocess_logistics import __version__
from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core import authz, export, importer, orchestration
from smartprocess_logistics.core.assignment import AssignmentIdentityError, reject_supplied_identity
from smartprocess_logistics.core.delimiters import DelimiterError, split_typed_list
from smartprocess_logistics.core.limits import payload_limits
from smartprocess_logistics.core.markers import MarkerPatternError
from smartprocess_logistics.core.normalization import NormalizationPolicy, SerialTextError
from smartprocess_logistics.core.quantities import coverage_summary, row_coverage
from smartprocess_logistics.core.textfile import TextFileError, decode_text, payload_bytes
from smartprocess_logistics.core.units import describe_bytes

# How many assignment names one delete request may carry. This is a request-size guard, not
# the page length the operator configures, so it is its own constant rather than a settings row.
MAX_BULK_NAMES = 500

# What a data URL adds to the bytes it carries: `data:<type>;base64,` plus the third the
# encoding costs. Generous on purpose — it is a ceiling on nonsense, not a size check.
MAX_DATA_URL_OVERHEAD = 1024


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


def _status(gateway, invoice_name):
	"""The rows of an invoice with their coverage, built from stored assignments.

	The required count, the shortage, the excess and the totals all come from `core.quantities`,
	so the dialog that draws them holds no arithmetic of its own.

	Each row also carries the serials registered on it, as the text that was typed, in the order
	they were registered. The dialog puts them back in the row's box, which is what makes
	correcting one serial an edit rather than a retype of the row's whole set. One read of the
	assignments answers both the counts and that list, so the page is one query rather than two.
	"""
	rows = gateway.read_rows(invoice_name)
	assignments = gateway.list_assignments(invoice_name, 0, 0)

	counts = {}
	serials = {}

	for assignment in assignments:
		row_key = assignment["sales_invoice_item"]
		counts[row_key] = counts.get(row_key, 0) + 1

		if assignment.get("serial_no"):
			serials.setdefault(row_key, []).append(assignment["serial_no"])

	coverages = [
		row_coverage(row.row_key, row.item_code, row.invoiced_qty, counts.get(row.row_key, 0)) for row in rows
	]
	summary = coverage_summary(coverages)

	covered = [
		{
			"row_key": coverage.row_key,
			"row_index": position,
			"item_code": coverage.item_code,
			"invoiced_qty": coverage.invoiced_qty,
			"required": coverage.required,
			"registered": coverage.registered,
			"shortage": coverage.shortage,
			"excess": coverage.excess,
			"is_complete": coverage.is_complete,
			"serials": serials.get(coverage.row_key, []),
		}
		for position, coverage in enumerate(coverages, start=1)
	]

	return {
		"sales_invoice": invoice_name,
		"rows": covered,
		"summary": {
			"rows": summary.rows,
			"required": summary.required,
			"registered": summary.registered,
			"shortage": summary.shortage,
			"excess": summary.excess,
			"incomplete_rows": summary.incomplete_rows,
			"is_complete": summary.is_complete,
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
	limit, offset = _read_page(limit, offset, _limits(gateway))

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
	to it; sending fewer replaces it; sending none removes them all. `serials` is a list, or the
	text an operator typed, which the server splits with the configured delimiters.
	"""
	gateway = FrappeGateway()

	try:
		reject_supplied_identity(frappe.local.form_dict)
		serials = _row_serials(serials, gateway.enabled_delimiters())
	except (AssignmentIdentityError, DelimiterError) as error:
		_fail(error)

	settings = gateway.settings()
	submissions = tuple((sales_invoice_item, text) for text in serials)

	try:
		principal = gateway.principal()
		invoice = gateway.read_invoice(sales_invoice)

		# Authorization first, and the invoice's rows only once it has passed. The rows would not
		# be returned to an unauthorized caller either way, but nothing this app reads on behalf
		# of a request is read before the request is allowed to make it.
		authz.authorize(principal, "set_row_serials", invoice=invoice)

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
		preview = _preview(content, filename, request, _limits(gateway))
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
	encoded, payload = _load_payload(gateway, stored)

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
		# `now_datetime()`, not `now()`: the lifecycle compares this against the session's
		# `expires_at`, a Datetime column, and the core declares both as `datetime`. A string
		# made the comparison raise TypeError on every confirmation, and the same file already
		# uses `now_datetime()` to build `expires_at`.
		now=frappe.utils.now_datetime(),
	)

	try:
		outcome = orchestration.run_confirmation(plan, gateway)
	except orchestration.OrchestrationError as error:
		_fail(error)
	except frappe.exceptions.DuplicateEntryError:
		frappe.throw(_("این سریال از قبل روی همین فاکتور ثبت شده است."))
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		_contention()

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


def _preview(content, filename, request, limits):
	"""Read the payload as the file it claims to be.

	`payload_bytes` is what turns a data URL — the only way a browser can put an `.xlsx` into a
	JSON request — back into bytes, and encodes plain text as UTF-8 for `decode_text` to detect.

	The text is measured **before** it is decoded, not after. Every other limit in this app
	bounds what has already been read; this one bounds what is about to be. A data URL is at most
	a third larger than the bytes it carries plus its own prefix, so a string longer than that is
	a payload no client could have sent honestly, and refusing it here costs one `len()` instead
	of a base64 decode of whatever the request body happens to hold.
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
		return importer.preview_from_workbook(
			content, request, archive_limits=limits.archive, sheet_limits=limits.sheets
		)

	return importer.preview_from_text(
		decode_text(content, max_bytes=limits.max_bytes).text, request, max_tokens=limits.max_tokens
	)


def _store_session(gateway, invoice, preview, principal):
	"""Persist a prepared preview so confirmation can be single-use.

	The payload is stored as the **validated** result, not the raw file: the row each serial
	belongs to is decided now, and confirmation re-checks it against the database rather than
	trusting this copy.
	"""
	import secrets

	from frappe.utils import add_to_date

	settings = gateway.settings()
	submissions = [{"row_key": item.row_key, "serial": item.serial.original} for item in preview.accepted]
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


# --- exporting and importing this app's records --------------------------------------
#
# Three endpoints, in the order an operator uses them: take an export, look at what an import
# would do, then do it. The export is never written to the server's disk — the browser turns the
# document it receives into a download — which is what lets this app run on a stack whose app
# directory is mounted read-only.


@frappe.whitelist(methods=["GET"])
@rate_limit(limit=10, seconds=60)
def export_serials():
	"""Every serial and every assignment in the site, as one self-describing JSON document.

	The document carries the site name it came from, the format and application versions, the
	normalisation version in force, and a digest over its own contents. The digest is what makes
	an import able to tell a file that was edited in a text editor from one that was not.
	"""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, "export_serials")
	except authz.AuthorizationError as error:
		_fail(error)

	rows = gateway.export_rows()
	settings = gateway.settings()
	document = export.build(
		rows["serials"],
		rows["assignments"],
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

	The `token` it returns is the file's own digest, and the apply endpoint requires it back. It
	is not a secret and not a session: it is the answer to "is this still the file I previewed",
	which matters because the preview and the apply are two separate requests over a file the
	browser holds.
	"""
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, "preview_serial_restore")
	except authz.AuthorizationError as error:
		_fail(error)

	document = _read_export(content)

	try:
		# The names the file mentions are the ones the plan asks about: a world built only from
		# what is already stored would call every row of an import into an empty site a conflict.
		plan = export.plan(document, gateway.transfer_world(principal, **export.world_names(document)))
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
	gateway = FrappeGateway()

	try:
		principal = gateway.principal()
		authz.require_endpoint(principal, "apply_serial_restore")
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
	plan = export.plan(document, world)

	for company in sorted({world.invoices.get(row.get("sales_invoice")) for row in plan.assignments}):
		if company:
			authz.require_company(principal, company)

	gateway.open_transaction()

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

	return {
		"created": {"serials": list(created_serials), "assignments": list(created_assignments)},
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


def _load_payload(gateway, session):
	"""The stored payload and its text, or a refusal.

	The text is returned as well as the parsed value so the caller can recompute the hash from
	exactly the bytes that were stored.
	"""
	stored = gateway.read_session_payload(session.name)

	if not stored:
		frappe.throw(_("محتوای این پیش‌نمایش خالی است."))

	try:
		return stored, json.loads(stored)
	except json.JSONDecodeError:
		frappe.throw(_("محتوای این پیش‌نمایش خوانده نشد."))
