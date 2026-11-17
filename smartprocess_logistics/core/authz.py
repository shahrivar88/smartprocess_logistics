"""Who may do what, decided without a database.

Every endpoint's authorization is one pure function over a `Principal` and the things the
caller asked for. The Frappe layer builds a `Principal` from the session and hands it here,
which is what makes the decisions testable without a site and what keeps them in one place
instead of scattered through request handlers.

Three levels, and the strongest role a user holds decides theirs:

| Role | Level | May |
| ---- | ----- | --- |
| `Logistics Serial Viewer` | viewer | read status and assignments |
| `Logistics Serial Editor` | editor | add, replace and remove serials on an invoice |
| `Logistics Serial Manager` | manager | administrative operations and settings |
| `System Manager` | manager | everything, as it already can everywhere |

Two rules are worth stating because they are the ones an endpoint gets wrong:

**The company is never taken from the request.** A caller can send any company it likes. Every
company check reads the company off the invoice that was fetched by name, so a caller from
another company cannot reach an invoice by naming it. That is the IDOR defence, and it is the
reason `InvoiceRef` carries the company rather than accepting one alongside it.

**A missing company list is not a wildcard unless the framework says so.** A user with no
Company user permission normally sees every company, and `apply_strict_user_permissions` turns
that into a denial. Both behaviours are reproduced here rather than assumed, because the
difference is a security decision the site makes, not this app.
"""

from dataclasses import dataclass

VIEWER = "viewer"
EDITOR = "editor"
MANAGER = "manager"

ROLE_VIEWER = "Logistics Serial Viewer"
ROLE_EDITOR = "Logistics Serial Editor"
ROLE_MANAGER = "Logistics Serial Manager"
ROLE_ARCHIVIST = "Logistics Serial Archivist"
ROLE_SYSTEM_MANAGER = "System Manager"

LEVEL_RANK = {VIEWER: 1, EDITOR: 2, MANAGER: 3}

LEVEL_BY_ROLE = {
	ROLE_VIEWER: VIEWER,
	ROLE_EDITOR: EDITOR,
	ROLE_MANAGER: MANAGER,
	ROLE_SYSTEM_MANAGER: MANAGER,
}

ENDPOINT_LEVELS = {
	"get_invoice_serial_status": VIEWER,
	"get_serial_assignments": VIEWER,
	# The three quantities on the invoice panel: a read, scoped by the caller's companies and
	# by the framework's read permission on the item, warehouse and company.
	"get_sellable_stock": VIEWER,
	"set_row_serials": EDITOR,
	# One serial, added to one row. The scan station calls this once per label; it is the same
	# level as typing a row's whole set, because it is the same act performed one label at a time.
	"add_row_serial": EDITOR,
	# The same act for a batch of labels on one row — one answer for one row instead of one answer
	# per label. The same level, for the same reason: it is the same write.
	"add_row_serials": EDITOR,
	"delete_assignments": MANAGER,
	"prepare_import_preview": EDITOR,
	# Exporting and importing this app's records. An export is a copy of every serial and every
	# assignment in the site, and an import can create them; both are administrative, so both
	# need a Manager and neither is offered to a Viewer or an Editor.
	"export_serials": MANAGER,
	"preview_serial_restore": MANAGER,
	"apply_serial_restore": MANAGER,
	# Moving serials from a cancelled invoice to the invoice that amended it. An Editor, because
	# it is an ordinary registration on a live invoice performed in bulk, and the serial is the
	# one the operator already had in hand.
	"preview_serial_transfer": EDITOR,
	"apply_serial_transfer": EDITOR,
	# Re-placing a cancelled invoice's own registrations from an export. A Manager **and** a named
	# role — see `ENDPOINT_ROLES` for why the level is not enough on its own.
	# The historical restore, preview and apply alike: both are gated on the role below, and
	# the preview is gated with the apply because it reports which rows would be placed rather
	# than merely that they exist.
	"preview_cancelled_serial_history": MANAGER,
	"restore_cancelled_serial_history": MANAGER,
	# Placing the audit records themselves. The same act in kind, and the same role: it creates
	# submitted documents that record acts performed elsewhere.
	"preview_restore_audit_records": MANAGER,
	"restore_audit_records": MANAGER,
}

