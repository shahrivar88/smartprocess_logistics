"""Hook configuration for smartprocess_logistics.

Two lifecycle hooks are registered. Both are idempotent and neither calls
`ignore_permissions`. `before_uninstall` is implemented in `install.py` and is deliberately
left unregistered; the note below says what it waits for.
"""

app_name = "smartprocess_logistics"
app_title = "SmartProcess Logistics"
app_publisher = "Smart Process"
app_description = "Optional warranty serial numbers for Sales Invoice item rows"
app_email = "info@smartprocess.local"
app_license = "mit"

# Installation is valid only on a site that already runs Frappe and ERPNext.
required_apps = ["frappe", "erpnext"]

# --- Registered -------------------------------------------------------------------------
#
# Both run the same three idempotent steps: create the three roles, apply the two composite
# unique constraints, and seed `Logistics Settings`.
#
# Both are needed because Frappe v16 calls them at different times and one does not imply the
# other. `installer.install_app` commits the app into `Installed Applications` *before* it runs
# `after_install`, and it never runs `after_migrate`; so a fresh install would otherwise have
# no unique constraints at all, and a failure inside `after_install` would leave a site
# reporting the app as installed with the work half done. `after_migrate` is what repairs that.
after_install = "smartprocess_logistics.install.after_install"
after_migrate = "smartprocess_logistics.install.after_migrate"

# --- Implemented, deliberately not registered -------------------------------------------
#
# `smartprocess_logistics.install.before_uninstall` writes a verified JSON copy of this app's
# records into the site's `private/files`, and stops the uninstall only when that copy cannot
# be written or does not read back as what was prepared. It does not refuse an uninstall merely
# because records exist.
#
# It stays unregistered until that behaviour has been exercised against a real uninstall on a
# disposable site, because it is the last hook between a drop and the data. Register it by
# adding:
#
#     before_uninstall = "smartprocess_logistics.install.before_uninstall"
#
# Phase 4 — Desk user interface:
#     doctype_js = {"Sales Invoice": "public/js/sales_invoice_serial_button.js"}
#     app_include_css = ["/assets/smartprocess_logistics/css/serial_dialog.css"]
#
# Phase 5 — import:
#     scheduler_events = {"daily": ["smartprocess_logistics.imports.cleanup.expire_sessions"]}

# --- Never registered by this app ------------------------------------------------------
#
# doc_events on "Sales Invoice" are deliberately absent. `Document.hook` runs a handler
# inside a try/finally, so an exception raised there aborts the invoice save and takes the
# user's operation down with it. Orphaned assignments, shortages and overages are derived at
# read time instead.
#
# The app also never registers override_doctype_class, override_whitelisted_methods, monkey
# patches, or a fixture that carries a host path, a site name or a port.
