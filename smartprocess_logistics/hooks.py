"""Hook configuration for smartprocess_logistics.

Everything this app registers is here: two lifecycle hooks (`after_install`, `after_migrate`),
the cleanup that runs after a removal, one scheduled job, the Desk assets, the two standard
records it ships as fixtures, the approved form scripts, the two company conditions the
framework's own list queries use, and the document events of the managed workflow. Each is
idempotent and none calls `ignore_permissions`. What is deliberately **absent** —
`override_doctype_class`, `override_whitelisted_methods`, monkey patching — is listed at the
bottom of this file with the reason.

The approved composition lives in `hooks_composition.py`. The development tree additionally
loads `hooks_commitment.py`, which appends the paused committed-stock panel and registers its
two Sales Invoice lifecycle handlers and Delivery Note validator. The approved release subset
excludes that module; this file then imports and registers only the approved hooks.
"""

import importlib.util

from smartprocess_logistics import hooks_composition

app_name = "smartprocess_logistics"
app_title = "SmartProcess Logistics"
app_publisher = "Smart Process"
app_description = "شماره سریال گارانتی اختیاری برای ردیف‌های فاکتور فروش"
app_email = "info@smartprocess.local"
app_license = "mit"

# Frappe v16's apps screen and dock. The custom homepage app on this site still renders the
# legacy icon grid, so `desktop_icon/لجستیک.json` remains as the compatibility entry; this hook
# and `dock/smartprocess_logistics/` are the supported v16 entry and must not be removed.
app_home = "/app/warehouse-and-purchasing"
add_to_apps_screen = [
	{
		"name": app_name,
		"title": app_title,
		"route": app_home,
		"sequence_id": 40,
	}
]

# Installation is valid only on a site that already runs Frappe and ERPNext.
required_apps = ["frappe", "erpnext"]

# --- Desk assets ------------------------------------------------------------------------
#
# Plain asset paths, not a bundle. Frappe serves `/assets/<app>/...` from the app's `public`
# directory, which this deployment bind-mounts read-only, so a `bench build` step could not
# write its output here even if it ran. A plain path needs no build.
#
# The report's own `.js` is loaded by Frappe from its folder and needs no hook.
app_include_css = ["/assets/smartprocess_logistics/css/logistics.css"]

# The approved scripts. The development composition appends the committed-stock panel file from
# `hooks_commitment.py`; the release subset leaves it out.
doctype_js = hooks_composition.approved_doctype_js()

# --- The records this app ships ------------------------------------------------------------
#
# Three standard records, and nothing else: the explicit `Physical` Warehouse Type this app
# classifies by, the Property Setter that makes it the default for a **new** Warehouse, and the
# one that lets `translated_doctype` render its Persian label. `frappe.utils.fixtures.sync_fixtures`
# imports `fixtures/*.json` on every install (`installer.py`) and every migrate (`migrate.py`);
# each record carries a fixed name, so a repeat replaces it instead of adding a second one. No
# existing warehouse or warehouse type is touched: a default is read only when a new document is
# created, and `translated_doctype` changes only how a label is looked up.
fixtures = [
	{"dt": "Warehouse Type", "filters": [["name", "=", "Physical"]]},
	{
		"dt": "Property Setter",
		"filters": [
			["doc_type", "=", "Warehouse"],
			["field_name", "=", "warehouse_type"],
			["property", "=", "default"],
		],
	},
	{
		"dt": "Property Setter",
		"filters": [["doc_type", "=", "Warehouse Type"], ["property", "=", "translated_doctype"]],
	},
]


# --- The framework's own queries ---------------------------------------------------------
#
# `Logistics Serial` has no company column — a serial belongs to a company only through the
# assignments that name it — so Frappe's own user-permission filtering cannot restrict it, and a
# list of every company's serials would otherwise be available to anybody holding the role. These
# conditions are how the queries the framework builds itself (`frappe.client.get_list`, the Desk
# list, a report written with `get_list`) carry the same company policy the endpoints apply:
# `smartprocess_logistics/permissions.py` explains both the rule and its limits.
#
# `frappe.get_all` skips permission conditions entirely, which is why `api/gateway.py` applies the
# policy itself as well; the two paths share one decision (`core.authz`).
permission_query_conditions = {
	"Logistics Serial": "smartprocess_logistics.permissions.serial_query_conditions",
	"Logistics Serial Assignment": ("smartprocess_logistics.permissions.assignment_query_conditions"),
}


