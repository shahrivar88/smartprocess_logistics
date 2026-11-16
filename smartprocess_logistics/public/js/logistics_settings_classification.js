// The classification table: one row per Warehouse Type, filled in when it is empty.
//
// Why the form fills it in at all: the mapping is stored as rows, but the list of types is the
// site's data, not this app's. An empty table is the install state and means "the effective
// policy decides", so the form shows what that policy already says — and saving it records the
// same answer, which is why opening this screen cannot reclassify a warehouse by itself.
//
// The classifications come from the server (`classification_rows`), never from this file: the
// effective policy lives in `warehouse/classification.py`, and a second copy of it here would be
// the copy that drifts. The values are the stable codes `physical`, `virtual` and `transit`; the
// Persian labels come from the app's translations, so nothing here decides a label either. This
// file's whole job is keeping the grid complete, and it decides nothing — the server refuses an
// incomplete, unknown or duplicate mapping on save.

const CLASSIFICATION_TABLE = "warehouse_classifications";
const CLASSIFICATION_ROWS_METHOD =
	"smartprocess_logistics.warehouse.classification_settings.classification_rows";

frappe.ui.form.on("Logistics Settings", {
	refresh(frm) {
		complete_classification_table(frm);
	},
});

function complete_classification_table(frm) {
	frappe.call({
		method: CLASSIFICATION_ROWS_METHOD,
		type: "GET",
		callback(response) {
			const rows = response.message || [];
			if (!rows.length) {
				return;
			}

			const saved = frm.doc[CLASSIFICATION_TABLE] || [];
			if (!saved.length) {
				// First open on a site that has never saved a mapping. Nothing is written until Save
				// is pressed, so this previews the current behaviour and changes none of it.
				rows.forEach((row) => frm.add_child(CLASSIFICATION_TABLE, row));
				frm.refresh_field(CLASSIFICATION_TABLE);
				return;
			}

			// A saved table can fall behind the site: somebody may add a Warehouse Type after the
			// mapping was recorded. Adding the row silently would change nothing but would look like
			// a change, so this only says which types are missing and leaves the decision, and the
			// save that refuses an incomplete table, to the administrator.
			const missing = rows.filter(
				(row) => !saved.some((saved_row) => classification_type_key(saved_row) === classification_type_key(row))
			);
			if (!missing.length) {
				return;
			}

			frappe.show_alert({
				indicator: "orange",
				message:
					__("ردیف این نوع‌های انبار در جدول طبقه‌بندی نیست؛ پیش از ذخیره بیفزایید:") +
					" " +
					missing
						.map((row) => classification_type_key(row) || __("بدون نوع انبار"))
						.join("، "),
			});
		},
	});
}

function classification_type_key(row) {
	return (row.warehouse_type || "").trim();
}
