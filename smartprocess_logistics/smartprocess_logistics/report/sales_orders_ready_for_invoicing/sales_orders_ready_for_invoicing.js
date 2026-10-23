frappe.query_reports["Sales Orders Ready for Invoicing"] = {
	filters: [
		{ fieldname: "company", label: __("شرکت"), fieldtype: "Link", options: "Company", reqd: 1 },
		{ fieldname: "customer", label: __("مشتری"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item", label: __("کالا"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("گروه کالا"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "from_date", label: __("از تاریخ سفارش"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("تا تاریخ سفارش"), fieldtype: "Date" },
	],
};
