frappe.query_reports["Warehouse Inventory"] = {
	filters: [
		{ fieldname: "company", label: __("شرکت"), fieldtype: "Link", options: "Company", reqd: 1 },
		{ fieldname: "item", label: __("کالا"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("گروه کالا"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("انبار"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "include_zero_stock", label: __("نمایش موجودی صفر"), fieldtype: "Check", default: 0 },
	],
};
