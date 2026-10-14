// Moving a cancelled invoice's serials onto the invoice that replaced it.
//
// When an invoice is cancelled and amended, ERPNext keeps the cancelled document and gives the
// amendment **new** item rows with new names. Nothing is lost and nothing is moved: the serials sit
// registered against a document that is now history, and the amendment reads as a shortage. This
// file is the window that offers to move them.
//
// Why it is not part of the scan station
// --------------------------------------
// The station exists to register the labels an operator has in their hand, one column at a time,
// and its whole design follows from that: a queue of pending scans, a chime, a column that turns
// green. This is a different act — a review of two documents and a selection made before anything
// is written — so it is a second toolbar button and a second dialog rather than a mode inside the
// first one. The two share the namespace below and nothing else.
//
// Where the decisions are
// -----------------------
// Nowhere in this file. Which serials can move, which rows have room, which serials are live on
// another invoice and which of them need an answer are all decided by `core/transfer.py` and
// reported by `preview_serial_transfer`. This file draws the answer, collects the operator's
// selection, and sends it back with the token the preview returned. A rule implemented here would
// be a rule the server tests could not reach.
//
// The result is a review, so the window is a table rather than a form: one row per serial the
// cancelled invoice holds, each with what will happen to it, and the operator changes what they
// disagree with before confirming.

frappe.provide("smartprocess_logistics");

/** What each refusal means on screen, and how it is emphasised. Keyed by `core/transfer.py`. */
smartprocess_logistics.TRANSFER_REASON = {
	ACKNOWLEDGEMENT_MISSING: { label: "نیازمند تأیید", tone: "is-warning" },
	ALREADY_ON_TARGET: { label: "از قبل روی اصلاحیه ثبت است", tone: "is-blocked" },
	ITEM_NOT_ON_TARGET: { label: "کالا در اصلاحیه نیست", tone: "is-blocked" },
	NO_ROOM: { label: "ردیف اصلاحیه جا ندارد", tone: "is-blocked" },
	ROW_MISMATCH: { label: "ردیف انتخابی مناسب نیست", tone: "is-blocked" },
	TARGET_ROW_NOT_ELIGIBLE: { label: "ثبت سریال برای این ردیف فعال نیست", tone: "is-blocked" },
};

/**
 * Call one of this app's endpoints, surfacing the server's sentence rather than a status code.
 *
 * The station has its own caller with its own busy handling; this one is separate because the two
 * have nothing in common beyond `frappe.call`, and sharing it would mean one of them carrying the
 * other's concerns.
 */
smartprocess_logistics.transfer_call = function (method, args) {
	return frappe
		.call({
			method: `${smartprocess_logistics.ENDPOINT}.${method}`,
			type: "POST",
			args: args,
		})
		.then((response) => response.message);
};

/** Whether this user may move serials at all: the same level the endpoint requires. */
smartprocess_logistics.may_transfer = function () {
	return smartprocess_logistics.allowed(smartprocess_logistics.ROLE.editor);
};

/**
 * The rows the table draws: the preview's own list, which the server sends in the source's order.
 *
 * Nothing is regrouped and nothing is sorted. The order an operator reviews serials in is the order
 * the rows are printed on the invoice they cancelled, and `core.transfer.preview_of` is where that
 * is decided — this window drew the table from four separately-grouped lists once, and the result
 * was a table grouped by outcome, which is the one order nobody can check against the paper.
 */
smartprocess_logistics.transfer_rows = function (preview) {
	return ((preview && preview.serials) || []).slice();
};

/**
 * What one row's status cell says, and how it is painted.
 *
 * Three branches, and the middle one reads a value the server chose rather than a code looked up
 * here: `answerable` is the server saying that an answer would change this row's outcome, which is
 * true of a serial live on another invoice and false of one there is no room for. The window never
 * has to know which reason code that is.
 */
