// The transfer window's own rules, run rather than read.
//
// The window is browser code and most of its behaviour is interaction: a dialog, a table, a select
// per row. What is worth executing is what it does with the server's answer — how a preview becomes
// table rows, what each serial's status cell says, and which target rows a serial may be aimed at —
// plus the one thing a table built out of strings can get wrong invisibly: escaping.
//
// It is a script and not part of the Python suite because it needs a JavaScript runtime, and the
// suite is documented as running anywhere. Node is in the lab image, so this runs there:
//
//     node deployment/lab/verify_transfer_logic.js
//
// It loads the **real** files, in the order `hooks.py` registers them, with a stand-in for `frappe`
// that implements only what loading them touches. Loading both is part of the test: the transfer
// window uses the namespace the station establishes, so a change that made the second file
// unloadable beside the first would fail here rather than in a browser.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const app = path.join(__dirname, "..", "..", "smartprocess_logistics", "public", "js");
const STATION = path.join(app, "sales_invoice_serial_button.js");
const TRANSFER = path.join(app, "sales_invoice_serial_transfer.js");

// --- the stand-in ------------------------------------------------------------------------

const handlers = {};

const store = {
	values: {},
	getItem(key) {
		return Object.prototype.hasOwnProperty.call(this.values, key) ? this.values[key] : null;
	},
	setItem(key, value) {
		this.values[key] = String(value);
	},
};

