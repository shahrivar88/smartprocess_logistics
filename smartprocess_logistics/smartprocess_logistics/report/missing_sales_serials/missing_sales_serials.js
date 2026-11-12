// Filters for the Missing Sales Serials report.
//
// The window is deliberately open by default. A report that silently covers ninety days answers
// a question nobody asked and hides the rows outside it, so the operator narrows it or reads all
// of it — and the only date the server applies is the one typed into `from_date`/`to_date`. An
// invoice older than the app is reported like any other: what makes a row a shortage is the
// item's `sp_serial_required` flag, not when the invoice was written.
//
// "Incomplete" means fewer serials than *delivered* units. A row nothing was delivered for is
// not short, and the serial state column says why: no serials, cancelled or returned, partial,
// unconfirmed, complete.

const SERIAL_STATE_LABELS = {
	NO_SERIALS: __("بدون سریال"),
	CANCELLED_OR_RETURNED: __("لغو یا برگشتی"),
	PARTIAL: __("ناقص"),
	UNCONFIRMED: __("بدون تأیید تحویل"),
	COMPLETE: __("کامل"),
};

frappe.query_reports["Missing Sales Serials"] = {
	filters: [
		{
			fieldname: "company",
			label: __("شرکت"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "customer",
			label: __("مشتری"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "item_code",
			label: __("کالا"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "sales_invoice",
			label: __("فاکتور فروش"),
			fieldtype: "Link",
			options: "Sales Invoice",
		},
		{
			fieldname: "from_date",
			label: __("از تاریخ سند"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("تا تاریخ سند"),
			fieldtype: "Date",
		},
		{
			fieldname: "only_incomplete",
			label: __("فقط ردیف‌های دارای کسری تحویل"),
			fieldtype: "Check",
			default: 1,
			description: __("ردیف‌هایی که تعداد سریال ثبت‌شدهٔ آن‌ها از تعداد تحویل‌شده کمتر است"),
		},
		{
			fieldname: "include_cancelled",
			label: __("شامل فاکتورهای لغوشده"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "serial_state" && SERIAL_STATE_LABELS[value]) {
			return SERIAL_STATE_LABELS[value];
		}

		return default_formatter(value, column, data);
	},

	onload(report) {
		report.page.add_inner_button(__("ثبت سریال"), () => {
			frappe.set_route("List", "Sales Invoice", { status: "Submitted" });
		});
	},
};