smartprocess_logistics.transfer_status = function (row) {
	if (row.outcome === "Moved") {
		return {
			label: row.acknowledged ? "انتقال (تأییدشده)" : "انتقال",
			tone: "is-ok",
			answerable: false,
		};
	}

	if (row.outcome === "Unselected") {
		return { label: "انتخاب نشده", tone: "is-idle", answerable: false };
	}

	const known = smartprocess_logistics.TRANSFER_REASON[row.outcome];

	return {
		label: known ? known.label : row.message || row.outcome,
		tone: known ? known.tone : "is-blocked",
		answerable: Boolean(row.answerable),
	};
};

/**
 * The target rows a serial may be aimed at: the rows of its item that may take serials.
 *
 * Room is read from the server's own subtraction rather than worked out here, and a row a serial
 * already sits on stays in the list even when it is full — dropping it would silently re-aim the
 * serial at another row, which is the one thing a mapping must never do.
 */
smartprocess_logistics.transfer_row_options = function (rows, item_code, chosen) {
	return (rows || [])
		.filter((row) => row.item_code === item_code && row.serial_required)
		.filter((row) => row.room > 0 || row.row_key === chosen);
};

/**
 * The transfer window.
 *
 * Opened from the toolbar of an amended invoice. The source is read from the document
 * (`amended_from`) and never typed: a price or a name chosen by the operator is a name the server
 * would have to trust, and this app does not trust names.
 */
