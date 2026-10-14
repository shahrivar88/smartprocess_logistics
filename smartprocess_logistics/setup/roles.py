"""The three roles this app grants, and the one function that creates them.

The app creates the roles but assigns them to nobody. An administrator grants them, which
keeps the privilege to widen access with the site's own administrators.

Every permission this app relies on is a DocPerm on the app's own DocTypes. None of these
roles is granted anything on `Sales Invoice`, on `Item` or on any other ERPNext DocType, and
no DocPerm carries `if_owner`, so an Editor may correct a colleague's registration.
"""

import frappe

from smartprocess_logistics.core.authz import (
	ROLE_ARCHIVIST as ARCHIVIST,
)
from smartprocess_logistics.core.authz import (
	ROLE_EDITOR as EDITOR,
)
from smartprocess_logistics.core.authz import (
	ROLE_MANAGER as MANAGER,
)
from smartprocess_logistics.core.authz import (
	ROLE_VIEWER as VIEWER,
)

# The four roles, and the names come from `core.authz` rather than being written here a second
# time: that module is what decides with them, so a second spelling of one would be a role the app
# checks for and never creates. The fourth is the only one that is not a level — it grants nothing
# on any DocType, and the one endpoint that needs it also needs a Manager level
# (`core.authz.ENDPOINT_ROLES` says why).
ROLES = (VIEWER, EDITOR, MANAGER, ARCHIVIST)


def ensure_roles():
	"""Create the app's roles. Safe to run repeatedly.

	Called from `after_install` and `after_migrate`, both of which run as Administrator, so
	the insert needs no permission bypass and the app calls none.
	"""
	for role in ROLES:
		if frappe.db.exists("Role", role):
			continue

		frappe.get_doc({"doctype": "Role", "desk_access": 1, "role_name": role}).insert()
