// Filters for the Purchased Not Received report.
//
// The report defaults to the rows that still owe goods, which is the question it exists to
// answer. Every row is one line of a submitted Purchase Invoice: the accounting receipt is what
// ERPNext itself recorded, and the physical arrival is the part of it that reached a warehouse a
// sale can be served from. A line received only into a transit or a virtual warehouse is
// received and not arrived, so the two columns disagree on purpose.
//
// "Cannot be determined" is not zero. It appears where a receipt row's warehouse or unit cannot
// be matched to the invoice row, and the notes column says which.
//
// The company is mandatory, and the default is what lets the report run when it opens: Frappe
// shows "Please set filters" instead of loading a report whose mandatory filter is empty.

frappe.query_reports["Purchased Not Received"] = {
	filters: [
		{
			fieldname: "company",
			label: __("شرکت"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("از تاریخ فاکتور"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("تا تاریخ فاکتور"),
			fieldtype: "Date",
		},
		{
			fieldname: "supplier",
			label: __("تأمین‌کننده"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "purchase_invoice",
			label: __("فاکتور خرید"),
			fieldtype: "Link",
			options: "Purchase Invoice",
		},
		{
			fieldname: "item",
			label: __("کالا"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "warehouse",
			label: __("انبار مقصد"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "receipt_status",
			label: __("وضعیت دریافت"),
			fieldtype: "Select",
			default: "outstanding",
			options: [
				{ label: __("همه"), value: "all" },
				{ label: __("دریافت‌نشده و ناقص"), value: "outstanding" },
				{ label: __("دریافت‌نشده"), value: "not_received" },
				{ label: __("دریافت ناقص"), value: "partially_received" },
				{ label: __("دریافت کامل"), value: "fully_received" },
				{ label: __("بیش از مقدار خرید"), value: "over_received" },
				{ label: __("برگشت خرید"), value: "purchase_return" },
			],
		},
	],

	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "physical_qty" && (data || {}).physical_qty === null) {
			return __("قابل تعیین نیست");
		}

		return default_formatter(value, column, data);
	},
};
