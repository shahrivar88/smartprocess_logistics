frappe.query_reports["Sales Orders Ready for Invoicing"] = {
	filters: [
		{ fieldname: "company", label: __("شرکت"), fieldtype: "Link", options: "Company", reqd: 1 },
		{ fieldname: "customer", label: __("مشتری"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item", label: __("کالا"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("گروه کالا"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "from_date", label: __("از تاریخ سفارش"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("تا تاریخ سفارش"), fieldtype: "Date" },
	],

	formatter(value, row, column, data, default_formatter) {
		if (column.sp_quantity) {
			return smartprocess_logistics.report_defaults.quantity(value, row, column, data, default_formatter);
		}

		return default_formatter(value, row, column, data);
	},

	onload(report) {
		smartprocess_logistics.report_defaults.apply(report);
	},
};