# --- Document events --------------------------------------------------------------------
#
# The approved composition is the invoice-first guards and the virtual-warehouse policy. The
# development tree extends it with the paused committed-stock handlers from
# `hooks_commitment.py`; the approved release subset excludes that module and gets this dict
# unchanged. The extension keeps the development order: the committed Delivery Note validator
# runs before the approved virtual-warehouse validator.
doc_events = hooks_composition.approved_doc_events()

if importlib.util.find_spec("smartprocess_logistics.hooks_commitment"):
	importlib.import_module("smartprocess_logistics.hooks_commitment").extend(doctype_js, doc_events)


# --- Registered -------------------------------------------------------------------------
#
# Both run the same five idempotent steps: create the three roles, apply the two composite unique
# constraints, create the archive tables if they are not there, bring
# `Logistics Settings.normalization_version` level with the code, and seed `Logistics Settings`.
#
# Both are needed because Frappe v16 calls them at different times and one does not imply the
# other. `installer.install_app` commits the app into `Installed Applications` *before* it runs
# `after_install`, and it never runs `after_migrate`; so a fresh install would otherwise have
# no unique constraints at all, and a failure inside `after_install` would leave a site
# reporting the app as installed with the work half done. `after_migrate` is what repairs that.
after_install = "smartprocess_logistics.install.after_install"
after_migrate = "smartprocess_logistics.install.after_migrate"

# The app's only scheduled job, and the reason it has one: `Logistics Import Session` is
# transient by design, so rows that were never confirmed, and rows whose import was applied
# months ago, accumulate until something removes them. The sweep expires the first sort and
# removes the second, in batches bounded by `core.session.PURGE_BATCH` and within the retention
# `core.session.RETENTION_DAYS`. It reads and writes one table — this app's own — and an expired
# session is already refused by the claim, so a run that never happens costs correctness nothing.
scheduler_events = {"daily": ["smartprocess_logistics.install.purge_stale_sessions"]}

# There is deliberately **no** `before_uninstall`. This app kept a copy of its own records in
# tables of its own, and a hook wrote and verified that copy before the removal, because
# `remove_app` deletes the table behind every DocType of the app's modules — verified in the
# source: `installer._delete_doctypes` runs one table-dropping statement per DocType. That copy
# is gone, and with it the reason for the hook: what protects the records now is a full site
# backup and an export of this app's own records, both taken on purpose before the removal, both
# documented in `README.md`. A hook that preserved something automatically would suggest the
# records are safe without either, which is the belief this change removes.

# What the framework's own cleanup cannot see. `remove_app` deletes the app's DocTypes, its
# module, its workspace and report, and everything linked to the module; `after_app_uninstall`
# then removes the icons and sidebars whose name matches the app's **title**. This app's icon is
# named in Persian (`سریال لجستیک`) while its title is `SmartProcess Logistics`, so the icon —
# and the three roles, which have no link to a module at all — survive the removal. This hook
# removes the icon and the unused roles, and leaves a role a user still holds alone.
after_uninstall = "smartprocess_logistics.install.after_uninstall"

# --- Implemented, deliberately not registered -------------------------------------------
#
# One thing is deliberately **not implemented**: there is no automatic preservation hook. An
# uninstall is a deliberate act, and the records are protected by an act that is equally
# deliberate — an export and a backup taken first. See `README.md` for the order.
#
# The Desk user interface used to be listed here as not registered, and it is registered: see
# `doctype_js` and `app_include_css` above. The two lines that said otherwise were left behind when
# it was wired up, and a comment that contradicts the file it is in is worse than no comment.

# --- Never registered by this app ------------------------------------------------------
#
# The validation handlers above are guards: they refuse a save and post nothing. The two Sales
# Invoice lifecycle events are registered only by the development extension
# (`hooks_commitment.py`), never by the approved composition; in the development tree they are
# the one place this app writes an ERPNext document, and they do it through the standard
# controller — a Material Transfer Stock Entry — inside the invoice's own request transaction,
# so rollback is the framework's and no handler commits.
# `Document.hook` runs a handler inside a try/finally, so an exception raised there aborts the
# save, which is exactly what a guard, and a failed commitment, must do. Orphaned assignments,
# shortages and overages are still derived at read time, never repaired by a hook.
#
# The app also never registers override_doctype_class, override_whitelisted_methods, monkey
# patches, or a fixture that carries a host path, a site name or a port.
