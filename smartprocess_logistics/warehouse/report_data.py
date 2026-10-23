"""Permission-aware data loading shared by the inventory and procurement reports.

The calculations are in `core`; this module reads only what the current user may read and
turns it into the snapshots those pure functions expect. Every query is bounded by the selected
company and by the framework's own `frappe.get_list` permissions. Item and Bin reads are
permission-aware; child-table reads use `frappe.get_all` only after a permitted parent has been
obtained, so a user cannot pull another company's stock through a report.

No function here writes, reserves or changes a document.
"""

import frappe
from frappe.utils import date_diff, today

from smartprocess_logistics.api.gateway import FrappeGateway
from smartprocess_logistics.core import authz, inventory, measurements, procurement, reporting
from smartprocess_logistics.warehouse import classification, policies

ITEM_READ = "Item"
WAREHOUSE = "Warehouse"
SALES_ORDER = "Sales Order"
PURCHASE_ORDER = "Purchase Order"
SALES_INVOICE = "Sales Invoice"

# A report is a screen, not an export job. Reading one extra row lets the loader refuse a
# result that exceeded the bound instead of silently showing a truncated report.
MAX_REPORT_ROWS = 10000


def _bounded(rows, label):
	rows = list(rows)
	if len(rows) > MAX_REPORT_ROWS:
		frappe.throw(
			f"تعداد ردیف‌های {label} از سقف {MAX_REPORT_ROWS} بیشتر است؛ بازهٔ گزارش را محدود کنید.",
			frappe.ValidationError,
		)
	return rows


def _require_read(doctype, message):
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(message, frappe.PermissionError)


def require_company(filters):
	"""The selected company, checked against the current user's company permissions."""
	company = str((filters or {}).get("company") or "").strip()
	if not company:
		frappe.throw("برای اجرای گزارش، شرکت را انتخاب کنید.", title="گزارش انبار و خرید")

	principal = FrappeGateway().principal()
	visible = authz.visible_companies(principal)
	if visible is not None and company not in visible:
		frappe.throw("این شرکت برای شما قابل دسترسی نیست.", frappe.PermissionError)

	return company


def _date_filters(filters, field):
	from_date = (filters or {}).get("from_date")
	to_date = (filters or {}).get("to_date")
	if from_date and to_date:
		return {field: ("between", [from_date, to_date])}
	if from_date:
		return {field: (">=", from_date)}
	if to_date:
		return {field: ("<=", to_date)}
	return {}


def _warehouses(company, filters=None):
	_require_read(WAREHOUSE, "برای اجرای این گزارش، دسترسی خواندن انبار لازم است.")

	filters = filters or {}
	warehouse_filters = {"company": company, "is_group": 0, "disabled": 0}
	if filters.get("warehouse"):
		warehouse_filters["name"] = filters.get("warehouse")

	rows = frappe.get_list(
		WAREHOUSE,
		filters=warehouse_filters,
		fields=["name", "warehouse_name", "company", "warehouse_type"],
		limit_page_length=MAX_REPORT_ROWS + 1,
	)
	return _bounded(rows, "انبار")


