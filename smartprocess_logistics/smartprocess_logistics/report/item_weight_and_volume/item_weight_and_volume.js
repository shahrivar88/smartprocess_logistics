frappe.query_reports["Item Weight and Volume"] = {
	filters: [
		{ fieldname: "company", label: __("شرکت"), fieldtype: "Link", options: "Company", reqd: 1 },
		{
			fieldname: "source",
			label: __("منبع"),
			fieldtype: "Select",
			options: "Purchase Invoice\nPurchase Order\nPurchase Receipt\nWarehouse Inventory",
			default: "Purchase Invoice",
			reqd: 1,
		},
		{ fieldname: "document", label: __("سند"), fieldtype: "Data" },
		{ fieldname: "item", label: __("کالا"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("گروه کالا"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("انبار"), fieldtype: "Link", options: "Warehouse" },
		{
			fieldname: "weight_uom",
			label: __("واحد وزن هدف"),
			fieldtype: "Link",
			options: "UOM",
		},
		{ fieldname: "from_date", label: __("از تاریخ"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("تا تاریخ"), fieldtype: "Date" },
	],

	formatter: function (value, row, column, data, default_formatter) {
		const text = default_formatter(value, row, column, data);
		if (data && data.row_type === "جمع سند") {
			return "<b>" + text + "</b>";
		}
		return text;
	},
};
