frappe.query_reports["Procurement Shortage"] = {
	filters: [
		{ fieldname: "company", label: __("شرکت"), fieldtype: "Link", options: "Company", reqd: 1 },
		{ fieldname: "item", label: __("کالا"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("گروه کالا"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("انبار"), fieldtype: "Link", options: "Warehouse" },
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