smartprocess_logistics.open_transfer_dialog = function (frm) {
	const source = frm.doc.amended_from;

	if (!source) {
		frappe.msgprint({
			title: __("انتقال سریال"),
			message: __("این فاکتور اصلاحیهٔ فاکتور لغو‌شده‌ای نیست، پس سریالی برای انتقال وجود ندارد."),
			indicator: "orange",
		});

		return;
	}

	const state = {
		preview: null,
		// What the source holds, learned from the first preview and kept: it is what an explicit
		// `serials` argument is subtracted from.
		serials: [],
		// The operator's own answers. These are the *only* thing this window decides, and they are
		// carried on every call rather than applied locally: the server answers about the state
		// they describe.
		unticked: new Set(),
		acknowledged: new Set(),
		aimed: {},
		// The table rows, derived once from the answer below and drawn by `draw` and
		// `transfer_table`. One derived list rather than two readers of the same answer.
		rows: [],
		busy: false,
	};

	const dialog = new frappe.ui.Dialog({
		title: __("انتقال سریال از فاکتور لغو‌شده"),
		size: "extra-large",
		fields: [{ fieldname: "body", fieldtype: "HTML" }],
		primary_action_label: __("انتقال"),
		primary_action() {
			apply();
		},
	});

	dialog.$wrapper.addClass("sp-logistics-dialog sp-transfer-dialog");
	dialog.$wrapper
		.find(".modal-footer .btn-primary")
		.prop("disabled", true)
		.attr("title", __("تا پیش‌نمایش خوانده نشود، انتقالی انجام نمی‌شود."));

	const body = () => dialog.fields_dict.body.$wrapper;

	const set_busy = (busy) => {
		state.busy = busy;
		dialog
			.$wrapper.find(".modal-footer .btn-primary")
			.prop("disabled", busy || !state.preview)
			.attr("title", busy ? __("در حال انجام...") : "");
		dialog.$wrapper.find(".sp-transfer-toolbar").toggleClass("is-busy", busy);
	};

	/**
	 * The arguments a call sends: which serials, which rows, which acknowledgments.
	 *
	 * `serials` is sent only once the window knows what the source holds, and omitted before that.
	 * The two are not the same request: omitted means every serial the source holds, and an empty
	 * list means the operator asked for none of them — so a first call that sent `[]` would draw a
	 * table of six rows all reading «انتخاب نشده» and send an apply that moved nothing. They do
	 * digest the same way when they mean the same thing (`preview_token` computes the digest over
	 * the selection *or* the source's serials), which is what makes omitting it safe.
	 */
	const arguments_for = (include_token) => {
		const args = {
			sales_invoice: source,
			target_invoice: frm.doc.name,
			mapping: { ...state.aimed },
			acknowledged: [...state.acknowledged].sort(),
		};

		if (state.serials.length) {
			args.serials = state.serials.filter((serial) => !state.unticked.has(serial));
		}

		if (include_token) {
			args.token = state.preview.token;
		}

		return args;
	};

	/**
	 * Ask the server what the operator's current answer would do.
	 *
	 * Every interaction goes through here, including ticking an acknowledgment, and that is the
	 * point: the outcome in each row's cell is the *server's* answer about the state the operator
	 * has described, so a row cannot read "needs an answer" after the operator has answered it.
	 * The window made that mistake first — it drew the table from the last preview and promoted a
	 * ticked row locally — which meant the table claimed something the server had never said. A
	 * review table is not a scan station; a round trip per tick costs nothing here.
	 *
	 * The operator's own answers are kept across the call, because the request carries them: the
	 * answer that comes back is about *this* selection, so nothing is forgotten and nothing is
	 * re-guessed.
	 */
	const preview = () => {
		set_busy(true);

		return smartprocess_logistics
			.transfer_call("preview_serial_transfer", arguments_for(false))
			.then((answer) => {
				state.preview = answer;
				state.rows = smartprocess_logistics.transfer_rows(answer);
				state.serials = state.rows.map((row) => row.serial);

				// A row the operator ticked that the server now reports as moving is an
				// acknowledgment that landed, so the local set is rebuilt from the answer rather
				// than kept beside it. One of the two has to win, and it is the server's.
				state.acknowledged = new Set(
					answer.serials.filter((row) => row.acknowledged).map((row) => row.serial)
				);
				state.unticked = new Set(
					answer.serials.filter((row) => row.outcome === "Unselected").map((row) => row.serial)
				);

				draw();
				set_busy(false);

				return answer;
			})
			.catch(() => {
				// A refusal is already a sentence from the server, and `frappe.call` has shown it.
				// What is left is to leave the button unusable rather than to pretend the preview
				// happened: an apply with no preview has no token to send.
				state.preview = null;
				body().html(
					`<div class="sp-logistics-empty">${frappe.utils.escape_html(
						__("پیش‌نمایش خوانده نشد. پنجره را ببندید و دوباره باز کنید.")
					)}</div>`
				);
				set_busy(false);
			});
	};

	const draw = () => {
		if (!state.preview) {
			return;
		}

		const counts = state.preview.counts || {};
		const rows = state.rows;
		const waiting = rows.filter((row) => row.answerable).length;
		const blocked = rows.filter(
			(row) => row.outcome !== "Moved" && row.outcome !== "Unselected" && !row.answerable
		).length;
		const moving = rows.filter((row) => row.outcome === "Moved").length;

		body().html(`
			<div class="sp-transfer-toolbar">
				<div class="sp-logistics-summary">
					<span class="sp-logistics-chip"><b>${frappe.utils.escape_html(source)}</b>
						<span class="sp-transfer-label">${__("فاکتور لغو‌شده")}</span></span>
					<span class="sp-logistics-chip"><b>${frappe.utils.escape_html(frm.doc.name)}</b>
						<span class="sp-transfer-label">${__("فاکتور اصلاحی")}</span></span>
					<span class="sp-logistics-chip">${__("سریال‌های مبدأ")}: <b>${Number(
						counts.source || 0
					)}</b></span>
					<span class="sp-logistics-chip is-complete">${__("انتقال")}: <b>${moving}</b></span>
					<span class="sp-logistics-chip is-short">${__("بدون جا")}: <b>${blocked}</b></span>
					<span class="sp-logistics-chip">${__("نیازمند تأیید")}: <b>${waiting}</b></span>
				</div>
				<button class="btn btn-xs btn-default sp-transfer-refresh">${__(
					"خواندن دوباره"
				)}</button>
			</div>
			<div class="sp-transfer-body"></div>
		`);

		const table = rows.length
			? smartprocess_logistics.transfer_table(state)
			: `<div class="sp-logistics-empty">${frappe.utils.escape_html(
					__("فاکتور لغو‌شده سریالی ندارد که بشود منتقل کرد.")
			  )}</div>`;

		body().find(".sp-transfer-body").html(table);
	};

	body().on("click", ".sp-transfer-refresh", (event) => {
		event.preventDefault();

		if (!state.busy) {
			preview();
		}
	});

	// Each of the three answers records what the operator said and then asks the server what it
	// does — including the acknowledgment, because whether it turns into a move is the server's
	// answer and not the window's assumption.
	body().on("change", ".sp-transfer-row-select", function () {
		const serial = $(this).attr("data-serial");
		const aimed = $(this).val();

		if (aimed) {
			state.aimed[serial] = aimed;
		} else {
			delete state.aimed[serial];
		}

		preview();
	});

	body().on("change", ".sp-transfer-ack", function () {
		const serial = $(this).attr("data-serial");

		if ($(this).is(":checked")) {
			state.acknowledged.add(serial);
		} else {
			state.acknowledged.delete(serial);
		}

		preview();
	});

	body().on("change", ".sp-transfer-pick", function () {
		const serial = $(this).attr("data-serial");

		if ($(this).is(":checked")) {
			state.unticked.delete(serial);
		} else {
			state.unticked.add(serial);
		}

		preview();
	});

	const apply = () => {
		if (!state.preview || state.busy) {
			return;
		}

		const args = arguments_for(true);
		const aimed = Object.keys(args.mapping).length;

		frappe.confirm(
			aimed
				? __("انتقال {0} سریال از فاکتور لغو‌شده، با نگاشت {1} ردیف؟", [
						args.serials.length,
						aimed,
				  ])
				: __("انتقال {0} سریال از فاکتور لغو‌شده به این فاکتور؟", [args.serials.length]),
			() => {
				set_busy(true);

				smartprocess_logistics
					.transfer_call("apply_serial_transfer", args)
					.then((answer) => {
						dialog.hide();
						smartprocess_logistics.transfer_result(frm, answer);
					})
					.catch(() => {
						// The server refused, and its refusal is already on screen. The token it
						// was carrying may no longer describe the site — a refusal is exactly what
						// a stale token produces — so the preview is fetched again rather than the
						// same request being offered a second time.
						state.preview = null;
						set_busy(false);

						return preview();
					});
			}
		);
	};

	dialog.show();
	dialog.$wrapper.find(".sp-transfer-body").html(
		`<div class="sp-logistics-empty">${frappe.utils.escape_html(
			__("لطفاً منتظر بمانید؛ پیش‌نمایش در حال خواندن است…")
		)}</div>`
	);
	preview();

	return dialog;
};

