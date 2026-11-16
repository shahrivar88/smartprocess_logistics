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
from smartprocess_logistics.core import (
	authz,
	fulfilment,
	inventory,
	measurement_totals,
	measurements,
	procurement,
	receipts,
	reporting,
)
from smartprocess_logistics.warehouse import classification, measurement_summary, policies, policy

ITEM_READ = "Item"
WAREHOUSE = "Warehouse"
SALES_ORDER = "Sales Order"
QUOTATION = "Quotation"
PURCHASE_ORDER = "Purchase Order"
SALES_INVOICE = "Sales Invoice"
PURCHASE_INVOICE = "Purchase Invoice"
PURCHASE_INVOICE_ITEM = "Purchase Invoice Item"
PURCHASE_RECEIPT = "Purchase Receipt"
PURCHASE_RECEIPT_ITEM = "Purchase Receipt Item"

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


def _classification_filter(filters):
	"""The «نوع انبار» filter, refused by name when the value is not one the form offers."""
	selected = str((filters or {}).get("classification") or policy.FILTER_ALL).strip()

	if selected not in policy.TYPE_FILTERS:
		frappe.throw(
			"نوع انبار انتخابی شناخته نشده است؛ همه، فیزیکی، مجازی، در راه یا تعیین‌نشده.",
			title="گزارش موجودی انبار",
		)

	return selected


