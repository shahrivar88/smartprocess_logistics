"""Frappe's own row restriction, registered so that the framework's queries carry this app's
company policy too.

Why a condition is needed at all: `Logistics Serial` has **no company column**, so the
framework's user-permission filtering — which matches `User Permission` values against `Link`
fields of the document — cannot restrict it. A record belongs to a company only through the
assignments that name it, and only a condition like the one below can say so. Without it, every
query Frappe builds itself would list every company's serials to anybody holding the role.

What this file is not:

- **Not the app's own reads.** `frappe.get_all` forces `ignore_permissions=True` and skips
  permission conditions entirely, which is exactly why `api/gateway.py` applies the same policy
  with explicit filters. Two mechanisms, one policy: `core.authz`.
- **Not the check before a write.** `core.authz.require_company` still decides per document,
  against the company read from the live invoice, on every write this app performs.

The contract is read from the source rather than assumed: `frappe/model/db_query.py:1159`
`get_permission_query_conditions` calls each registered method as
`frappe.call(frappe.get_attr(method), self.user, doctype=self.doctype)` and joins the answers
that came back non-empty with `" and "`. So a method takes `user`, accepts `doctype` as a keyword,
and returns SQL — or an empty value meaning "no restriction". Values are quoted with
`frappe.db.escape`, the way `frappe/desk/doctype/event/event.py:286` quotes them.
"""

import frappe

from smartprocess_logistics.api import gateway
from smartprocess_logistics.core import authz


def _table(doctype):
	"""The *table* a doctype's rows live in, which is not the doctype's name.

	`tab` + doctype, Frappe's own convention — and measured on the lab rather than assumed: a
	condition naming `` `Logistics Serial Assignment` `` reached MariaDB as written and failed with
	`Table '...Logistics Serial Assignment' doesn't exist`, because a raw condition gets none of the
	aliases the query builder would have applied.
	"""
	return f"tab{doctype}"


def _condition(user, column):
	"""The caller's company restriction as SQL, or `""` for no restriction.

	The caller is resolved through the same builder the endpoints use, so a Desk list and an
	export cannot disagree about which companies a person reaches.
	"""
	return authz.company_sql_condition(gateway.principal_for(user), frappe.db.escape, column) or ""


def serial_query_conditions(user, doctype=None):
	"""Serials reachable through the caller's companies.

	`Logistics Serial` has no company column, so the restriction can only be a subquery: a serial
	is visible when some assignment of an allowed company names it. The identifiers are the real
	table names, which is what the framework's generated queries use.
	"""
	assignment = _table(gateway.ASSIGNMENT_DOCTYPE)
	condition = _condition(user, f"`{assignment}`.`company`")

	if not condition:
		return ""

	return (
		f"`{_table(gateway.SERIAL_DOCTYPE)}`.`name` in "
		f"(select `logistics_serial` from `{assignment}` where {condition})"
	)


def assignment_query_conditions(user, doctype=None):
	"""Assignments of the caller's companies."""
	return _condition(user, f"`{_table(gateway.ASSIGNMENT_DOCTYPE)}`.`company`")


def serial_has_permission(doc, ptype, user, debug=False):
	"""Allow or deny a per-document action on `Logistics Serial`.

	`create` and anything but read/write/delete defer to the framework's role check. A serial is
	readable or writable only when at least one of its assignments lies in a company this caller
	reaches; a serial with no assignment, or whose assignments all lie elsewhere, is denied. The
	hook can only deny, and `True` — not `None` — is what defers to the role permissions, as
	`frappe.permissions.has_controller_permissions` reads the return.
	"""
	if ptype not in ("read", "write", "delete"):
		return True

	name = doc.get("name") if doc is not None else None

	if not name:
		return False

	companies = frozenset(
		company
		for company in frappe.get_all(
			gateway.ASSIGNMENT_DOCTYPE,
			filters={"logistics_serial": name},
			pluck="company",
			distinct=True,
		)
		if company
	)

	return authz.may_reach_any(gateway.principal_for(user), companies)
