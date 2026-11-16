// The inter-app registry on the Settings form: what this app depends on, and what it changed.
//
// Two claims, kept apart on purpose. A dependency is a statement about an app; an applied change
// is a statement about the site, and only the second one may say "applied". Both come from
// `smartprocess_logistics.interop.dependency_rows`, which joins the static lists in
// `core/interop.py` with facts read from the site — the installed apps, the fixtures, the roles
// and the Custom Field metadata. This file renders the answer and decides nothing: it cannot turn
// a missing field into "applied", and it never writes.

const DEPENDENCY_ROWS_METHOD = "smartprocess_logistics.interop.dependency_rows";
const INTEROP_FIELD = "interop_registry";
const KIND_DEPENDENCY = "dependency";

frappe.ui.form.on("Logistics Settings", {
	refresh(frm) {
		render_dependency_registry(frm);
	},
});

function render_dependency_registry(frm) {
	const field = frm.fields_dict[INTEROP_FIELD];
	if (!field) {
		return;
	}

	const $wrapper = field.$wrapper;
	$wrapper.html(`<p class="text-muted small">${__("در حال خواندن وضعیت سایت…")}</p>`);

	frappe.call({
		method: DEPENDENCY_ROWS_METHOD,
		type: "GET",
		callback(response) {
			const rows = response.message || [];
			$wrapper.empty();
			$wrapper.append(
				interop_section(
					__("وابستگی‌ها"),
					rows.filter((row) => row.kind === KIND_DEPENDENCY)
				)
			);
			$wrapper.append(
				interop_section(
					__("تغییرات اعمال‌شده در این سایت"),
					rows.filter((row) => row.kind !== KIND_DEPENDENCY)
				)
			);
		},
	});
}

function interop_section(title, rows) {
	const $section = $("<div>").append(`<h6 class="mt-3">${escape_html(title)}</h6>`);
	rows.forEach((row) => $section.append(interop_entry(row)));
	return $section;
}

function interop_entry(row) {
	const facts = [
		[__("هدف"), row.purpose],
		[__("نسخه یا سازوکار"), row.constraint],
		[__("پیکربندی"), row.configuration],
		[__("نحوهٔ اعمال"), row.deployment],
		[__("روش بررسی"), row.verification],
		[__("برگشت یا حذف"), row.removal],
	];

	if (row.version) {
		facts.splice(1, 0, [__("نسخهٔ نصب‌شده"), row.version]);
	}
	if (row.missing && row.missing.length) {
		facts.push([__("ثبت نشده"), row.missing.join("، ")]);
	}

	const $entry = $("<div class='sp-interop-entry'>");
	$entry.append(
		`<div class="sp-interop-head"><b>${escape_html(row.target)}</b>` +
			`<span class="indicator-pill ${escape_html(row.severity)}">${escape_html(row.status_label)}</span></div>`
	);

	const $facts = $("<table class='sp-interop-facts'>");
	facts.forEach(([label, value]) => {
		$facts.append(
			`<tr><th>${escape_html(label)}</th><td>${escape_html(value || "—")}</td></tr>`
		);
	});
	$entry.append($facts);

	return $entry;
}

function escape_html(value) {
	return frappe.utils.escape_html(String(value == null ? "" : value));
}
