// Filters for the Missing Sales Serials report.
//
// The window is deliberately open by default. A report that silently covers ninety days answers
// a question nobody asked and hides the rows outside it, so the operator narrows it or reads all
// of it — and the only date the server applies is the one typed into `from_date`/`to_date`. An
// invoice older than the app is reported like any other: what makes a row a shortage is the
// item's `sp_serial_required` flag, not when the invoice was written.

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
			label: __("فقط ردیف‌های ناقص"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "include_cancelled",
			label: __("شامل فاکتورهای لغوشده"),
			fieldtype: "Check",
			default: 0,
		},
	],

	onload(report) {
		report.page.add_inner_button(__("ثبت سریال"), () => {
			frappe.set_route("List", "Sales Invoice", { status: "Submitted" });
		});
	},
};
