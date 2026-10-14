// The two buttons that hand this app's records to a file, and take them back.
//
// Why the records travel through a file at all: Frappe's uninstaller removes the table behind
// every DocType of the app being removed, so nothing this app stores can outlive its own
// removal. A full site backup covers disaster recovery; this covers the other case — the app
// goes away and its serial history has to come back with it.
//
// Two rules this file follows, both of them the app's own:
//
//   * It decides nothing. The server validates the file, plans the import and authorizes the
//     write; this file collects a file, shows what the server said, and asks for confirmation.
//   * It never writes to the site's disk. The export is turned into a download in the browser
//     and the import reads a file the operator picked, so neither path needs a writable app
//     directory — which this deployment does not have.

// One apply at a time, for the whole form. A second confirmation while the first request is in
// flight would be a duplicate submission, and the server's answer to that would be an idempotent
// no-op rather than an error — which is the right answer to a duplicate, and the wrong thing to
// send from a button that was pressed twice.
let applying = false;

frappe.ui.form.on("Logistics Settings", {
	refresh(frm) {
		// A convenience, not a permission: the endpoints check the level themselves and refuse
		// with their own sentence. Hiding the buttons only keeps a Viewer from pressing one that
		// cannot work.
		if (!is_manager()) {
			return;
		}

		frm.add_custom_button(__("خروجی کامل اطلاعات سریال‌ها"), () => export_everything(frm));
		frm.add_custom_button(__("بازیابی اطلاعات سریال‌ها"), () => restore_everything(frm, "live"));

		// The second restore, and a separate button on purpose. It writes to cancelled invoices —
		// the one thing every other write in this app refuses (I22) — so it is not a mode the
		// ordinary button falls into: an operator who means the ordinary restore must not be one
		// click from the historical one, and the archivist who means this must be able to find it.
		if (is_archivist()) {
			frm.add_custom_button(__("بازآوری تاریخچهٔ فاکتورهای لغو‌شده"), () =>
				restore_everything(frm, "history")
			);
			frm.add_custom_button(__("بازآوری سابقه‌های انتقال"), () =>
				restore_everything(frm, "records")
			);
		}
	},
});

// The two restores, and the whole of what differs between them. Everything else — the file
// chooser, the preview, the confirmation, the reporting — is one flow, because it is one act from
// the operator's side and a second copy of it would be a second place for the warnings to drift.
const RESTORES = {
	live: {
		title: __("بازیابی سریال‌ها"),
		preview_endpoint: "preview_serial_restore",
		apply_endpoint: "apply_serial_restore",
		confirm_label: __("ثبت کن"),
		warning: "",
	},
	records: {
		title: __("بازآوری سابقه‌های انتقال"),
		preview_endpoint: "preview_restore_audit_records",
		apply_endpoint: "restore_audit_records",
		confirm_label: __("بازآوری کن"),
		// A different warning for a different act: this one writes documents that *record* acts
		// performed on another site, which is why each one says so on its own face.
		warning: __(
			"هشدار: این عمل سابقه‌های انتقال را از آرشیو می‌سازد. هر سابقه‌ای که از این‌جا ساخته شود، خودش را بازآوری‌شده معرفی می‌کند و هرگز روی سابقه‌ای با همان نام بازنویسی نمی‌شود."
		),
	},
	history: {
		title: __("بازآوری تاریخچهٔ فاکتورهای لغو‌شده"),
		preview_endpoint: "preview_cancelled_serial_history",
		apply_endpoint: "restore_cancelled_serial_history",
		confirm_label: __("بازآوری کن"),
		// Shown at the top of the preview and not only in a tooltip. What makes this button
		// different from the one above it is that it writes to documents the app otherwise never
		// writes to, and an operator pressing it is entitled to be told that before they confirm
		// rather than after.
		//
		// Plain text, no markup: the dialog escapes what it is given, so an emphasis marker would
		// reach the operator as two asterisks. The box it is drawn in is what makes it stand out.
		warning: __(
			"هشدار: این عمل ثبت‌ها را روی فاکتورهای لغو‌شده برمی‌گرداند. ثبت‌های موجود دست‌نخورده می‌مانند و هیچ ردیفی بازنویسی یا حذف نمی‌شود."
		),
	},
};