def _item_details(codes):
	if not codes:
		return {}
	_require_read(ITEM_READ, "برای اجرای این گزارش، دسترسی خواندن کالا لازم است.")

	meta = frappe.get_meta(ITEM_READ)
	fields = ["name", "item_name", "item_group", "stock_uom", "weight_per_unit", "weight_uom"]
	for custom in (*measurements.CUSTOM_MEASUREMENT_FIELDS, *measurements.PRESENCE_FIELDS):
		if meta.get_field(custom):
			fields.append(custom)

	rows = _bounded(
		frappe.get_list(
			ITEM_READ,
			filters={"name": ("in", tuple(sorted(codes)))},
			fields=fields,
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"کالا",
	)
	return {row.get("name"): row for row in rows}


def _warehouse_stock(company, warehouses):
	names = [row.get("name") for row in warehouses if row.get("name")]
	if not names:
		return [], [], {}
	_require_read("Bin", "برای اجرای این گزارش، دسترسی خواندن موجودی انبار لازم است.")

	types = {row.get("name"): row.get("warehouse_type") for row in warehouses}
	bins = _bounded(
		frappe.get_list(
			"Bin",
			filters={"company": company, "warehouse": ("in", tuple(names))},
			fields=[
				"item_code",
				"warehouse",
				"company",
				"actual_qty",
				"reserved_qty",
				"stock_uom",
				"stock_value",
				"valuation_rate",
			],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"موجودی انبار",
	)
	for row in bins:
		row["warehouse_type"] = types.get(row.get("warehouse"))

	rows = [
		inventory.WarehouseStock(
			company=company,
			item_code=row.get("item_code"),
			warehouse=row.get("warehouse"),
			warehouse_type=row.get("warehouse_type"),
			actual_qty=row.get("actual_qty"),
			reserved_qty=row.get("reserved_qty"),
		)
		for row in bins
	]
	return (
		bins,
		rows,
		inventory.stock_by_item(
			rows,
			company,
			permitted_warehouses=set(names),
			virtual_types=classification.virtual_types(),
		),
	)


def warehouse_inventory(company, filters):
	"""Rows for the Warehouse Inventory report."""
	warehouses = _warehouses(company, filters)
	bins, _rows, _stock = _warehouse_stock(company, warehouses)
	item_codes = {row.get("item_code") for row in bins if row.get("item_code")}
	items = _item_details(item_codes)
	rows = reporting.warehouse_inventory_rows(
		bins, items, virtual_types=classification.virtual_types()
	)
	item_filter = (filters or {}).get("item")
	group_filter = (filters or {}).get("item_group")
	include_zero = bool((filters or {}).get("include_zero_stock"))

	return [
		row
		for row in rows
		if (include_zero or row["actual_qty"] != 0)
		and (not item_filter or row["item_code"] == item_filter)
		and (not group_filter or row["item_group"] == group_filter)
	]


def _sales_invoice_lines(company, item_names):
	if not frappe.has_permission(SALES_INVOICE, "read"):
		return None

	headers = _bounded(
		frappe.get_list(
			SALES_INVOICE,
			filters={"company": company, "docstatus": 1},
			fields=["name", "is_return", "update_stock"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"فاکتور فروش",
	)
	parents = {row.get("name"): row for row in headers}
	if not parents or not item_names:
		return []

	items = _bounded(
		frappe.get_all(
			"Sales Invoice Item",
			filters={
				"parent": ("in", tuple(parents)),
				"so_detail": ("in", tuple(item_names)),
				"docstatus": 1,
			},
			fields=["parent", "so_detail", "qty", "amount"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"ردیف فاکتور فروش",
	)

	return [
		procurement.InvoiceLine(
			so_detail=row.get("so_detail"),
			qty=row.get("qty"),
			docstatus=1,
			is_return=parents[row.get("parent")].get("is_return"),
			update_stock=parents[row.get("parent")].get("update_stock"),
			amount=row.get("amount"),
			parent=row.get("parent"),
		)
		for row in items
	]


def sales_order_data(company, filters):
	"""Sales Order lines, invoice links and customer metadata for one company."""
	_require_read(SALES_ORDER, "برای اجرای این گزارش، دسترسی خواندن سفارش فروش لازم است.")
	parents = _bounded(
		frappe.get_list(
			SALES_ORDER,
			filters={
				"company": company,
				"docstatus": 1,
				**_date_filters(filters, "transaction_date"),
			},
			fields=["name", "customer", "transaction_date", "delivery_date", "status", "docstatus"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"سفارش فروش",
	)
	parent_map = {row.get("name"): row for row in parents}
	if not parent_map:
		return [], {}, {}, True

	items = _bounded(
		frappe.get_all(
			"Sales Order Item",
			filters={"parent": ("in", tuple(parent_map))},
			fields=[
				"name",
				"parent",
				"item_code",
				"item_name",
				"qty",
				"delivered_qty",
				"returned_qty",
				"conversion_factor",
				"warehouse",
				"idx",
				"delivery_date",
			],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"ردیف سفارش فروش",
	)
	item_names = [row.get("name") for row in items if row.get("name")]
	invoice_lines = _sales_invoice_lines(company, item_names)
	invoices_available = invoice_lines is not None
	invoice_lines = invoice_lines or []
	invoiced = procurement.invoiced_quantities_by_so_detail(invoice_lines)
	reviews = procurement.credit_note_reviews_by_so_detail(invoice_lines)

	lines = []
	metadata = {}
	for row in items:
		parent = parent_map[row.get("parent")]
		item_code = row.get("item_code")
		line = procurement.SalesOrderLine(
			name=row.get("name"),
			sales_order=row.get("parent"),
			company=company,
			item_code=item_code,
			qty=row.get("qty"),
			delivered_qty=row.get("delivered_qty"),
			returned_qty=row.get("returned_qty"),
			invoiced_qty=invoiced.get(row.get("name"), inventory.ZERO),
			conversion_factor=row.get("conversion_factor"),
			transaction_date=parent.get("transaction_date"),
			delivery_date=row.get("delivery_date") or parent.get("delivery_date"),
			status=parent.get("status"),
			docstatus=parent.get("docstatus"),
			idx=row.get("idx"),
		)
		lines.append(line)
		created = parent.get("transaction_date")
		metadata[row.get("name")] = {
			"customer": parent.get("customer"),
			"order_date": created,
			"age_days": date_diff(today(), created) if created else None,
		}

	return lines, metadata, reviews, invoices_available


def _purchase_order_data(company, filters):
	_require_read(PURCHASE_ORDER, "برای اجرای این گزارش، دسترسی خواندن سفارش خرید لازم است.")
	parents = _bounded(
		frappe.get_list(
			PURCHASE_ORDER,
			filters={
				"company": company,
				"docstatus": 1,
				**_date_filters(filters, "transaction_date"),
			},
			fields=["name", "supplier", "transaction_date", "status", "docstatus"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"سفارش خرید",
	)
	parent_map = {row.get("name"): row for row in parents}
	if not parent_map:
		return [], {}, {}

	items = _bounded(
		frappe.get_all(
			"Purchase Order Item",
			filters={"parent": ("in", tuple(parent_map))},
			fields=[
				"name",
				"parent",
				"item_code",
				"qty",
				"received_qty",
				"returned_qty",
				"conversion_factor",
				"idx",
			],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"ردیف سفارش خرید",
	)
	lines = []
	metadata = {}
	for row in items:
		parent = parent_map[row.get("parent")]
		lines.append(
			procurement.PurchaseOrderLine(
				name=row.get("name"),
				company=company,
				item_code=row.get("item_code"),
				qty=row.get("qty"),
				received_qty=row.get("received_qty"),
				returned_qty=row.get("returned_qty"),
				conversion_factor=row.get("conversion_factor"),
				status=parent.get("status"),
				docstatus=parent.get("docstatus"),
			)
		)
		metadata[row.get("name")] = {
			"supplier": parent.get("supplier"),
			"order_date": parent.get("transaction_date"),
		}

	return lines, metadata, procurement.outstanding_purchase_supply_by_item(lines, company)


def inventory_procurement_rows(company, filters, *, strategy=None):
	"""Rows for the combined inventory/procurement dashboard."""
	policy = policies.inventory_policy()
	strategy = strategy or policy.allocation_priority
	warehouses = _warehouses(company, filters)
	_bins, _rows, stock = _warehouse_stock(company, warehouses)
	sales_rows, _metadata, _reviews, _available = sales_order_data(company, filters)
	_po_lines, _po_metadata, po_supply = _purchase_order_data(company, filters)
	rows = reporting.inventory_dashboard_rows(
		company,
		stock,
		sales_rows,
		po_supply,
		strategy=strategy,
		virtual_stock_covers=policy.virtual_stock_covers_procurement,
		net_open_supply=policy.recommended_purchase_net_of_open_po,
	)
	return _filter_items(rows, filters)


def procurement_shortage_rows(company, filters, *, strategy=None):
	"""Rows for the Procurement Shortage report, including source Sales Orders."""
	policy = policies.inventory_policy()
	strategy = strategy or policy.allocation_priority
	warehouses = _warehouses(company, filters)
	_bins, _rows, stock = _warehouse_stock(company, warehouses)
	sales_rows, _metadata, _reviews, _available = sales_order_data(company, filters)
	_po_lines, _po_metadata, po_supply = _purchase_order_data(company, filters)
	related = {}
	for line in sales_rows:
		related.setdefault(line.item_code, set()).add(line.sales_order)

	rows = reporting.procurement_shortage_rows(
		company,
		stock,
		sales_rows,
		po_supply,
		related_orders={key: tuple(value) for key, value in related.items()},
		strategy=strategy,
		virtual_stock_covers=policy.virtual_stock_covers_procurement,
		net_open_supply=policy.recommended_purchase_net_of_open_po,
	)
	return _filter_items(rows, filters)


def ready_invoicing_rows(company, filters, *, strategy=None):
	"""Rows for the Sales Orders Ready for Invoicing report.

	With Sales Invoice read permission, invoiced and remaining quantities are exact. Without
	it, the report returns an operational-only projection and marks the invoice fields as
	unavailable; it never reads Sales Invoices or invents a zero.
	"""
	policy = policies.inventory_policy()
	strategy = strategy or policy.allocation_priority
	lines, metadata, reviews, available = sales_order_data(company, filters)

	warehouses = _warehouses(company, filters)
	_bins, _rows, stock = _warehouse_stock(company, warehouses)
	physical = {code: item.physical for code, item in stock.items()}
	virtual = {code: item.virtual for code, item in stock.items()}
	if available:
		readiness = procurement.ready_for_invoicing(
			lines, physical, virtual, company, strategy=strategy
		)
		rows = reporting.ready_invoicing_rows(readiness, metadata, reviews)
	else:
		readiness = procurement.operational_readiness(
			lines, physical, virtual, company, strategy=strategy
		)
		rows = reporting.operational_ready_invoicing_rows(
			readiness,
			metadata,
			"مقدار فاکتورشده برای نقش شما در دسترس نیست؛ ستون‌های فاکتور خالی‌اند و وضعیت فقط بر اساس موجودی است.",
		)
	return _filter_items(rows, filters)


def _filter_items(rows, filters):
	item = (filters or {}).get("item")
	group = (filters or {}).get("item_group")
	if not item and not group:
		return rows

	items = _item_details({row.get("item_code") for row in rows if row.get("item_code")})
	return [
		row
		for row in rows
		if (not item or row.get("item_code") == item)
		and (not group or (items.get(row.get("item_code")) or {}).get("item_group") == group)
	]


def _purchase_document(source):
	return {
		"Purchase Invoice": ("Purchase Invoice", "Purchase Invoice Item", "posting_date"),
		"Purchase Order": ("Purchase Order", "Purchase Order Item", "transaction_date"),
		"Purchase Receipt": ("Purchase Receipt", "Purchase Receipt Item", "posting_date"),
	}.get(source)


def _measurement_fields(meta):
	return [
		field
		for field in ("sp_gross_weight", "sp_length_cm", "sp_width_cm", "sp_height_cm")
		if meta.get_field(field)
	]


def _measurement(row, source=measurements.HISTORICAL):
	return measurements.Measurement(
		weight_per_unit=row.get("weight_per_unit"),
		weight_uom=row.get("weight_uom") or None,
		gross_weight=row.get("sp_gross_weight"),
		length_cm=row.get("sp_length_cm"),
		width_cm=row.get("sp_width_cm"),
		height_cm=row.get("sp_height_cm"),
		source=source,
	)


def weight_volume_rows(company, filters):
	"""Rows for Item Weight and Volume, for one source at a time."""
	source = str((filters or {}).get("source") or "Purchase Invoice").strip()
	item_filter = (filters or {}).get("item")
	group_filter = (filters or {}).get("item_group")
	_require_read(ITEM_READ, "برای اجرای این گزارش، دسترسی خواندن کالا لازم است.")

	if source == "Warehouse Inventory":
		warehouses = _warehouses(company, filters)
		bins, _rows, _stock = _warehouse_stock(company, warehouses)
		items = _item_details({row.get("item_code") for row in bins if row.get("item_code")})
		result = []
		for row in bins:
			item = items.get(row.get("item_code")) or {}
			if item_filter and row.get("item_code") != item_filter:
				continue
			if group_filter and item.get("item_group") != group_filter:
				continue
			measurement = _measurement(item, source=measurements.CURRENT)
			presence = measurements.snapshot_presence(item)
			gross_available = "sp_gross_weight" in presence
			volume_available = all(
				field in presence for field in ("sp_length_cm", "sp_width_cm", "sp_height_cm")
			)
			result.append(
				{
					"document_type": "Warehouse Inventory",
					"document": row.get("warehouse"),
					"posting_date": None,
					"party": "",
					"cargo_ref": "",
					"item_code": row.get("item_code"),
					"item_name": item.get("item_name") or "",
					**reporting.weight_volume_row(
						row.get("actual_qty"),
						measurement=measurement,
						gross_available=gross_available,
						volume_available=volume_available,
						measurement_source="current_item",
					),
				}
			)
		return result

	document = _purchase_document(source)
	if not document:
		frappe.throw("منبع گزارش وزن و حجم پشتیبانی نمی‌شود.")

	parent_doctype, child_doctype, date_field = document
	_require_read(parent_doctype, "برای اجرای این گزارش، دسترسی خواندن سند خرید لازم است.")
	parent_filters = {"company": company, "docstatus": 1, **_date_filters(filters, date_field)}
	if filters.get("document"):
		parent_filters["name"] = filters.get("document")
	parent_fields = ["name", date_field, "supplier"]
	if frappe.get_meta(parent_doctype).get_field("sp_cargo_ref"):
		parent_fields.append("sp_cargo_ref")
	parents = _bounded(
		frappe.get_list(
			parent_doctype,
			filters=parent_filters,
			fields=parent_fields,
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"سند خرید",
	)
	parent_map = {row.get("name"): row for row in parents}
	if not parent_map:
		return []

	child_meta = frappe.get_meta(child_doctype)
	custom_fields = _measurement_fields(child_meta)
	fields = [
		"parent",
		"item_code",
		"item_name",
		"qty",
		"conversion_factor",
		"total_weight",
		"weight_per_unit",
		"weight_uom",
	]
	fields.extend(custom_fields)
	if child_meta.get_field("sp_measurement_snapshot"):
		fields.append("sp_measurement_snapshot")
	if child_meta.get_field(measurements.PRESENCE_FIELD):
		fields.append(measurements.PRESENCE_FIELD)
	rows = _bounded(
		frappe.get_all(
			child_doctype,
			filters={"parent": ("in", tuple(parent_map))},
			fields=fields,
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"ردیف سند خرید",
	)
	items = _item_details({row.get("item_code") for row in rows if row.get("item_code")})
	result = []
	for row in rows:
		item = items.get(row.get("item_code")) or {}
		if item_filter and row.get("item_code") != item_filter:
			continue
		if group_filter and item.get("item_group") != group_filter:
			continue
		factor = row.get("conversion_factor") or 1
		stock_qty = row.get("qty") or 0
		stock_qty = inventory.as_decimal(stock_qty) * inventory.positive_factor(factor)
		snapshotted = bool(row.get("sp_measurement_snapshot")) and bool(custom_fields)
		measurement = _measurement(row) if snapshotted else None
		presence = (
			measurements.parse_presence(row.get(measurements.PRESENCE_FIELD))
			if snapshotted and child_meta.get_field(measurements.PRESENCE_FIELD)
			else ()
		)
		gross_available = "sp_gross_weight" in presence
		volume_available = all(
			field in presence for field in ("sp_length_cm", "sp_width_cm", "sp_height_cm")
		)
		result.append(
			{
				"document_type": source,
				"document": row.get("parent"),
				"posting_date": parent_map[row.get("parent")].get(date_field),
				"party": parent_map[row.get("parent")].get("supplier") or "",
				"cargo_ref": parent_map[row.get("parent")].get("sp_cargo_ref") or "",
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name") or item.get("item_name") or "",
				**reporting.weight_volume_row(
					stock_qty,
					measurement=measurement,
					native_net_weight=row.get("total_weight")
					if row.get("total_weight") is not None
					else (stock_qty * inventory.as_decimal(row.get("weight_per_unit") or 0)),
					native_weight_uom=row.get("weight_uom"),
					gross_available=gross_available,
					volume_available=volume_available,
					measurement_source="historical_snapshot"
					if snapshotted
					else (
						"native_net_snapshot" if row.get("total_weight") is not None else "native_item_weight"
					),
				),
			}
		)
	return result