const frappe = {
	provide(namespace) {
		let node = globalThis;

		for (const part of namespace.split(".")) {
			node[part] = node[part] || {};
			node = node[part];
		}
	},
	ui: {
		form: {
			on(doctype, table) {
				handlers[doctype] = handlers[doctype] || [];
				handlers[doctype].push(table);
			},
		},
	},
	user: { has_role: () => true },
	session: { user: "transfer@example.test" },
	utils: {
		escape_html: (value) =>
			String(value).replace(
				/[&<>"']/g,
				(character) =>
					({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character])
			),
		icon: () => "",
	},
	db: { get_single_value: () => 1 },
	defaults: { get_user_permissions: () => ({}) },
	call: () => Promise.resolve({ message: {} }),
	msgprint: () => {},
	confirm: (_message, on_yes) => on_yes(),
};

globalThis.frappe = frappe;
globalThis.window = globalThis;
globalThis.localStorage = store;
globalThis.__ = (text, args) => {
	if (args === undefined) {
		return text;
	}

	const list = Array.isArray(args) ? args : [args];

	return text.replace(/\{(\d+)\}/g, (whole, index) =>
		Object.prototype.hasOwnProperty.call(list, Number(index)) ? String(list[Number(index)]) : whole
	);
};

// In the order `hooks.py` lists them.
vm.runInThisContext(fs.readFileSync(STATION, "utf8"), { filename: STATION });
vm.runInThisContext(fs.readFileSync(TRANSFER, "utf8"), { filename: TRANSFER });

const transfer = globalThis.smartprocess_logistics;
const source = fs.readFileSync(TRANSFER, "utf8");

// --- the checks --------------------------------------------------------------------------

let failures = 0;

function check(condition, description, detail = "") {
	console.log(`  ${condition ? "ok  " : "FAIL"} ${description}`);

	if (!condition) {
		failures += 1;

		if (detail) {
			console.log(`       ${detail}`);
		}
	}
}

/**
 * The preview of the case this feature was written for: eighteen serials of one item on a cancelled
 * invoice, an amendment that invoices ten of it, one of them live on another active invoice, and
 * one item the amendment no longer carries.
 *
 * The shape is the server's — one list in the source's order, from `core.transfer.preview_of`.
 */
function live_case(overrides = {}) {
	const serials = [];

	for (let index = 1; index <= 18; index += 1) {
		const row = {
			serial: `W-${String(index).padStart(4, "0")}`,
			item_code: "PR89889",
			source_row: "row-3",
			source_index: 3,
			order: index - 1,
			acknowledged: false,
			answerable: false,
			message: "",
			detail: "",
			target_row: null,
			target_index: null,
			outcome: "Unselected",
		};

		if (index === 1) {
			row.outcome = "ACKNOWLEDGEMENT_MISSING";
			row.answerable = true;
			row.message = "این سریال روی یک فاکتور فعال دیگر هم ثبت است و تأیید جداگانه لازم دارد";
		} else if (index <= 11) {
			row.outcome = "Moved";
			row.target_row = "t-2";
			row.target_index = 2;
		} else {
			row.outcome = "NO_ROOM";
			row.message = "ردیف‌های این کالا در فاکتور اصلاحی جا ندارند";
			row.detail = "PR89889";
		}

		serials.push(row);
	}

	return Object.assign(
		{
			serials: serials,
			stale: [],
			counts: { source: 18, moved: 10, refused: 8, unselected: 0, unresolved: 0, acknowledged: 0 },
			target_rows: [
				{ row_key: "t-1", item_code: "PR89887", index: 1, capacity: 5, held: 0, room: 5, serial_required: true },
				{ row_key: "t-2", item_code: "PR89889", index: 2, capacity: 10, held: 0, room: 10, serial_required: true },
				{ row_key: "t-3", item_code: "PR89889", index: 3, capacity: 4, held: 0, room: 4, serial_required: true },
				{ row_key: "t-4", item_code: "PR89888", index: 4, capacity: 6, held: 0, room: 6, serial_required: false },
			],
		},
		overrides
	);
}

console.log(`transfer window logic from ${path.relative(process.cwd(), TRANSFER)}\n`);

console.log("both files load, in the order hooks.py registers them");
check(typeof transfer.open_transfer_dialog === "function", "the transfer window is defined");
check(
	typeof transfer.open_serial_dialog === "function" && Boolean(transfer.ENDPOINT),
	"the station's own namespace survived the second file loading beside it"
);
check(
	(handlers["Sales Invoice"] || []).length === 2,
	"both files registered a Sales Invoice hook rather than the second replacing the first",
	`handlers: ${(handlers["Sales Invoice"] || []).length}`
);
check(transfer.METHOD.add_row_serial === "POST", "the station's endpoint table is untouched");
check(transfer.may_transfer() === true, "an editor may transfer");

console.log("\none row per serial the cancelled invoice holds");
const rows = transfer.transfer_rows(live_case());

check(rows.length === 18, "eighteen source serials are eighteen rows", `got ${rows.length}`);
check(
	rows.map((row) => row.order).join(",") === rows.map((_row, index) => index).join(","),
	"and they arrive in the source's own order, numbering them 0 to 17"
);
check(rows[0].outcome === "ACKNOWLEDGEMENT_MISSING", "the first serial is the one needing an answer");
check(rows.filter((row) => row.outcome === "Moved").length === 10, "ten of them are moving");
check(rows.filter((row) => row.answerable).length === 1, "one is waiting on an answer");
check(rows.filter((row) => row.outcome === "NO_ROOM").length === 7, "seven have no room");
check(rows[1].target_row === "t-2", "a moving row names the target row it will be written to");
check(rows[1].target_index === 2, "and its number, for the operator to read");
check(rows[1].acknowledged === false, "a serial that was never in question is not marked acknowledged");
check(
	transfer.transfer_rows({}).length === 0 && transfer.transfer_rows(null).length === 0,
	"and no answer at all is an empty table rather than an exception"
);

console.log("\nthe rows are the server's list, regrouped by nothing");
const reversed = live_case({ serials: live_case().serials.slice().reverse() });

check(
	transfer.transfer_rows(reversed)[0].serial === "W-0018",
	"the window draws the list in the order it was given, and imposes no order of its own"
);

console.log("\nan acknowledged serial reads differently from one that was never in question");
const acked = live_case({
	serials: [
		{
			serial: "W-0001",
			item_code: "PR89889",
			order: 0,
			acknowledged: true,
			answerable: true,
			message: "",
			outcome: "Moved",
			target_row: "t-2",
			target_index: 2,
		},
	],
});

check(
	transfer.transfer_status(transfer.transfer_rows(acked)[0]).label === "انتقال (تأییدشده)",
	"it says so in the cell"
);
check(
	transfer.transfer_status(transfer.transfer_rows(acked)[0]).tone === "is-ok",
	"and it is painted as a move"
);

console.log("\nwhat each status cell says");
const blocked_cell = transfer.transfer_status({ outcome: "NO_ROOM", answerable: false });
const waiting_cell = transfer.transfer_status({ outcome: "ACKNOWLEDGEMENT_MISSING", answerable: true });

check(transfer.transfer_status({ outcome: "Moved" }).tone === "is-ok", "a move is the ok tone");
check(
	transfer.transfer_status({ outcome: "Moved" }).label === "انتقال",
	"and an unacknowledged move says only that it moves"
);
check(blocked_cell.tone === "is-blocked", "a row with no room is blocked");
check(blocked_cell.label.startsWith("ردیف اصلاحیه"), "and says why in Persian");
check(waiting_cell.tone === "is-warning", "a serial needing an answer is a warning, not a failure");
check(waiting_cell.answerable === true, "and the window offers it a control");
check(blocked_cell.answerable === false, "while a blocked one is offered none");
check(
	transfer.transfer_status({ outcome: "Unselected" }).answerable === false &&
		transfer.transfer_status({ outcome: "Unselected" }).label === "انتخاب نشده",
	"a serial nobody asked for is neither moving nor refused"
);
check(
	blocked_cell.label !== transfer.transfer_status({ outcome: "ALREADY_ON_TARGET" }).label,
	"two different reasons do not read the same"
);
check(
	transfer.transfer_status({ outcome: "SOMETHING_NEW", message: "متن سرور" }).label === "متن سرور",
	"an unknown code falls back to the server's own sentence rather than a blank cell"
);

console.log("\nevery reason the server can send has a cell to land in");
const reasons = [
	"ACKNOWLEDGEMENT_MISSING",
	"ALREADY_ON_TARGET",
	"ITEM_NOT_ON_TARGET",
	"NO_ROOM",
	"ROW_MISMATCH",
	"TARGET_ROW_NOT_ELIGIBLE",
];

for (const reason of reasons) {
	const entry = transfer.TRANSFER_REASON[reason];

	check(Boolean(entry && entry.label && entry.tone), `${reason} has a label and a tone`);
}

console.log("\nwhich target rows a serial may be aimed at");
const options = transfer.transfer_row_options(live_case().target_rows, "PR89889", "t-2");

check(options.length === 2, "the two eligible rows of that item, and not the row of another item");
check(
	options.every((row) => row.item_code === "PR89889" && row.serial_required),
	"a row of another item, or one that may not take serials, is never offered"
);
check(
	transfer.transfer_row_options(
		[{ row_key: "t-4", item_code: "PR89889", index: 4, room: 6, serial_required: false }],
		"PR89889",
		""
	).length === 0,
	"an item whose only row is unmarked offers nothing"
);
check(
	transfer.transfer_row_options(
		[{ row_key: "t-5", item_code: "PR89889", index: 5, room: 0, serial_required: true }],
		"PR89889",
		""
	).length === 0,
	"a full row is not offered"
);
check(
	transfer.transfer_row_options(
		[{ row_key: "t-5", item_code: "PR89889", index: 5, room: 0, serial_required: true }],
		"PR89889",
		"t-5"
	).length === 1,
	"but the row a serial already sits on stays in the list, so the choice does not vanish"
);

console.log("\na stale selection is not a row, and the window does not invent one");
check(
	transfer.transfer_rows(live_case({ stale: [{ serial: "GHOST", message: "پیدا نشد" }] })).length === 18,
	"eighteen rows for eighteen source serials, with the stale entry reaching no table row"
);

console.log("\nhostile text in the data cannot become markup");
const hostile = live_case({
	serials: [
		{
			serial: "<img src=x onerror=alert(1)>",
			item_code: "<script>bad()</script>",
			order: 0,
			acknowledged: false,
			answerable: false,
			message: "<b>bold</b>",
			outcome: "NO_ROOM",
			target_row: null,
			target_index: null,
		},
		{
			serial: 'W-2"><script>bad()</script>',
			item_code: "PR89889",
			order: 1,
			acknowledged: false,
			answerable: false,
			message: "",
			outcome: "Moved",
			target_row: "t-2",
			target_index: 2,
		},
	],
});

const drawn = transfer.transfer_table({
	rows: transfer.transfer_rows(hostile),
	preview: { target_rows: hostile.target_rows },
	unticked: new Set(),
	acknowledged: new Set(),
});

check(!/<img|<script/i.test(drawn), "no tag from a serial, an item or a message survives into markup");
check(drawn.includes("&lt;img"), "the serial is written as text");
// Not `onerror=` on its own: escaped text still spells the word, and a value that reads
// `&lt;img onerror=x&gt;` is inert. What must not exist is an event handler *inside a tag* — an
// `onerror` with an unclosed `<` before it — which is what appears the moment the escaping is
// removed. Measured both ways.
check(
	!/<[^>]*onerror/i.test(drawn),
	"and no value can become an event handler inside a tag"
);
check(!/data-serial="W-2">/.test(drawn), "nor can a quote in a serial close the attribute it sits in");

console.log("\nthe window implements no rule of its own");
check(!/\.capacity\s*-|-\s*row\.held/.test(source), "it does not work out how much room a row has");
check(!/is_eligible|serial_required\s*&&/.test(source), "it does not decide which rows may take serials");
check(!/normalized/i.test(source), "it never names a serial identity — the server derives it");
check(!/ignore_permissions|ignore_links/.test(source), "and it bypasses nothing");
check(/answerable/.test(source), "it reads the server's flag for `an answer would change this`");
check(
	!/reason ===|outcome === "ACKNOWLEDGEMENT_MISSING"/.test(source),
	"rather than branching on which reason code that flag belongs to"
);

console.log("\nthe button appears only where a transfer is possible");
const hook = source.slice(source.indexOf('frappe.ui.form.on("Sales Invoice"'));
const guard = hook.slice(0, hook.indexOf("add_custom_button"));

check(/frm\.doc\.amended_from/.test(guard), "the button is drawn only for an amendment");
check(/docstatus === 2/.test(guard), "and not on a cancelled document");
check(/may_transfer\(\)/.test(guard), "and only for a user who may move serials");
check(/reaches_company\(/.test(guard), "and only where the user's company permissions reach");
check(!/add_custom_button\([^)]*__\("سریال"\)/.test(hook), "there is no dropdown on the invoice toolbar");

console.log("\nthe endpoint calls match the endpoints");
check(/transfer_call\("preview_serial_transfer"/.test(source), "the preview is called by its own name");
check(/transfer_call\("apply_serial_transfer"/.test(source), "and so is the apply");
check(/type: "POST"/.test(source), "both are sent as POST, which is what the endpoints declare");
check(
	!/frm\.save|frappe\.db\.set_value/.test(source),
	"and the window writes through the endpoint rather than around it"
);

console.log(`\n${failures ? `FAIL (${failures} checks)` : "PASS"}`);

process.exit(failures ? 1 : 0);
