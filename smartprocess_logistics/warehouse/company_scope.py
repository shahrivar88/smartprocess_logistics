"""Apply the app's company policy to whitelisted Link queries.

The report loaders already call `report_data.require_company`. Link queries are whitelisted
endpoints too, so leaving their company id to `frappe.get_list` alone would let a strict site
answer with another company's warehouses or stock. This module keeps that decision in the same
`core.authz` rule the rest of the app uses.
"""

import frappe

from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core import authz


def require_company(company):
	"""Return the normalized company, or throw the same app-level refusal as the API."""
	company = str(company or "").strip()
	if not company:
		return ""

	try:
		authz.require_company(FrappeGateway().principal(), company)
	except authz.AuthorizationError as error:
		frappe.throw(error.message, frappe.PermissionError)

	return company
