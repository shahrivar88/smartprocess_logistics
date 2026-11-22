// Operational inventory and order shortage.
//
// Rows are already sorted by shortage, descending. The two "related" columns hold document
// names separated by ", " and are drawn as links here, because a Link column can only hold one.
//
// The company is mandatory, and the default is what lets the report run when it opens: Frappe
// shows "Please set filters" instead of loading a report whose mandatory filter is empty.

frappe.query_reports["تراز موجودی"] = {
	filters: [
		{
			fieldname: "company",
			label: __("شرکت"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "item", label: __("کالا"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("گروه کالا"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("انبار"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "only_shortage", label: __("فقط اقلام دارای کسری"), fieldtype: "Check", default: 0 },
		{ fieldname: "hide_zero", label: __("عدم نمایش اقلام صفر"), fieldtype: "Check", default: 0 },
		{ fieldname: "from_date", label: __("از تاریخ سفارش"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("تا تاریخ سفارش"), fieldtype: "Date" },
	],

	formatter: function (value, row, column, data, default_formatter) {
		const text = column.sp_quantity
			? smartprocess_logistics.report_defaults.quantity(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);

		if (column.fieldname === "related_quotations" || column.fieldname === "related_sales_orders") {
			if (!data || !data[column.fieldname]) {
				return "";
			}
			const doctype = column.fieldname === "related_quotations" ? "Quotation" : "Sales Order";
			return String(data[column.fieldname])
				.split(", ")
				.filter(Boolean)
				.map(function (name) {
					return frappe.utils.get_form_link(doctype, name, true);
				})
				.join(", ");
		}

		return text;
	},

	onload: function (report) {
		smartprocess_logistics.report_defaults.apply(report);
	},
};
