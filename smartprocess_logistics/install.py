"""Installation, migration and uninstall entry points for smartprocess_logistics.

`hooks.py` does not reference this module yet, so nothing here runs. The functions are
complete and are activated together with the app mount, so this phase's review covers them
before anything executes.

Every function is safe to run repeatedly: each one checks for what already exists before it
writes. None of them touches a document this app does not own, and none of them uses
`ignore_permissions`.
"""

import frappe
from frappe import _

from smartprocess_logistics.setup import constraints, roles, seeds

# Records this app owns that a reader would miss if they disappeared. `Logistics Import
# Session` is deliberately absent: a preview session is transient by design and holds no
# history worth preserving.
PROTECTED_DOCTYPES = ("Logistics Serial", "Logistics Serial Assignment")


def after_install():
	"""Create the roles and seed the settings.

	`seeds.seed_settings` also records the installation timestamp, and that timestamp is the
	eligibility cut-off of the Missing Sales Serials report. It is written here and nowhere
	else, and the `set_only_once` flag on the field makes the framework refuse any later
	change to it.
	"""
	roles.ensure_roles()
	seeds.seed_settings()


def after_migrate():
	"""Re-assert the roles, the seeded settings and the unique constraints.

	The constraints are declared here rather than at install because a DocType schema sync
	may rebuild a table and take its indexes with it. `frappe.db.add_unique` checks
	`information_schema` first, so re-running this is a no-op.
	"""
	roles.ensure_roles()
	seeds.seed_settings()
	constraints.ensure_unique_constraints()


def before_uninstall():
	"""Refuse the uninstall while this app still owns records.

	`frappe.installer.remove_app` runs `before_uninstall` hooks before it drops anything, so
	raising from here stops the uninstall with the data intact.

	There is deliberately no override flag. A fresh export is not treated as authorization to
	destroy records, because an export nobody has verified is not a recovery plan. The
	administrator exports the two DocTypes, deletes the records deliberately, and then
	uninstalls.
	"""
	populated = []

	for doctype in PROTECTED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue

		count = frappe.db.count(doctype)
		if count:
			populated.append(f"{doctype}: {count}")

	if not populated:
		return

	frappe.throw(
		_(
			"This app still owns records and cannot be uninstalled ({0}). Export the data from"
			" both DocTypes, delete the records deliberately, and then uninstall."
		).format(", ".join(populated)),
		title=_("Serial data must not be destroyed silently"),
	)