function is_manager() {
	return (
		frappe.user.has_role("Logistics Serial Manager") || frappe.user.has_role("System Manager")
	);
}

/** The role the historical restore asks for, on top of a Manager. */
function is_archivist() {
	return (
		frappe.user.has_role("Logistics Serial Archivist") || frappe.user.has_role("System Manager")
	);
}

/** The mode's own names, or the ordinary restore's if the caller passed one that is not ours. */
function mode_of(name) {
	return RESTORES[name] || RESTORES.live;
}

function export_everything(frm) {
	frappe.dom.freeze(__("در حال آماده‌سازی خروجی…"));

	frappe.call({
		method: "smartprocess_logistics.api.endpoints.export_serials",
		type: "GET",
		callback: (response) => {
			frappe.dom.unfreeze();

			const payload = response.message || {};

			download(payload.filename, JSON.stringify(payload.document, null, 1));

			const counts = payload.counts || {};

			frappe.show_alert({
				message: __("خروجی گرفته شد: {0} سریال و {1} ردیف.", [
					counts.serials || 0,
					counts.assignments || 0,
				]),
				indicator: "green",
			});
		},
		error: () => frappe.dom.unfreeze(),
	});
}

function download(filename, text) {
	const blob = new Blob([text], { type: "application/json;charset=utf-8" });
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");

	link.href = url;
	link.download = filename || "smartprocess-logistics-serials.json";
	document.body.appendChild(link);
	link.click();
	document.body.removeChild(link);
	URL.revokeObjectURL(url);
}

function restore_everything(frm, mode) {
	pick_file()
		.then((file) => preview(file, mode))
		.catch(() => {});
}

function pick_file() {
	return new Promise((resolve) => {
		const input = document.createElement("input");

		input.type = "file";
		input.accept = ".json,application/json";
		input.onchange = () => {
			const file = input.files && input.files[0];

			if (!file) {
				return;
			}

			const reader = new FileReader();

			reader.onload = () => resolve({ name: file.name, content: reader.result });
			reader.readAsText(file, "utf-8");
		};
		input.click();
	});
}

function preview(file, mode) {
	const restore = mode_of(mode);

	frappe.dom.freeze(__("در حال بررسی فایل…"));

	return frappe
		.call({
			method: `smartprocess_logistics.api.endpoints.${restore.preview_endpoint}`,
			type: "POST",
			args: { content: file.content },
		})
		.then((response) => {
			frappe.dom.unfreeze();

			// `frappe.call` resolves with the response it received, whose payload is under
			// `message`. Treating that response as the payload is what made the first version of
			// this dialog show nothing and send `token: undefined` — a preview that never reached
			// the operator, and an apply the server was right to refuse. The return value of a
			// `callback:` option is discarded, so unwrapping has to happen here.
			const preview = (response && response.message) || {};

			if (!preview.token) {
				report_problem(
					__("پیش‌نمایش کامل نرسید. فایل را دوباره انتخاب کنید."),
					__("پیش‌نمایش بازیابی")
				);

				return;
			}

			show_preview(preview, file, restore);
		})
		.catch(() => {
			frappe.dom.unfreeze();
		});
}

