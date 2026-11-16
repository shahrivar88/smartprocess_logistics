// The «نوع انبار» filter sends the stable codes `all`, `physical`, `virtual`, `transit` and
// `untyped`; the Persian words a person reads come from `translations/fa.csv`. A code is never
// replaced by a label, so a saved bookmark keeps filtering the same rows after the wording changes.
frappe.query_reports["Warehouse Inventory"] = {
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
		{
			fieldname: "classification",
			label: __("نوع انبار"),
			fieldtype: "Select",
			options: "all\nphysical\nvirtual\ntransit\nuntyped",
			default: "all",
		},
		{ fieldname: "include_zero_stock", label: __("نمایش موجودی صفر"), fieldtype: "Check", default: 0 },
	],
};