# The endpoints that need a named role **in addition** to their level.
#
# A level says how much of the site a caller works with — read, edit, administer — and every role
# that reaches a level reaches everything at it. One act is not like that. Adding a registration to
# an invoice whose accounting has been reversed is refused everywhere else in this app (I22), and
# being able to administer the app does not imply being trusted with it: a site may have several
# Managers and mean only one of them by this. So it is named, and a Manager without the role is
# refused with a sentence that says which role is missing rather than a bare "not permitted".
#
# `System Manager` satisfies it, as it satisfies everything else here. That is the app's existing
# convention rather than a decision made for this endpoint, and it is the reason the role is a gate
# on Managers instead of on the site: refusing the Administrator would leave a site that lost track
# of its roles with no way to do a legitimate thing this app ships.
ENDPOINT_ROLES = {
	"preview_cancelled_serial_history": ROLE_ARCHIVIST,
	"restore_cancelled_serial_history": ROLE_ARCHIVIST,
	"preview_restore_audit_records": ROLE_ARCHIVIST,
	"restore_audit_records": ROLE_ARCHIVIST,
}

# The endpoints that move serials from a cancelled invoice to its amendment. They are the only
# ones about **two** invoices, and each end is checked for what it is rather than for the same
# thing twice: the target is written to and must be reachable as a live invoice, while the source
# is read and is cancelled by definition.
#
# Neither is in `WRITING_ENDPOINTS`, and that is not an oversight. That tuple means "this endpoint
# puts a registration onto the invoice it names", and it is what refuses a cancelled invoice. A
# transfer puts its registrations on the *target* and reads the cancelled one, so adding it there
# would refuse the source it exists to read. What keeps a transfer from writing onto a cancelled
# invoice is `core.transfer.plan_transfer`, which refuses a cancelled target — a rule with a test
# on it rather than a list membership.
TRANSFER_ENDPOINTS = ("preview_serial_transfer", "apply_serial_transfer")

# Endpoints that a session may only be confirmed by the user who prepared it.
SESSION_BOUND_ENDPOINTS = ()  # no endpoint is session-bound any more

# The endpoints that put a registration onto an invoice. These refuse a cancelled invoice; every
# other endpoint may read and remove what one holds, because a registration added to an invoice
# that was later cancelled is exactly the mistake somebody has to be able to see and undo. The
# list is here, beside the levels, so that "may write to this invoice" is decided in one place
# rather than in the client that draws the button.
WRITING_ENDPOINTS = (
	"add_row_serial",
	"add_row_serials",
	"prepare_import_preview",
	"set_row_serials",
)

# Every endpoint that is about one invoice — named by the caller, or by the stored session that is
# being confirmed. These all check that invoice, whether or not one was found: an endpoint that
# only checked the ones it found answered "فاکتور یافت نشد" for a name that does not exist and
# "به این شرکت دسترسی ندارید" for one that does, which is two answers where the design promises
# one, and a caller who can tell them apart has a way to find out which invoice names are real.
INVOICE_ENDPOINTS = (
	"add_row_serial",
	"add_row_serials",
	"get_invoice_serial_status",
	"get_serial_assignments",
	"prepare_import_preview",
	"set_row_serials",
)

# Frappe's own `docstatus`. 2 is cancelled.
DOCSTATUS_CANCELLED = 2

CODE_NOT_AUTHENTICATED = "AUTH_NOT_AUTHENTICATED"
CODE_UNKNOWN_ENDPOINT = "AUTH_UNKNOWN_ENDPOINT"
CODE_NOT_PERMITTED = "AUTH_NOT_PERMITTED"
CODE_COMPANY_NOT_ALLOWED = "AUTH_COMPANY_NOT_ALLOWED"
CODE_STRICT_COMPANY_UNSET = "AUTH_STRICT_COMPANY_UNSET"
CODE_INVOICE_NOT_FOUND = "AUTH_INVOICE_NOT_FOUND"

# The one sentence an unreachable invoice is refused with — whether the name matched nothing or
# matched a real invoice in a company this caller cannot reach. Written here, once, so the two
# answers cannot drift apart: the whole point is that they are the same answer.
INVOICE_REFUSAL = "فاکتور یافت نشد یا به آن دسترسی ندارید."
CODE_SESSION_NOT_OWNED = "AUTH_SESSION_NOT_OWNED"
CODE_INVOICE_CANCELLED = "AUTH_INVOICE_CANCELLED"

REFUSAL_INVOICE_CANCELLED = "این فاکتور لغو شده است و نمی‌توان سریال جدیدی روی آن ثبت کرد."

# One sentence, one place. A site that requires company permissions and has not given this user
# one has the same answer for a document check and for a site-wide operation, and the operator
# should not have to work out that the two are the same fact.
REFUSAL_NO_COMPANY = "این سایت دسترسی شرکت را الزامی کرده و برای کاربر شما شرکتی تعیین نشده است."


