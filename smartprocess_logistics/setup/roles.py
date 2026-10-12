"""The three roles this app grants, and the one function that creates them.

The app creates the roles but assigns them to nobody. An administrator grants them, which
keeps the privilege to widen access with the site's own administrators.

Every permission this app relies on is a DocPerm on the app's own DocTypes. None of these
roles is granted anything on `Sales Invoice`, on `Item` or on any other ERPNext DocType, and
no DocPerm carries `if_owner`, so an Editor may correct a colleague's registration.
"""

import frappe

VIEWER = "Logistics Serial Viewer"
EDITOR = "Logistics Serial Editor"
MANAGER = "Logistics Serial Manager"

ROLES = (VIEWER, EDITOR, MANAGER)


def ensure_roles():
	"""Create the app's roles. Safe to run repeatedly.

	Called from `after_install` and `after_migrate`, both of which run as Administrator, so
	the insert needs no permission bypass and the app calls none.
	"""
	for role in ROLES:
		if frappe.db.exists("Role", role):
			continue

		frappe.get_doc({"doctype": "Role", "desk_access": 1, "role_name": role}).insert()