/**
 * The table: one row per serial, and the operator's answer in the cells they can change.
 *
 * A serial that will move can be unticked (it stays where it is), a row with more than one eligible
 * option gets a select, and a serial that needs an acknowledgment gets a checkbox that says so in
 * words. A serial that cannot move gets no control at all and the reason in the cell: a disabled
 * checkbox would suggest there is a state of the world in which it moves.
 */
smartprocess_logistics.transfer_table = function (state) {
	const rows = state.rows || [];
	const target_rows = (state.preview && state.preview.target_rows) || [];
	const cells = rows
		.map((row) => {
			const status = smartprocess_logistics.transfer_status(row);
			const picked = !state.unticked.has(row.serial);
			const serial = frappe.utils.escape_html(row.serial);
			let control = "";

			// A row the operator's own answer controls — one that is moving, or one they have taken
			// out of the selection — carries the tick that controls it. A row the *server* decided
			// carries nothing: it is not that the operator may not change their mind about a row
			// with no room, it is that no answer of theirs would move it, and a disabled box would
			// suggest there is one. The row they unticked gets its box back as well, which is what
			// makes a mis-tick reversible inside this window instead of by closing it.
			if (row.outcome === "Moved" || row.outcome === "Unselected") {
				control = `<input type="checkbox" class="sp-transfer-pick" data-serial="${serial}" ${
					picked ? "checked" : ""
				}>`;
			} else if (status.answerable) {
				control = `<input type="checkbox" class="sp-transfer-ack" data-serial="${serial}" ${
					state.acknowledged.has(row.serial) ? "checked" : ""
				} title="${frappe.utils.escape_html(__("اگر تأیید شود، این سریال منتقل می‌شود."))}">`;
			}

			const options =
				row.outcome === "Moved"
					? smartprocess_logistics.transfer_row_options(target_rows, row.item_code, row.target_row)
					: [];
			const aimed =
				options.length > 1
					? `<select class="sp-transfer-row-select" data-serial="${serial}">${options
							.map(
								(option) =>
									`<option value="${frappe.utils.escape_html(option.row_key)}" ${
										option.row_key === row.target_row ? "selected" : ""
									}>${frappe.utils.escape_html(__("ردیف {0}", [option.index]))}</option>`
							)
							.join("")}</select>`
					: row.outcome === "Moved"
					? frappe.utils.escape_html(__("ردیف {0}", [row.target_index || ""]))
					: "";

			return `<tr>
				<td class="sp-transfer-pick-cell">${control}</td>
				<td class="sp-transfer-serial" dir="ltr">${serial}</td>
				<td dir="ltr">${frappe.utils.escape_html(row.item_code)}</td>
				<td class="sp-transfer-status ${status.tone}" title="${frappe.utils.escape_html(
					row.message || ""
				)}">${frappe.utils.escape_html(status.label)}</td>
				<td>${aimed}</td>
			</tr>`;
		})
		.join("");

	return `<table class="sp-transfer-table">
		<thead><tr>
			<th></th>
			<th>${__("سریال")}</th>
			<th>${__("کالا")}</th>
			<th>${__("وضعیت")}</th>
			<th>${__("ردیف مقصد")}</th>
		</tr></thead>
		<tbody>${cells}</tbody>
	</table>`;
};