class AuthorizationError(PermissionError):
	"""A request that is not allowed. `code` is stable and safe to branch on."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class Principal:
	"""The acting user, as far as authorization is concerned.

	`companies` is the set of companies the user is restricted to, or `None` when the user has
	no Company user permission at all. `strict_companies` mirrors the site's
	`apply_strict_user_permissions`: when it is on, `None` means no company rather than every
	company.
	"""

	user: str
	roles: frozenset = frozenset()
	companies: frozenset | None = None
	strict_companies: bool = False

	def level(self):
		"""The strongest level this principal holds, or `None`."""
		levels = [LEVEL_BY_ROLE[role] for role in self.roles if role in LEVEL_BY_ROLE]

		if not levels:
			return None

		return max(levels, key=lambda level: LEVEL_RANK[level])

	def has_level(self, level):
		"""Whether the principal holds at least `level`."""
		held = self.level()

		return held is not None and LEVEL_RANK[held] >= LEVEL_RANK[level]

	def may_reach_company(self, company):
		"""Whether the principal may touch a company's data."""
		if self.companies is None:
			return not self.strict_companies

		return company in self.companies


@dataclass(frozen=True)
class InvoiceRef:
	"""An invoice as the database returned it.

	The company is here because it was read from the stored document, not because a caller
	supplied it. The lifecycle is here for the same reason: `docstatus` is the site's own answer
	to "may this invoice still be added to", and it is read from the document rather than taken
	from a request or trusted to the window that draws the button.

	It has no default. A default of 0 would mean "draft" for every caller that forgot to read it,
	which is the one value that silently permits a write; a caller that has an invoice to describe
	has to have read it.
	"""

	name: str
	company: str
	docstatus: int


def require_live_invoice(principal, endpoint, invoice):
	"""Refuse an invoice the site has cancelled, for the endpoints that write onto one.

	A registration is a statement about a document: the serial came with this invoice. A cancelled
	invoice is no longer a document that happened — its accounting has been reversed — so adding
	one to it records a claim nothing supports. Refusing it is the server's business and not the
	window's: the window has the same rule, and a rule that lives only there is one an API call
	does not have.
	"""
	if endpoint not in WRITING_ENDPOINTS or invoice is None:
		return True

	if invoice.docstatus == DOCSTATUS_CANCELLED:
		raise AuthorizationError(CODE_INVOICE_CANCELLED, REFUSAL_INVOICE_CANCELLED)

	return True


def require_endpoint(principal, endpoint):
	"""The level an endpoint needs, after checking the principal reaches it."""
	if endpoint not in ENDPOINT_LEVELS:
		raise AuthorizationError(CODE_UNKNOWN_ENDPOINT, f"متد ناشناخته: {endpoint}")

	if not principal or not principal.user:
		raise AuthorizationError(CODE_NOT_AUTHENTICATED, "کاربر وارد نشده است.")

	required = ENDPOINT_LEVELS[endpoint]

	if not principal.has_level(required):
		raise AuthorizationError(
			CODE_NOT_PERMITTED,
			f"برای این عملیات سطح دسترسی «{required}» لازم است.",
		)

	# The level is not always the whole answer: one endpoint asks for a named role as well, and a
	# caller who reaches the level without it is told so in those words.
	named = ENDPOINT_ROLES.get(endpoint)

	if named and named not in principal.roles and ROLE_SYSTEM_MANAGER not in principal.roles:
		raise AuthorizationError(
			CODE_NOT_PERMITTED,
			f"این عملیات به نقش «{named}» نیاز دارد.",
		)

	return required


def require_company(principal, company):
	"""Refuse a company the principal cannot reach."""
	if not isinstance(company, str) or not company.strip():
		raise AuthorizationError(CODE_COMPANY_NOT_ALLOWED, "شرکت تعیین نشده است.")

	if principal.companies is None and principal.strict_companies:
		raise AuthorizationError(CODE_STRICT_COMPANY_UNSET, REFUSAL_NO_COMPANY)

	if not principal.may_reach_company(company):
		raise AuthorizationError(CODE_COMPANY_NOT_ALLOWED, "به این شرکت دسترسی ندارید.")

	return company


def require_any_company(principal):
	"""Refuse a principal that may reach no company at all.

	The endpoints that carry this app's records in and out — the export, the preview and the
	import — are site-wide operations rather than operations on one document, so there is no
	invoice whose company could be checked. A caller the site has restricted to *no* company has
	nothing these endpoints may show it.

	Refused rather than answered with nothing, and the difference matters: an operator on a strict
	site with no company assigned would otherwise receive a *successful* export of zero rows and
	keep the file as the copy of their serial history. A sentence saying the site has no company
	for them is a fact they can act on; an empty file is a trap.

	A principal with no Company user permission is unrestricted (`companies is None`) and passes:
	that is the framework's own meaning for it, and it is the ordinary case on a single-company
	site.
	"""
	if visible_companies(principal) == frozenset():
		raise AuthorizationError(CODE_STRICT_COMPANY_UNSET, REFUSAL_NO_COMPANY)

	return True