function show_preview(preview, file, restore) {
	const rows = preview.to_create || {};
	const skipped = preview.to_skip || {};

	const lines = [
		__("فایل: {0}", [file.name]),
		__("سایت مبدأ: {0}", [preview.source_site || "—"]),
		__("زمان خروجی: {0}", [preview.exported_on || "—"]),
		"",
		__("برای ثبت: {0} سریال و {1} ردیف.", [rows.serials || 0, rows.assignments || 0]),
		__("قبلاً موجود: {0} سریال و {1} ردیف.", [skipped.serials || 0, skipped.assignments || 0]),
		__("قابل ثبت نیست: {0} ردیف.", [preview.conflicts || 0]),
	];

	// What to say about the file's records, which is not the same thing in every mode: the two
	// assignment restores place none of them and the records restore places them. The first
	// version of this line said "they are not restored" in all three, which in the third mode was
	// the one sentence it must never print — a preview that contradicts what the button does.
	const records_in_file = preview.records_in_file || 0;

	if (records_in_file) {
		const placing = (preview.to_create_records || {}).transfers || 0;

		lines.push(
			"",
			placing
				? __("این فایل {0} سابقهٔ انتقال دارد و {1} از آن‌ها بازآوری می‌شود.", [
						records_in_file,
						placing,
				  ])
				: __("این فایل {0} سابقهٔ انتقال هم دارد. بازآوری آن‌ها انجام نمی‌شود و فقط سابقه می‌ماند.", [
						records_in_file,
				  ])
		);
	}

	const dialog = new frappe.ui.Dialog({
		title: restore.title,
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "summary" }],
		primary_action_label: restore.confirm_label,
		primary_action: () => {
			dialog.hide();
			apply(preview.token, file, restore);
		},
		secondary_action_label: __("انصراف"),
	});

	let html = "";

	if (restore.warning) {
		html += `<div class="sp-restore-warning">${frappe.utils.escape_html(restore.warning)}</div>`;
	}

	html += `<div style="white-space: pre-line; margin-bottom: 12px;">${frappe.utils.escape_html(
		lines.join("\n")
	)}</div>`;

	if ((preview.sample || []).length) {
		html += `<table class="table table-bordered"><thead><tr>
			<th>${__("ردیف")}</th><th>${__("دلیل")}</th><th>${__("جزئیات")}</th>
			</tr></thead><tbody>`;

		(preview.sample || []).forEach((row) => {
			html += `<tr><td>${frappe.utils.escape_html(row.row || "")}</td>
				<td>${frappe.utils.escape_html(row.message || "")}</td>
				<td>${frappe.utils.escape_html(row.detail || "")}</td></tr>`;
		});

		html += "</tbody></table>";
	}

	dialog.fields_dict.summary.$wrapper.html(html);
	dialog.show();
}

function apply(token, file, restore) {
	// The token is what ties this write to the file the operator actually saw, and the server
	// requires it. Sending the request without one would be asking the server to weaken that
	// check; refusing here says the same thing to the operator in a sentence they can act on.
	if (!token) {
		report_problem(
			__("نشانهٔ تأیید پیش‌نمایش در دسترس نیست. فایل را دوباره انتخاب کنید."),
			restore.title
		);

		return;
	}

	// One apply at a time, however many times the button is pressed. The dialog closes on the
	// first press, but a reopened preview could be confirmed twice, and the second request would
	// be a duplicate submission rather than an idempotent one.
	if (applying) {
		return;
	}

	applying = true;
	frappe.dom.freeze(__("در حال بازیابی…"));

	return frappe
		.call({
			method: `smartprocess_logistics.api.endpoints.${restore.apply_endpoint}`,
			type: "POST",
			args: { content: file.content, token: token },
		})
		.then((response) => {
			const result = (response && response.message) || {};
			const counts = result.counts || {};
			const created = counts.created || {};

			const records = created.transfers || [];

			frappe.msgprint({
				title: restore.title,
				indicator: "green",
				message: [
					__("ثبت‌شده: {0} سریال و {1} ردیف.", [created.serials || 0, created.assignments || 0]),
					records.length ? __("سابقه‌های بازآوری‌شده: {0}.", [records.length]) : "",
					__("رد شده (از قبل موجود): {0} ردیف.", [
						(counts.skipped && counts.skipped.assignments) || 0,
					]),
					__("قابل ثبت نبود: {0} ردیف.", [counts.conflicts || 0]),
				]
					.filter(Boolean)
					.join("<br>"),
			});
		})
		.catch(() => {
			// The server's own refusal has already been shown to the operator by Frappe; the
			// freeze is what this has to undo.
		})
		.always(() => {
			applying = false;
			frappe.dom.unfreeze();
		});
}

function report_problem(message, title) {
	frappe.msgprint({ title: title, indicator: "red", message: message });
}