def warehouse_inventory(company, filters):
	"""Rows for the Warehouse Inventory report."""
	selected = _classification_filter(filters)
	warehouses = [
		row
		for row in _warehouses(company, filters)
		if policy.type_filter_matches(
			row.get("warehouse_type"),
			selected,
			virtual_types=classification.virtual_types(),
			mapping=classification.stored_mapping(),
		)
	]
	bins, _rows, _stock = _warehouse_stock(company, warehouses)
	item_codes = {row.get("item_code") for row in bins if row.get("item_code")}
	items = _item_details(item_codes)
	rows = reporting.warehouse_inventory_rows(
		bins,
		items,
		virtual_types=classification.virtual_types(),
		mapping=classification.stored_mapping(),
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
	bins, _rows, stock = _warehouse_stock(company, warehouses)
	operational = fulfilment.stock_by_item(bins, classification.virtual_types())
	transit_by_item = {code: value.transit for code, value in operational.items()}
	sales_rows, _metadata, _reviews, _available = sales_order_data(company, filters)
	_po_lines, _po_metadata, po_supply = _purchase_order_data(company, filters)
	rows = reporting.inventory_dashboard_rows(
		company,
		stock,
		sales_rows,
		po_supply,
		transit_by_item=transit_by_item,
		strategy=strategy,
		net_open_supply=policy.recommended_purchase_net_of_open_po,
	)
	return _filter_items(rows, filters)


def procurement_shortage_rows(company, filters, *, strategy=None):
	"""Rows for the Procurement Shortage report, including source Sales Orders."""
	policy = policies.inventory_policy()
	strategy = strategy or policy.allocation_priority
	warehouses = _warehouses(company, filters)
	bins, _rows, stock = _warehouse_stock(company, warehouses)
	operational = fulfilment.stock_by_item(bins, classification.virtual_types())
	transit_by_item = {code: value.transit for code, value in operational.items()}
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
		transit_by_item=transit_by_item,
		related_orders={key: tuple(value) for key, value in related.items()},
		strategy=strategy,
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


def quotation_data(company, filters):
	"""Submitted Quotation Item rows of one company, with the quantity already ordered from each.

	A conversion to a Sales Order writes the converted quantity into `ordered_qty` (ERPNext's own
	status updater, in Stock UOM, reversed when the Sales Order is cancelled). That figure is what
	keeps the converted part out of this report's demand.
	"""
	_require_read(QUOTATION, "برای اجرای این گزارش، دسترسی خواندن پیش‌فاکتور لازم است.")

	parents = _bounded(
		frappe.get_list(
			QUOTATION,
			filters={
				"company": company,
				"docstatus": 1,
				**_date_filters(filters, "transaction_date"),
			},
			fields=["name", "status", "valid_till", "transaction_date"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"پیش‌فاکتور",
	)
	parent_map = {row.get("name"): row for row in parents}
	if not parent_map:
		return []

	items = _bounded(
		frappe.get_all(
			"Quotation Item",
			filters={"parent": ("in", tuple(parent_map))},
			fields=[
				"name",
				"parent",
				"item_code",
				"qty",
				"stock_qty",
				"ordered_qty",
				"conversion_factor",
			],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"ردیف پیش‌فاکتور",
	)

	lines = []
	for row in items:
		parent = parent_map.get(row.get("parent")) or {}
		lines.append(
			fulfilment.QuotationLine(
				name=row.get("name") or "",
				quotation=row.get("parent") or "",
				company=company,
				item_code=row.get("item_code") or "",
				qty=row.get("qty"),
				stock_qty=row.get("stock_qty"),
				ordered_qty=row.get("ordered_qty"),
				conversion_factor=row.get("conversion_factor") or 1,
				status=parent.get("status") or "",
				docstatus=1,
				valid_till=parent.get("valid_till"),
			)
		)

	return lines


def operational_fulfilment_rows(company, filters):
	"""Rows for «تراز موجودی».

	The warehouse filter narrows the **stock** side, like the other inventory reports; demand is
	company-wide because a Sales Order line is not tied to the warehouse being inspected. Virtual
	warehouses never reach this function's totals. Open Purchase Orders are read only for the
	«سفارش خرید باز» and «پیشنهاد خرید» columns: they never change the shortage.
	"""
	policy = policies.inventory_policy()
	warehouses = _warehouses(company, filters)
	bins, _rows, _stock = _warehouse_stock(company, warehouses)
	stock = fulfilment.stock_by_item(bins, classification.virtual_types())

	quote_lines = quotation_data(company, filters)
	sales_lines, _metadata, _reviews, _available = sales_order_data(company, filters)
	sales_order_demand = procurement.open_delivery_demand_by_item(sales_lines, company)
	sales_orders_by_item = {}
	for line in sales_lines:
		if procurement.open_delivery_demand(line) > inventory.ZERO:
			sales_orders_by_item.setdefault(line.item_code, set()).add(line.sales_order)

	_purchase_lines, _purchase_metadata, purchase_supply = _purchase_order_data(company, filters)

	rows = fulfilment.fulfilment_rows(
		company,
		stock,
		quote_lines,
		sales_order_demand,
		sales_orders_by_item=sales_orders_by_item,
		purchase_supply_by_item=purchase_supply,
		today=today(),
		only_shortage=bool((filters or {}).get("only_shortage")),
		hide_zero=bool((filters or {}).get("hide_zero")),
		net_open_supply=policy.recommended_purchase_net_of_open_po,
	)

	details = _item_details({row.item_code for row in rows})
	result = []
	for row in rows:
		detail = details.get(row.item_code) or {}
		result.append(
			{
				"company": row.company,
				"item_code": row.item_code,
				"item_name": detail.get("item_name") or "",
				"item_group": detail.get("item_group") or "",
				"stock_uom": detail.get("stock_uom") or "",
				"physical_stock": row.physical,
				"transit_stock": row.transit,
				"quotation_demand": row.quotation_demand,
				"sales_order_demand": row.sales_order_demand,
				"total_demand": row.total_demand,
				"shortage": row.shortage,
				"open_purchase_supply": row.open_purchase_supply,
				"suggested_purchase": row.suggested_purchase,
				"related_quotations": ", ".join(row.related_quotations),
				"related_sales_orders": ", ".join(row.related_sales_orders),
			}
		)

	return _filter_items(result, filters)


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


def _summary_row(
	totals,
	*,
	document_type,
	document,
	posting_date,
	party,
	cargo_ref,
	item_label,
	extra_warnings=(),
):
	issues = measurement_summary.warnings(totals, extra_warnings)
	net_source = (
		"document_total"
		if totals.net_by_uom and not totals.net_missing and not totals.unresolved_uoms
		else "document_partial"
	)
	gross_source = (
		"document_total"
		if totals.gross_by_uom and not totals.gross_missing and not totals.unresolved_uoms
		else "document_partial"
	)
	volume_source = (
		"document_total"
		if totals.volume_cm3 is not None and not totals.volume_missing
		else "document_partial"
	)
	source = "document_total" if totals.complete else "document_partial"

	return {
		"row_type": "جمع سند",
		"document_type": document_type,
		"document": document,
		"posting_date": posting_date,
		"party": party or "",
		"cargo_ref": cargo_ref or "",
		"item_code": "",
		"item_name": item_label,
		"quantity": None,
		"net_weight": totals.net_total,
		"gross_weight": totals.gross_total,
		"volume_cm3": totals.volume_cm3,
		"volume_m3": totals.volume_m3,
		"weight_uom": totals.weight_uom or "",
		"net_source": net_source,
		"gross_source": gross_source,
		"volume_source": volume_source,
		"measurement_source": source,
		"rejected_qty": None,
		"rejected_net_weight": totals.rejected_net_total,
		"rejected_gross_weight": totals.rejected_gross_total,
		"rejected_volume_cm3": totals.rejected_volume_cm3,
		"rejected_volume_m3": totals.rejected_volume_m3,
		"rows_count": totals.rows,
		"coverage": measurement_summary.coverage_text(totals),
		"warning": "؛ ".join(issues),
	}


def _with_document_summaries(
	details, lines_by_document, metadata, *, target_uom, extra_warnings=()
):
	converter = measurement_summary.make_converter()
	result = []
	by_document = {}
	for detail in details:
		by_document.setdefault(detail["document"], []).append(detail)

	for document in sorted(by_document):
		totals = measurement_totals.document_totals(
			document,
			lines_by_document.get(document, ()),
			target_uom=target_uom,
			convert=converter,
		)
		info = metadata.get(document, {})
		result.append(
			_summary_row(
				totals,
				document_type=info.get("document_type", ""),
				document=document,
				posting_date=info.get("posting_date"),
				party=info.get("party"),
				cargo_ref=info.get("cargo_ref"),
				item_label=info.get("item_label", "جمع سند"),
				extra_warnings=extra_warnings,
			)
		)
		result.extend(
			sorted(
				by_document[document],
				key=lambda row: (row.get("_idx") or 0, row.get("item_code") or ""),
			)
		)

	return result


def weight_volume_rows(company, filters):
	"""Rows for Item Weight and Volume, with one summary row per document.

	The summary is computed over every row of the document; item and item-group filters narrow
	only the detail rows, and the summary says so when such a filter is active.
	"""
	source = str((filters or {}).get("source") or "Purchase Invoice").strip()
	item_filter = (filters or {}).get("item")
	group_filter = (filters or {}).get("item_group")
	target_uom = (filters or {}).get("weight_uom")
	filter_note = (
		("جمع سند کامل است و فیلتر کالا/گروه را شامل نمی‌شود.",)
		if item_filter or group_filter
		else ()
	)
	_require_read(ITEM_READ, "برای اجرای این گزارش، دسترسی خواندن کالا لازم است.")

	if source == "Warehouse Inventory":
		warehouses = _warehouses(company, filters)
		bins, _rows, _stock = _warehouse_stock(company, warehouses)
		items = _item_details({row.get("item_code") for row in bins if row.get("item_code")})
		details = []
		lines_by_document = {}
		metadata = {}
		for row in bins:
			item = items.get(row.get("item_code")) or {}
			measurement = _measurement(item, source=measurements.CURRENT)
			presence = measurements.snapshot_presence(item)
			gross_available = "sp_gross_weight" in presence
			volume_available = all(
				field in presence for field in ("sp_length_cm", "sp_width_cm", "sp_height_cm")
			)
			document = row.get("warehouse")
			line = measurement_totals.MeasurementLine(
				document=document,
				item_code=row.get("item_code") or "",
				stock_qty=row.get("actual_qty"),
				measurement=measurement,
				net_present=measurement_totals.weight_is_present(measurement),
				gross_present=gross_available,
				volume_present=volume_available,
			)
			lines_by_document.setdefault(document, []).append(line)
			metadata[document] = {
				"document_type": "Warehouse Inventory",
				"posting_date": None,
				"party": "",
				"cargo_ref": "",
				"item_label": "جمع انبار",
			}
			if item_filter and row.get("item_code") != item_filter:
				continue
			if group_filter and item.get("item_group") != group_filter:
				continue
			details.append(
				{
					"row_type": "ردیف کالا",
					"document_type": "Warehouse Inventory",
					"document": document,
					"posting_date": None,
					"party": "",
					"cargo_ref": "",
					"item_code": row.get("item_code"),
					"item_name": item.get("item_name") or "",
					"rejected_qty": None,
					"rejected_net_weight": None,
					"rejected_gross_weight": None,
					"rejected_volume_cm3": None,
					"rejected_volume_m3": None,
					"rows_count": None,
					"coverage": "",
					"warning": "",
					"_idx": 0,
					**reporting.weight_volume_row(
						row.get("actual_qty"),
						measurement=measurement,
						net_available=line.net_present,
						gross_available=gross_available,
						volume_available=volume_available,
						measurement_source="current_item",
					),
				}
			)
		return _with_document_summaries(
			details,
			lines_by_document,
			metadata,
			target_uom=target_uom,
			extra_warnings=filter_note,
		)

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
		"idx",
		"item_code",
		"item_name",
		"qty",
		"conversion_factor",
		"total_weight",
		"weight_per_unit",
		"weight_uom",
	]
	if child_meta.get_field("rejected_qty"):
		fields.append("rejected_qty")
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
	details = []
	lines_by_document = {}
	metadata = {}
	for row in rows:
		item = items.get(row.get("item_code")) or {}
		factor = inventory.positive_factor(row.get("conversion_factor") or 1)
		stock_qty = inventory.as_decimal(row.get("qty") or 0) * factor
		has_rejected = source == "Purchase Receipt" and bool(child_meta.get_field("rejected_qty"))
		rejected_qty = (
			inventory.as_decimal(row.get("rejected_qty") or 0) * factor if has_rejected else inventory.ZERO
		)
		snapshotted = bool(row.get("sp_measurement_snapshot")) and bool(custom_fields)
		measurement = _measurement(row)
		presence = (
			measurements.parse_presence(row.get(measurements.PRESENCE_FIELD))
			if snapshotted and child_meta.get_field(measurements.PRESENCE_FIELD)
			else ()
		)
		net_present = measurement_totals.weight_is_present(measurement)
		gross_available = "sp_gross_weight" in presence
		volume_available = all(
			field in presence for field in ("sp_length_cm", "sp_width_cm", "sp_height_cm")
		)
		parent = row.get("parent")
		lines_by_document.setdefault(parent, []).append(
			measurement_totals.MeasurementLine(
				document=parent,
				item_code=row.get("item_code") or "",
				stock_qty=stock_qty,
				rejected_stock_qty=rejected_qty,
				measurement=measurement,
				net_present=net_present,
				gross_present=gross_available,
				volume_present=volume_available,
				idx=row.get("idx") or 0,
			)
		)
		metadata[parent] = {
			"document_type": source,
			"posting_date": parent_map[parent].get(date_field),
			"party": parent_map[parent].get("supplier") or "",
			"cargo_ref": parent_map[parent].get("sp_cargo_ref") or "",
			"item_label": "جمع سند",
		}
		if item_filter and row.get("item_code") != item_filter:
			continue
		if group_filter and item.get("item_group") != group_filter:
			continue
		rejected_values = (
			reporting.weight_volume_row(
				rejected_qty,
				measurement=measurement,
				net_available=net_present,
				gross_available=gross_available,
				volume_available=volume_available,
			)
			if has_rejected
			else None
		)
		details.append(
			{
				"row_type": "ردیف کالا",
				"document_type": source,
				"document": parent,
				"posting_date": parent_map[parent].get(date_field),
				"party": parent_map[parent].get("supplier") or "",
				"cargo_ref": parent_map[parent].get("sp_cargo_ref") or "",
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name") or item.get("item_name") or "",
				"rejected_qty": rejected_qty if has_rejected else None,
				"rejected_net_weight": rejected_values["net_weight"] if rejected_values else None,
				"rejected_gross_weight": rejected_values["gross_weight"] if rejected_values else None,
				"rejected_volume_cm3": rejected_values["volume_cm3"] if rejected_values else None,
				"rejected_volume_m3": rejected_values["volume_m3"] if rejected_values else None,
				"rows_count": None,
				"coverage": "",
				"warning": "",
				"_idx": row.get("idx") or 0,
				**reporting.weight_volume_row(
					stock_qty,
					measurement=measurement,
					native_net_weight=stock_qty * inventory.as_decimal(row.get("weight_per_unit") or 0)
					if net_present
					else None,
					native_weight_uom=row.get("weight_uom"),
					net_available=net_present,
					gross_available=gross_available,
					volume_available=volume_available,
					measurement_source="historical_snapshot"
					if snapshotted
					else (
						"native_net_snapshot" if net_present else "unavailable"
					),
				),
			}
		)
	return _with_document_summaries(
		details,
		lines_by_document,
		metadata,
		target_uom=target_uom,
		extra_warnings=filter_note,
	)


def _receipt_statuses(filters):
	"""The statuses the receipt-status filter keeps. An unset filter means outstanding."""
	value = str((filters or {}).get("receipt_status") or receipts.FILTER_OUTSTANDING).strip()
	statuses = receipts.STATUS_FILTERS.get(value)
	if statuses is None:
		frappe.throw(
			"وضعیت دریافت انتخاب‌شده شناخته نشده است.",
			frappe.ValidationError,
			title="گزارش کالاهای خریداری‌شده و دریافت‌نشده",
		)

	return statuses


def _purchase_invoice_filters(company, filters):
	"""Submitted invoices of the selected company inside the selected window."""
	condition = {"company": company, "docstatus": 1, **_date_filters(filters, "posting_date")}
	if filters.get("supplier"):
		condition["supplier"] = filters.get("supplier")
	if filters.get("purchase_invoice"):
		condition["name"] = filters.get("purchase_invoice")

	return condition


def _receipt_warehouses(item, linked):
	"""Where this line went: the invoice row's warehouse and every submitted receipt's.

	An invoice that set Update Stock names its warehouse on its own row; one that did not
	usually names none, and the warehouse exists only on the receipt rows.
	"""
	names = []
	candidates = [item.get("warehouse")]
	candidates.extend(row.get("warehouse") for row in linked if row.get("docstatus") == 1)
	for name in candidates:
		if name and name not in names:
			names.append(name)

	return names


def _warehouse_types(company, names):
	"""`Warehouse Type` per named warehouse, for the three-way classification.

	A warehouse missing from the result is one this user may not read, one of another company,
	or one that no longer exists; the caller reads that as "cannot be classified", never as
	physical.
	"""
	if not names:
		return {}
	_require_read(WAREHOUSE, "برای اجرای این گزارش، دسترسی خواندن انبار لازم است.")

	rows = _bounded(
		frappe.get_list(
			WAREHOUSE,
			filters={"company": company, "name": ("in", tuple(sorted(names)))},
			fields=["name", "warehouse_type"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"انبار",
	)
	return {row.get("name"): row.get("warehouse_type") for row in rows}


def _classification_of(warehouse, types):
	if not warehouse or warehouse not in types:
		return None

	return classification.classification_of_type(types[warehouse])


def _permitted_receipts(company, names):
	"""Receipt headers this user may read in this company, cancelled and draft ones included.

	They are kept so the report can say a cancelled or a draft receipt exists instead of
	silently leaving it out of the sums.
	"""
	if not names:
		return {}

	rows = _bounded(
		frappe.get_list(
			PURCHASE_RECEIPT,
			filters={"company": company, "name": ("in", tuple(sorted(names)))},
			fields=["name", "is_return"],
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"رسید خرید",
	)
	return {row.get("name"): row for row in rows}


def _receipt_rows(company, items):
	"""Every Purchase Receipt Item linked to these invoice items, keyed by the invoice row."""
	invoice_items = tuple(sorted({row.get("name") for row in items if row.get("name")}))
	receipt_items = tuple(sorted({row.get("pr_detail") for row in items if row.get("pr_detail")}))
	if not invoice_items and not receipt_items:
		return {}

	_require_read(PURCHASE_RECEIPT, "برای اجرای این گزارش، دسترسی خواندن رسید خرید لازم است.")

	# Both directions are read because ERPNext fills them at different moments: an invoice that
	# came first carries `pr_detail` once a receipt is made from it, and a receipt that came
	# first carries `purchase_invoice_item` once the invoice is made from it.
	conditions = []
	if invoice_items:
		conditions.append({"purchase_invoice_item": ("in", invoice_items)})
	if receipt_items:
		conditions.append({"name": ("in", receipt_items)})

	found = {}
	for condition in conditions:
		rows = _bounded(
			frappe.get_all(
				PURCHASE_RECEIPT_ITEM,
				filters=condition,
				fields=[
					"name",
					"parent",
					"purchase_invoice_item",
					"qty",
					"rejected_qty",
					"received_qty",
					"uom",
					"conversion_factor",
					"warehouse",
					"docstatus",
				],
				limit_page_length=MAX_REPORT_ROWS + 1,
			),
			"ردیف رسید خرید",
		)
		for row in rows:
			found.setdefault(row.get("name"), row)

	parents = _permitted_receipts(company, {row.get("parent") for row in found.values() if row.get("parent")})
	from_detail = {row.get("pr_detail"): row.get("name") for row in items if row.get("pr_detail")}

	by_invoice_item = {}
	for row in found.values():
		parent = parents.get(row.get("parent"))
		if parent is None:
			continue

		row["is_return"] = bool(parent.get("is_return"))
		key = row.get("purchase_invoice_item") or from_detail.get(row.get("name"))
		by_invoice_item.setdefault(key, []).append(row)

	return by_invoice_item


def _receipt_report_row(company, item, parent, linked, types, statuses):
	"""One report row for one invoice item, or None when the receipt filter excludes it."""
	update_stock = bool(parent.get("update_stock"))
	receipt_rows = []
	cancelled = 0
	draft = 0

	for row in linked:
		if row.get("docstatus") == 2:
			cancelled += 1
			continue
		if row.get("docstatus") == 0:
			draft += 1
			continue

		receipt_rows.append(
			receipts.ReceiptRow(
				qty=row.get("qty"),
				rejected_qty=row.get("rejected_qty"),
				received_qty=row.get("received_qty"),
				uom=row.get("uom"),
				conversion_factor=row.get("conversion_factor"),
				classification=_classification_of(row.get("warehouse"), types),
				is_return=bool(row.get("is_return")),
			)
		)

	outcome = receipts.evaluate(
		receipts.InvoiceRow(
			purchased_qty=item.get("qty"),
			received_qty=item.get("received_qty"),
			uom=item.get("uom"),
			conversion_factor=item.get("conversion_factor"),
			is_return=bool(parent.get("is_return")),
			update_stock=update_stock,
			warehouse_classification=_classification_of(item.get("warehouse"), types)
			if update_stock
			else None,
			receipts=tuple(receipt_rows),
			cancelled_receipts=cancelled,
			draft_receipts=draft,
		)
	)
	if outcome.status not in statuses:
		return None

	return {
		"company": company,
		"purchase_invoice": parent.get("name"),
		"posting_date": parent.get("posting_date"),
		"supplier": parent.get("supplier"),
		"item_code": item.get("item_code"),
		"item_name": item.get("item_name") or "",
		"purchased_qty": outcome.purchased,
		"received_qty": outcome.received,
		"outstanding_qty": outcome.outstanding,
		"uom": item.get("uom") or "",
		"warehouse": "، ".join(_receipt_warehouses(item, linked)),
		"currency": parent.get("currency") or "",
		"receipt_status": outcome.status,
		"physical_qty": outcome.physical,
		"note_codes": list(outcome.notes),
	}


def purchased_not_received(company, filters):
	"""Rows for the Purchased Not Received report: one per submitted Purchase Invoice Item.

	The received quantity is ERPNext's own `Purchase Invoice Item.received_qty`, so the report
	agrees with the document's status; the outstanding quantity is the difference. Nothing is
	derived from `Bin`: which invoice row a unit settled is a document fact, not a balance.
	"""
	filters = filters or {}
	statuses = _receipt_statuses(filters)
	_require_read(PURCHASE_INVOICE, "برای اجرای این گزارش، دسترسی خواندن فاکتور خرید لازم است.")

	headers = _bounded(
		frappe.get_list(
			PURCHASE_INVOICE,
			filters=_purchase_invoice_filters(company, filters),
			fields=["name", "posting_date", "supplier", "currency", "is_return", "update_stock"],
			order_by="posting_date asc, name asc",
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"فاکتور خرید",
	)
	parents = {row.get("name"): row for row in headers}
	if not parents:
		return []

	item_filters = {"parent": ("in", tuple(parents)), "docstatus": 1}
	if filters.get("item"):
		item_filters["item_code"] = filters.get("item")

	items = _bounded(
		frappe.get_all(
			PURCHASE_INVOICE_ITEM,
			filters=item_filters,
			fields=[
				"name",
				"parent",
				"idx",
				"item_code",
				"item_name",
				"qty",
				"received_qty",
				"uom",
				"conversion_factor",
				"warehouse",
				"pr_detail",
			],
			order_by="parent asc, idx asc",
			limit_page_length=MAX_REPORT_ROWS + 1,
		),
		"ردیف فاکتور خرید",
	)
	if not items:
		return []

	linked = _receipt_rows(company, items)
	classification_warehouses = set()
	for item in items:
		classification_warehouses.update(_receipt_warehouses(item, linked.get(item.get("name"), ())))
	types = _warehouse_types(company, classification_warehouses)

	warehouse_filter = filters.get("warehouse")
	result = []
	for item in items:
		parent = parents.get(item.get("parent"))
		if parent is None:
			continue
		if warehouse_filter and warehouse_filter not in _receipt_warehouses(
			item, linked.get(item.get("name"), ())
		):
			continue

		row = _receipt_report_row(company, item, parent, linked.get(item.get("name"), ()), types, statuses)
		if row is not None:
			result.append(row)

	return result