def company_sql_condition(principal, escape, column="company"):
	"""A SQL fragment restricting a query to the companies this principal may see.

	The same policy `visible_companies` states, written as a condition for a query the framework
	builds itself. `escape` quotes one value and belongs to the caller, which is what keeps this
	module free of the framework while the policy stays in one place.

	Three answers: `None` for no restriction, `"1 = 0"` for a restriction nothing satisfies — the
	strict site with no company assigned — and an `IN (...)` list otherwise, in a fixed order so
	that the same principal produces the same SQL.
	"""
	companies = visible_companies(principal)

	if companies is None:
		return None

	if not companies:
		return "1 = 0"

	return f"{column} IN ({', '.join(escape(company) for company in sorted(companies))})"


def require_invoice_access(principal, endpoint, invoice):
	"""Refuse an invoice the principal cannot reach, or that does not exist.

	`invoice` is the document as read from the database, or `None` when the name matched
	nothing. A missing invoice and an invoice in another company give the same answer on
	purpose: telling a caller which of the two it was would let them probe for names.
	"""
	require_endpoint(principal, endpoint)

	if invoice is None:
		raise AuthorizationError(CODE_INVOICE_NOT_FOUND, INVOICE_REFUSAL)

	try:
		require_company(principal, invoice.company)
	except AuthorizationError as error:
		# **The same refusal, from the same code and the same sentence.** An invoice that exists
		# in a company this caller cannot reach is answered exactly as a name that matched
		# nothing, because the difference — that the name is real — is the one thing a caller
		# probing for invoice names is trying to learn. The company check is what decides, and it
		# still decides: this raises the refusal rather than returning the invoice.
		raise AuthorizationError(CODE_INVOICE_NOT_FOUND, INVOICE_REFUSAL) from error

	return invoice


def require_transfer_invoices(principal, endpoint, source, target):
	"""Both ends of a transfer, each refused the way every other unreachable invoice is.

	`source` and `target` are the documents as read from the database, or `None` where the name
	matched nothing. Three things are deliberately **not** checked here, and each is checked
	somewhere with a test on it:

	- that the source is cancelled and the target is not — `core.transfer.plan_transfer` refuses
	  either, and the apply re-plans against the database under the lock;
	- that the target amends the source — the same planner, from the stored `amended_from`;
	- that the two are in one company — the same planner again.

	What is left is the question only this module answers: whether *this caller* may see either
	document. That is per-document and per-company, and it is asked of both, because a transfer an
	operator may not see the source of is a transfer that would tell them what another company's
	cancelled invoice once carried.

	A missing invoice and an invoice in another company give the same answer for each end, and the
	two ends give the same answer as each other: a caller cannot learn which of the two names is
	real by comparing the refusals.
	"""
	require_endpoint(principal, endpoint)

	for invoice in (source, target):
		if invoice is None:
			raise AuthorizationError(CODE_INVOICE_NOT_FOUND, INVOICE_REFUSAL)

		try:
			require_company(principal, invoice.company)
		except AuthorizationError as error:
			raise AuthorizationError(CODE_INVOICE_NOT_FOUND, INVOICE_REFUSAL) from error

	return True


def require_session_owner(principal, session_owner):
	"""Refuse confirming a session somebody else prepared."""
	if principal.user != session_owner:
		raise AuthorizationError(CODE_SESSION_NOT_OWNED, "این پیش‌نمایش را کاربر دیگری ساخته است.")

	return True


def authorize(principal, endpoint, *, invoice=None, session_owner=None):
	"""One call that applies every check an endpoint declares.

	Endpoints call this first, before they read or write anything.
	"""
	require_endpoint(principal, endpoint)

	if endpoint in INVOICE_ENDPOINTS:
		require_invoice_access(principal, endpoint, invoice)

	require_live_invoice(principal, endpoint, invoice)

	if endpoint in SESSION_BOUND_ENDPOINTS and session_owner is not None:
		require_session_owner(principal, session_owner)

	return True


def visible_companies(principal):
	"""The companies a query should be restricted to, or `None` for no restriction.

	The report and list queries use this to add the same restriction the framework's own
	permission-aware queries apply.
	"""
	if principal.companies is None:
		return frozenset() if principal.strict_companies else None

	return principal.companies


def may_reach_any(principal, companies):
	"""Whether the principal may reach at least one of `companies`.

	An empty set is reachable by nobody, so a serial with no assignment is denied. An unrestricted
	principal — no Company user permission and not strict — reaches every set, and a restricted one
	reaches a set that overlaps its own companies. The `Logistics Serial` `has_permission` hook turns
	this into a boolean; the hook can only deny, and `True` defers to the framework's role check.
	"""
	if not companies:
		return False

	visible = visible_companies(principal)

	if visible is None:
		return True

	return not visible.isdisjoint(companies)
