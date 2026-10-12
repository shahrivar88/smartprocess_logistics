"""Hook configuration for smartprocess_logistics.

No hook below is registered. The lifecycle functions in `install.py` are written and are
reviewed here, but the lines that would call them stay commented until the step that mounts
and activates the app, so installing this app currently does nothing at all.
"""

app_name = "smartprocess_logistics"
app_title = "SmartProcess Logistics"
app_publisher = "Smart Process"
app_description = "Optional warranty serial numbers for Sales Invoice item rows"
app_email = "info@smartprocess.local"
app_license = "mit"

# Installation is valid only on a site that already runs Frappe and ERPNext.
required_apps = ["frappe", "erpnext"]

# --- Written, not registered yet ------------------------------------------------------
#
# `install.py` implements all three. Each one is idempotent and none of them uses
# `ignore_permissions`. Activating them is the step that installs the app.
#
#     after_install = "smartprocess_logistics.install.after_install"
#     after_migrate = "smartprocess_logistics.install.after_migrate"
#     before_uninstall = "smartprocess_logistics.install.before_uninstall"
#
# The unique constraints are applied by `after_migrate` rather than `after_install`, because
# a DocType schema sync may rebuild a table and take its indexes with it.
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
