"""The approved hook composition, independent of the paused committed-stock feature.

`hooks.py` starts from these two functions. The development tree then loads
`hooks_commitment.py`, which appends the paused committed-stock form script and registers the
two Sales Invoice lifecycle handlers and the committed Delivery Note validator. The approved
release subset excludes that module; the functions below stay importable and complete without
it.

Nothing here imports Frappe. The handler names are strings, exactly as `hooks.py` has always
exported them, so the composition can be checked without a site.
"""


def approved_doctype_js():
	"""The Desk form scripts that belong to the approved release."""
	return {
		# Both are inlined into the Sales Invoice form meta, in this order, and Frappe accepts a
		# list here: `desk/form/meta.py::get_code_files_via_hooks` wraps a single path in a list
		# and then reads each one. The development extension appends the committed-stock panel
		# script; the approved release has only these two acts.
		"Sales Invoice": [
			"public/js/sales_invoice_serial_button.js",
			"public/js/sales_invoice_serial_transfer.js",
			"public/js/sales_invoice_warehouse_stock.js",
		],
		# Stock-aware Item suggestions and the read-only billing-progress panel. The panel
		# reads standard Sales Invoice links; it never creates or submits an invoice.
		"Sales Order": "public/js/sales_order_stock_visibility.js",
		# Stock-aware Item and warehouse selectors for a document that only plans: the
		# selector offers virtual warehouses too, and nothing here reserves or moves stock.
		"Quotation": "public/js/quotation_stock_visibility.js",
		# A read-only net/gross/volume panel, and the warehouse selectors. The server computes
		# the panel from the receipt's own quantities and measurement snapshots; the selectors
		# only add a type line under each warehouse name and store the same name. Neither script
		# edits the document.
		"Purchase Receipt": [
			"public/js/purchase_receipt_measurements.js",
			"public/js/purchase_receipt_warehouses.js",
		],
		# The two buttons that export this app's records and import them back. They live on the
		# Settings single because that is where an administrator looks for them, and they are the
		# only supported way to carry the history across a removal. The classification table
		# follows them: it keeps the grid complete in front of the server's own refusal and
		# decides no classification itself. The third one renders the inter-app registry, which is
		# read-only by design: it shows what the app depends on and what it applied to the site.
		"Logistics Settings": [
			"public/js/logistics_settings_transfer.js",
			"public/js/logistics_settings_classification.js",
			"public/js/logistics_settings_dependencies.js",
		],
		# One Link query for one purpose: in a Material Transfer, the Item Code selector offers
		# the items that are actually in the row's source warehouse. It replaces the form's own
		# `get_query` on that field and leaves every other purpose to the standard controller.
		"Stock Entry": "public/js/stock_entry_item_stock_filter.js",
	}


def approved_doc_events():
	"""The document guards and the virtual-warehouse policy of the approved release."""
	return {
		# The serial workflow's update-stock guard, plus the virtual-warehouse refusal. The
		# development extension adds the two commitment lifecycle handlers to this dict.
		"Sales Invoice": {
			"validate": [
				"smartprocess_logistics.integrity.validate_sales_invoice",
				"smartprocess_logistics.warehouse.sales.validate_sales_invoice",
			],
		},
		# The approved managed-row rule (I30) and the virtual-warehouse refusal. The development
		# extension replaces the first entry with the combined committed-stock validator from
		# `commitment_delivery.py`; the release keeps this approved pair.
		"Delivery Note": {
			"validate": [
				"smartprocess_logistics.integrity.validate_delivery_note",
				"smartprocess_logistics.warehouse.sales.validate_delivery_note",
			],
		},
		# Positive measurements are marked as entered on the Item; the visible per-field
		# checks are how an explicit zero is distinguished from a field never filled.
		"Item": {
			"validate": "smartprocess_logistics.measurements.validate_item_measurements",
		},
		# Gross weight and dimensions are frozen on the submitted purchase row; native net
		# weight is already snapshotted by ERPNext. The handler only fills empty rows.
		"Purchase Order": {
			"before_submit": "smartprocess_logistics.measurements.snapshot_purchase_document",
		},
		# A submitted receipt still depends on the invoice it names, so the invoice cannot be
		# cancelled first: cancel the receipt, then the invoice.
		"Purchase Invoice": {
			"validate": "smartprocess_logistics.integrity.validate_purchase_invoice",
			"before_submit": "smartprocess_logistics.measurements.snapshot_purchase_document",
			"before_cancel": "smartprocess_logistics.purchase.invoice.before_cancel",
		},
		# Purchase owns the invoice-first guard and the quantity-based receipt rules. Every
		# stock receipt row must link a submitted Purchase Invoice; the destination may be any
		# permitted virtual or physical warehouse, and the standard Purchase Receipt posts it.
		"Purchase Receipt": {
			"validate": [
				"smartprocess_logistics.integrity.validate_purchase_receipt",
				"smartprocess_logistics.purchase.receipt.validate_purchase_receipt",
			],
			"before_submit": "smartprocess_logistics.measurements.snapshot_purchase_document",
		},
		# A Stock Entry that touches a virtual warehouse must be a Material Transfer. Everything
		# else about the movement is the standard controller's.
		"Stock Entry": {
			"validate": "smartprocess_logistics.warehouse.stock_entry.validate_stock_entry",
		},
		# Virtual stock is not sellable, so it cannot be reserved either.
		"Stock Reservation Entry": {
			"validate": "smartprocess_logistics.warehouse.sales.validate_stock_reservation_entry",
		},
		# The app's own DocType, held to the rules `core/assignment.py` states. Its controller is
		# empty on purpose (`tests/test_doctype_controllers.py`), so a registration is checked
		# here, and here is the only layer a Desk form, `frappe.client` and the REST API all pass
		# through. Removal leaves a trace on the invoice and never touches `Logistics Serial`.
		"Logistics Serial Assignment": {
			"validate": "smartprocess_logistics.serial_link.validate_assignment",
			"on_trash": "smartprocess_logistics.serial_link.audit_deletion",
		},
		# `Logistics Serial` is the identity another document references, so it too is held to the
		# rules `core/serial.py` states: its identity is derived on the server from `serial_no` and
		# immutable once written. Its controller is empty on purpose, so this is the one layer
		# every write path passes through.
		"Logistics Serial": {
			"validate": "smartprocess_logistics.serial_link.validate_serial",
		},
		# `Logistics Serial Transfer` is the evidential document the apply writes. It is held to the
		# record rules `core/transfer.py` states — provenance, children and identity — so a forged or
		# hand-edited record cannot be accepted as what `record_of` wrote. Its controller is empty on
		# purpose, so this is the one layer every write path passes through.
		"Logistics Serial Transfer": {
			"validate": "smartprocess_logistics.serial_link.validate_transfer",
		},
		# The classification table is a mapping, not a setting of its own: it changes what this
		# app lets you do with a warehouse and writes no Warehouse. The save is refused unless the
		# writer may write the single, and the mapping the request cached is dropped afterwards so
		# the next answer comes from what was saved.
		"Logistics Settings": {
			"validate": "smartprocess_logistics.warehouse.classification_settings.validate_logistics_settings",
			"on_update": "smartprocess_logistics.warehouse.classification_settings.forget_classification_mapping",
		},
	}
