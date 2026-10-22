// Stock Entry — the Item Code selector, in a Material Transfer, offers only the items that are
// actually in the row's source warehouse.
//
// This is a Link query, not a new selector: the field keeps Frappe's search box, its keyboard
// navigation and its paging. ERPNext sets `erpnext.queries.item({is_stock_item: 1})` on this
// field for every Stock Entry; for a Material Transfer we replace it with the app's
// warehouse-aware query, and for every other purpose we return ERPNext's own query unchanged.
//
// The source warehouse is the row's `s_warehouse`, else the document's `from_warehouse` — the
// same fallback the stock controller uses. It is read when the search runs, so a row's choice
// and the document default are both honoured without rebuilding the grid.
//
// The filter is a convenience. The server query enforces warehouse and Item permissions and
// the form's own validation and the standard submit still decide what may actually move.

frappe.provide("smartprocess_logistics");

(function () {
	const QUERY_METHOD = "smartprocess_logistics.warehouse.item_search.items_in_stock";
	const PURPOSE = "Material Transfer";
	const PROMPT_EVERY_MS = 5000;

	let last_prompt_at = 0;

	function row_doc(cdt, cdn) {
		return cdt && cdn && locals[cdt] ? locals[cdt][cdn] : null;
	}

	function source_warehouse(doc, cdt, cdn) {
		const row = row_doc(cdt, cdn);
		return (row && row.s_warehouse) || (doc && doc.from_warehouse) || "";
	}

	function prompt_for_source() {
		const now = Date.now();
		if (now - last_prompt_at < PROMPT_EVERY_MS) {
			return;
		}
		last_prompt_at = now;
		frappe.show_alert(
			{
				message: __(
					"ابتدا انبار مبدأ را انتخاب کنید تا فهرست کالاهای موجود در آن نمایش داده شود."
				),
				indicator: "orange",
			},
			4
		);
	}

	function item_query(doc, cdt, cdn) {
		if (!doc || doc.purpose !== PURPOSE) {
			// ERPNext's own query, exactly as the Stock Entry controller set it.
			return erpnext.queries.item({ is_stock_item: 1 });
		}

		const warehouse = source_warehouse(doc, cdt, cdn);
		if (!warehouse) {
			// No source, no list: an unfiltered list would claim every item is available here.
			prompt_for_source();
			return { query: QUERY_METHOD, filters: { warehouse: "", company: doc.company || "" } };
		}

		return {
			query: QUERY_METHOD,
			filters: { warehouse: warehouse, company: doc.company || "" },
		};
	}

	function install(frm) {
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (!grid) {
			return;
		}

		const factory = function (doc, cdt, cdn) {
			return item_query(doc || frm.doc, cdt, cdn);
		};
		grid.get_field("item_code").get_query = factory;

		// Rows rendered before this line keep the controller's query on the control itself.
		(grid.grid_rows || []).forEach(function (grid_row) {
			const field = grid_row.on_grid_fields_dict && grid_row.on_grid_fields_dict.item_code;
			if (field) {
				field.get_query = factory;
			}
		});
	}

	function warn_if_unavailable(frm, cdt, cdn) {
		if (!frm.doc || frm.doc.purpose !== PURPOSE) {
			return;
		}

		const row = row_doc(cdt, cdn);
		if (!row || !row.item_code) {
			return;
		}

		const warehouse = source_warehouse(frm.doc, cdt, cdn);
		if (!warehouse) {
			return;
		}

		const item_code = row.item_code;
		frappe
			.xcall(
				QUERY_METHOD,
				{
					doctype: "Item",
					txt: "",
					searchfield: "name",
					start: 0,
					page_len: 1,
					filters: JSON.stringify({
						warehouse: warehouse,
						company: frm.doc.company || "",
						item_code: item_code,
					}),
				},
				"GET"
			)
			.then(function (rows) {
				if (rows && rows.length) {
					return;
				}
				const current = row_doc(cdt, cdn);
				if (!current || current.item_code !== item_code || current.s_warehouse !== warehouse) {
					return;
				}
				frappe.msgprint({
					title: __("جریان انبار — موجودی منبع"),
					indicator: "orange",
					message: __(
						"کالای {0} در انبار {1} موجودی مثبت ندارد. ردیف حذف یا تغییر داده نشد؛ انبار مبدأ یا کالا را اصلاح کنید.",
						[item_code, warehouse]
					),
				});
			})
			.catch(function () {
				// A failed check is not a stock statement; stay silent rather than mislead.
			});
	}

	frappe.ui.form.on("Stock Entry", {
		onload: install,
		refresh: install,
	});

	frappe.ui.form.on("Stock Entry Detail", {
		s_warehouse: warn_if_unavailable,
	});
})();
