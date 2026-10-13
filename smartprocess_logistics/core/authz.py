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
	"set_row_serials": EDITOR,
	"delete_assignments": MANAGER,
	"prepare_import_preview": EDITOR,
	"confirm_import": EDITOR,
	# Exporting and importing this app's records. An export is a copy of every serial and every
	# assignment in the site, and an import can create them; both are administrative, so both
	# need a Manager and neither is offered to a Viewer or an Editor.
	"export_serials": MANAGER,
	"preview_serial_restore": MANAGER,
	"apply_serial_restore": MANAGER,
}

# Endpoints that a session may only be confirmed by the user who prepared it.
SESSION_BOUND_ENDPOINTS = ("confirm_import",)

CODE_NOT_AUTHENTICATED = "AUTH_NOT_AUTHENTICATED"
CODE_UNKNOWN_ENDPOINT = "AUTH_UNKNOWN_ENDPOINT"
CODE_NOT_PERMITTED = "AUTH_NOT_PERMITTED"
CODE_COMPANY_NOT_ALLOWED = "AUTH_COMPANY_NOT_ALLOWED"
CODE_STRICT_COMPANY_UNSET = "AUTH_STRICT_COMPANY_UNSET"
CODE_INVOICE_NOT_FOUND = "AUTH_INVOICE_NOT_FOUND"
CODE_SESSION_NOT_OWNED = "AUTH_SESSION_NOT_OWNED"


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
	supplied it.
	"""

	name: str
	company: str


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

	return required


def require_company(principal, company):
	"""Refuse a company the principal cannot reach."""
	if not isinstance(company, str) or not company.strip():
		raise AuthorizationError(CODE_COMPANY_NOT_ALLOWED, "شرکت تعیین نشده است.")

	if principal.companies is None and principal.strict_companies:
		raise AuthorizationError(
			CODE_STRICT_COMPANY_UNSET,
			"این سایت دسترسی شرکت را الزامی کرده و برای کاربر شما شرکتی تعیین نشده است.",
		)

	if not principal.may_reach_company(company):
		raise AuthorizationError(CODE_COMPANY_NOT_ALLOWED, "به این شرکت دسترسی ندارید.")

	return company


def require_invoice_access(principal, endpoint, invoice):
	"""Refuse an invoice the principal cannot reach, or that does not exist.

	`invoice` is the document as read from the database, or `None` when the name matched
	nothing. A missing invoice and an invoice in another company give the same answer on
	purpose: telling a caller which of the two it was would let them probe for names.
	"""
	require_endpoint(principal, endpoint)

	if invoice is None:
		raise AuthorizationError(CODE_INVOICE_NOT_FOUND, "فاکتور یافت نشد یا به آن دسترسی ندارید.")

	require_company(principal, invoice.company)

	return invoice


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

	if invoice is not None or endpoint in ("get_invoice_serial_status", "get_serial_assignments"):
		require_invoice_access(principal, endpoint, invoice)

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