/**
 * What happened, in one sentence, and the form reloaded so the columns show the new registrations.
 *
 * The message names the serials that stayed behind as a count and not as a list: on the invoice
 * this was written for, eight of eighteen stay, and a dialog listing them would be a second table
 * to read rather than a result to understand. The record holds the list, and `Logistics Serial
 * Transfer` is where it is read from.
 */
smartprocess_logistics.transfer_result = function (frm, answer) {
	const counts = (answer && answer.counts) || {};
	const stayed = Number(counts.refused || 0) + Number(counts.unselected || 0);
	const lines = [
		__("انتقال انجام شد: {0} سریال منتقل شد.", [Number(counts.moved || 0)]),
		__("{0} سریال روی فاکتور لغو‌شده ماند.", [stayed]),
		answer && answer.transfer ? __("سابقهٔ انتقال: {0}", [answer.transfer]) : "",
	].filter(Boolean);

	frappe.msgprint({
		title: __("انتقال سریال"),
		message: lines.map((line) => frappe.utils.escape_html(line)).join("<br>"),
		indicator: Number(counts.moved || 0) ? "green" : "orange",
	});

	frm.reload_doc();
};

// --- the toolbar button ----------------------------------------------------------------------

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus === 2 || !frm.doc.amended_from) {
			return;
		}

		if (!smartprocess_logistics.settings() || !smartprocess_logistics.may_transfer()) {
			return;
		}

		if (!smartprocess_logistics.reaches_company(frm.doc.company)) {
			return;
		}

		// No group: a group becomes a dropdown, and a dropdown is one more click for the only two
		// buttons this app puts on an invoice. The two are separate actions rather than a menu of
		// one thing.
		frm.add_custom_button(__("انتقال سریال"), () =>
			smartprocess_logistics.open_transfer_dialog(frm)
		);
	},
});
