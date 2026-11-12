// Sales Invoice: the warehouse selectors, the stock-aware Item suggestions, and the two view
// filters above the Items table.
//
// The server owns every decision: which warehouses qualify, whether they are restricted to
// physical ones, whether a stock line is shown, and whether a row is hidden because it has no
// stock. The Desk always asks the same two queries and adds the two filter flags, which are
// per-user view preferences kept in this browser — they are never written to the document, so
// one user's filter does not change what another user sees or what is saved.
//
// The filters only narrow a list. A value already chosen in the table is never cleared: if a
// row stops matching, it stays until the operator changes it.

(function () {
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.physical_warehouses";
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_invoice";
	const BAR_ID = "sp-si-stock-filters";
	const HIDE_ZERO_ITEMS = "hide_zero_stock";
	const HIDE_EMPTY_WAREHOUSES = "hide_empty";
	const STATE_KEY = "sp_si_stock_filters:" + (frappe.session.user || "guest");

	function preferences() {
		try {
			return JSON.parse(localStorage.getItem(STATE_KEY) || "{}") || {};
		} catch (error) {
			return {};
		}
	}

	function enabled(name) {
		return preferences()[name] === true;
	}

	function remember(name, value) {
		const next = preferences();
		next[name] = value;
		try {
			localStorage.setItem(STATE_KEY, JSON.stringify(next));
		} catch (error) {
			// A browser that refuses storage loses only the memory of the choice.
		}
	}

	function warehouse_query(frm, item_code) {
		return {
			query: WAREHOUSE_QUERY,
			filters: {
				company: frm.doc.company || "",
				item_code: item_code || "",
				hide_empty: enabled(HIDE_EMPTY_WAREHOUSES) ? 1 : 0,
			},
		};
	}

	function child_item_code(cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		return (row && row.item_code) || "";
	}

	function item_query(frm) {
		return {
			query: ITEM_QUERY,
			filters: {
				company: frm.doc.company || "",
				customer: frm.doc.customer || "",
				hide_zero_stock: enabled(HIDE_ZERO_ITEMS) ? 1 : 0,
			},
		};
	}

	// Frappe keeps the last result per (doctype, search term) on the input and shows it before
	// the fresh answer arrives, so a changed filter would briefly look stale. Clearing it makes
	// the next dropdown a first search.
	function clear_link_cache(frm, fieldname) {
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		const rows = (grid && grid.grid_rows) || [];
		rows.forEach(function (row) {
			const field = row.get_field && row.get_field(fieldname);
			if (field && field.$input) {
				field.$input.cache = {};
			}
		});
	}

	function on_toggle(frm, name, checked) {
		remember(name, checked);
		clear_link_cache(frm, "item_code");
		clear_link_cache(frm, "warehouse");
		clear_link_cache(frm, "target_warehouse");
	}

	function checkbox(frm, id, name, label) {
		const input = $("<input type='checkbox'>").attr("id", id).prop("checked", enabled(name));
		input.on("change", function () {
			on_toggle(frm, name, this.checked);
		});

		return $("<label class='checkbox'></label>")
			.attr("for", id)
			.css({ display: "inline-flex", "align-items": "center", gap: "6px", "margin-inline-end": "18px" })
			.append(input)
			.append($("<span></span>").text(label));
	}

	function render_filters(frm) {
		const items = frm.fields_dict.items;
		const wrapper = items && items.$wrapper;
		if (!wrapper || wrapper.find("#" + BAR_ID).length) {
			return;
		}

		const bar = $("<div></div>")
			.attr("id", BAR_ID)
			.css({ display: "flex", "flex-wrap": "wrap", padding: "6px 0" })
			.append(checkbox(frm, "sp-hide-zero-items", HIDE_ZERO_ITEMS, "عدم نمایش کالاهای بدون موجودی"))
			.append(
				checkbox(
					frm,
					"sp-hide-empty-warehouses",
					HIDE_EMPTY_WAREHOUSES,
					"عدم نمایش انبارهای بدون موجودی کالای انتخاب‌شده"
				)
			);

		wrapper.prepend(bar);
	}

	function install(frm) {
		if (!frm.fields_dict) {
			return;
		}

		["set_warehouse", "set_target_warehouse"].forEach(function (fieldname) {
			if (frm.fields_dict[fieldname]) {
				frm.set_query(fieldname, function () {
					return warehouse_query(frm);
				});
			}
		});

		["warehouse", "target_warehouse"].forEach(function (fieldname) {
			frm.set_query(fieldname, "items", function (doc, cdt, cdn) {
				return warehouse_query(frm, child_item_code(cdt, cdn));
			});
			if (frm.fields_dict.packed_items) {
				frm.set_query(fieldname, "packed_items", function (doc, cdt, cdn) {
					return warehouse_query(frm, child_item_code(cdt, cdn));
				});
			}
		});

		frm.set_query("item_code", "items", function () {
			return item_query(frm);
		});

		render_filters(frm);
	}

	frappe.ui.form.on("Sales Invoice", {
		onload: install,
		refresh: install,
		company: install,
	});

	frappe.ui.form.on("Sales Invoice Item", {
		item_code: function (frm) {
			install(frm);
		},
	});
})();
