// The scan station's own rules, run rather than read.
//
// The station is browser code and its behaviour is mostly interaction: focus, a spinner that
// clears, a sound. Three of its decisions are pure functions over what the server answered, and
// those are the ones worth a test that executes them — the rule for a completion chime (which is
// the whole of "once per completion, never on open"), the rule that keeps a scanned value
// selectable, and the per-user sound preference.
//
// It is a script and not part of the Python suite because it needs a JavaScript runtime, and the
// suite is documented as running anywhere. Node is already in the lab image, so this runs there:
//
//     node deployment/lab/verify_scan_logic.js
//
// It loads the real `public/js/sales_invoice_serial_button.js` with a stand-in for `frappe` that
// implements only what loading it touches. Nothing here re-implements the station; a change to
// the file is a change to what this tests.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(
	__dirname,
	"..",
	"..",
	"smartprocess_logistics",
	"public",
	"js",
	"sales_invoice_serial_button.js"
);

// --- the stand-in ------------------------------------------------------------------------

const handlers = {};
let supplied = null;

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
				handlers[doctype] = table;
			},
		},
	},
	user: { has_role: () => true },
	session: { user: "scan@example.test" },
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
	msgprint: () => {},
	show_alert: (payload) => {
		supplied = payload;
	},
	call: () => Promise.resolve({ message: {} }),
};

globalThis.frappe = frappe;
globalThis.window = globalThis;
globalThis.localStorage = store;
// Frappe's `__` interpolates `{0}`-style arguments; the station's warning line is built with it,
// so the double has to do the same substitution for the wording to be asserted as it will read.
globalThis.__ = (text, args) => {
	if (args === undefined) {
		return text;
	}

	const list = Array.isArray(args) ? args : [args];

	return text.replace(/\{(\d+)\}/g, (whole, index) =>
		Object.prototype.hasOwnProperty.call(list, Number(index)) ? String(list[Number(index)]) : whole
	);
};

vm.runInThisContext(fs.readFileSync(SOURCE, "utf8"), { filename: SOURCE });

const station = globalThis.smartprocess_logistics;

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

// Two shapes read off the station's own source, for the guards below to assert against.
//
// `box_emptied_before` is the order that matters in the scan path: the box must be empty *before*
// the label is filed, because leaving it fires its own `blur` and the blur would send the same text
// a second time — and, since the station now files a burst that no scanner ended, because a box
// that still held the previous label would be filed as part of the next one.
// `state_pending_untouched` is the whole of "a redraw cannot lose a scan": the waiting list is
// written by the scan path and by nothing that redraws the window.
const box_emptied_before = (text, filing) =>
	text.indexOf('input.val("")') > 0 && text.indexOf('input.val("")') < text.indexOf(filing);

const state_pending_untouched = (text) => !/state\.pending\s*=/.test(text);

console.log(`station logic from ${path.relative(process.cwd(), SOURCE)}\n`);

console.log("the file loads and registers its hook");
check(typeof station.open_serial_dialog === "function", "the scan window is defined");
check(typeof station.open_details_dialog === "function", "the details window is defined");
check(Boolean(handlers["Sales Invoice"]), "the Sales Invoice form hook is registered");
check(
	station.METHOD.add_row_serial === "POST",
	"one serial is written with POST, the verb the endpoint declares"
);

// The one state a row is in, from the numbers the window has: what the invoice holds, what the
// operator has in hand, what the row needs and what it may hold. Everything a card is painted and
// everything a chime is decided from comes through here, so these checks are the whole vocabulary.
console.log("\none state per row: gray, green, blue, red");
const state_of = (required, capacity, persisted, pending) =>
	station.row_state({ required: required, capacity: capacity }, { persisted: persisted, pending: pending });

check(state_of(5, 5, 0, 0) === "INCOMPLETE", "a row holding nothing is incomplete");
check(state_of(5, 5, 4, 0) === "INCOMPLETE", "so is one label short of what it needs");
check(state_of(5, 5, 5, 0) === "SAVED", "a row whose labels are all written is saved");
check(state_of(5, 5, 0, 5) === "STAGED", "a row holding exactly enough, all unsaved, is staged");
check(state_of(5, 5, 4, 1) === "STAGED", "and so is one that is part written and part in hand");
check(state_of(5, 5, 6, 0) === "CONFLICT", "more than the invoice allows is a conflict");
check(state_of(5, 5, 5, 1) === "CONFLICT", "including when the excess is only in hand");
check(state_of(5, 5, 5, 0) !== state_of(5, 5, 4, 1), "green and blue are different states, not a shade");
check(state_of(0, 0, 0, 0) === "SAVED", "a row that invoices nothing is saved with nothing");

console.log("\nthe chime rule: once per save, and only on a write");
const saved_row = (persisted, required) => state_of(required, required, persisted, 0);

check(station.should_chime(true, false, saved_row(20, 20)), "a row the server has just saved rings");
check(
	!station.should_chime(true, true, saved_row(20, 20)),
	"a second answer about the same saved row is silent"
);
check(
	!station.should_chime(false, false, saved_row(20, 20)),
	"a load never rings, however saved the row is"
);
check(!station.should_chime(true, false, saved_row(19, 20)), "a row still short does not ring");
check(
	!station.should_chime(true, false, state_of(20, 20, 19, 1)),
	"and neither does a row whose last label is still in hand: that is green, not saved"
);
check(!station.should_chime(true, false, "CONFLICT"), "an over-filled row does not ring");

console.log("\ntwenty labels, one completion");
const row = (registered, required) => ({
	required: required,
	capacity: required,
});
let rings = 0;
// What the station remembers about the column: whether the server's *previous* answer called it
// saved. The loop below feeds answers the way a scan session does, one write at a time.
const remembered = { was: undefined };

for (let scan = 1; scan <= 20; scan += 1) {
	if (station.should_chime(true, remembered.was, state_of(20, 20, scan, 0))) {
		rings += 1;
	}

	remembered.was = state_of(20, 20, scan, 0) === "SAVED";
}

check(rings === 1, "exactly one chime across twenty scans", `rang ${rings} times`);

console.log("\na column emptied and filled again rings again");
check(
	!station.should_chime(true, remembered.was, state_of(20, 20, 19, 0)),
	"deleting a label is not a completion"
);
check(
	station.should_chime(true, state_of(20, 20, 19, 0) === "SAVED", state_of(20, 20, 20, 0)),
	"the label that completes the column again rings"
);

console.log("\nthe sound preference is per user and remembered");
check(station.sound_enabled("a@example.test"), "it is on by default");
station.set_sound_enabled(false, "a@example.test");
check(!station.sound_enabled("a@example.test"), "turning it off is remembered");
check(station.sound_enabled("b@example.test"), "and it is that user's choice, not the terminal's");
station.set_sound_enabled(true, "a@example.test");
check(station.sound_enabled("a@example.test"), "turning it back on is remembered");

console.log("\na scanned value cannot break the selector that finds its column");
check(station.selector("f1pqktg7fb") === "f1pqktg7fb", "an ordinary row key is unchanged");

// The invariant is precise: inside a double-quoted attribute value no quote may be left
// unescaped, because that is the character that would end the value. Asserting that the result
// does not *contain* a quote would be wrong — `\"` contains one, and is exactly the fix.
const hostile = 'a"] .sp-scan-cells';
const escaped = station.selector(hostile);
check(
	!/(^|[^\\])"/.test(escaped),
	"no unescaped quote survives in the selector value",
	escaped
);

// The preferred path, when the browser has it, is the browser's own escaping.
globalThis.CSS = { escape: (value) => value.replace(/[^a-zA-Z0-9_-]/g, (character) => `\\${character}`) };
check(station.selector("a b") === "a\\ b", "CSS.escape is used when the browser provides it");
delete globalThis.CSS;
check(station.selector("a b") === "a b", "and the fallback leaves an ordinary value alone");

console.log("\nscanned text is escaped before it is drawn");
check(
	station.escape('<img src=x onerror="1">') === "&lt;img src=x onerror=&quot;1&quot;&gt;",
	"a serial is drawn as text, not as markup"
);

console.log("\nthe scanner's suffix: Enter always, Tab only when there is something to send");
check(station.scanner_suffix("Enter", "شا8326980") === "submit", "Enter sends the label");
check(station.scanner_suffix("Tab", "شا8326980") === "submit", "Tab sends the label too");
check(station.scanner_suffix("Tab", "") === "navigate", "an empty box's Tab is the operator moving on");
check(station.scanner_suffix("Tab", "   ") === "navigate", "and whitespace is an empty box");
check(station.scanner_suffix("a", "شا") === "ignore", "ordinary typing is left alone");
check(station.scanner_suffix("F5", "شا") === "ignore", "so is every other key");

console.log("\nthe next-column command is the whole scanned value, exactly");
check(station.is_next_row_command("**", "**"), "the configured command is the command");
check(station.is_next_row_command("  **  ", "**"), "and it is compared without the whitespace around it");
check(!station.is_next_row_command("**1", "**"), "a serial that starts with it is a serial");
check(!station.is_next_row_command("1**", "**"), "so is one that ends with it");
check(!station.is_next_row_command("AB**CD", "**"), "so is one that contains it");
check(!station.is_next_row_command("***", "**"), "and so is one character more of the same");
check(!station.is_next_row_command("**", "**1"), "the longer configured command does not match the shorter scan");
check(station.is_next_row_command("§§", "§§"), "a site that chose its own command gets its own command");
check(!station.is_next_row_command("§§", "**"), "and the shipped one no longer matches there");
check(!station.is_next_row_command("**", ""), "a site with no command navigates on nothing");
check(!station.is_next_row_command("**", null), "and neither does a command that never arrived");
check(!station.is_next_row_command("", "**"), "an empty box is not a command");
check(!station.is_next_row_command("x", "X"), "the match is exact in case too, like a serial is");

console.log("\nthe next column is the next one on screen, and the last one wraps");
const order = ["row-9", "row-3", "row-1"];
// Deliberately not sorted: the order given is the order used. A station that sorted by row key
// would move the cursor somewhere the operator is not looking.
check(station.next_row_key(order, "row-9") === "row-3", "the next key is the next one in the order given");
check(station.next_row_key(order, "row-3") === "row-1", "and the one after that");
check(station.next_row_key(order, "row-1") === "row-9", "past the last one, the first one");
check(station.next_row_key(["only"], "only") === "only", "a single column goes back to itself");
check(station.next_row_key(order, "row-not-here") === "row-9", "a key that is not there answers with the first");
check(station.next_row_key([], "row-1") === null, "and no columns at all answers with nothing");
check(station.next_row_key(null, "row-1") === null, "including when the order never arrived");
check(
	station.next_row_key(["row-2", "row-1"], "row-2") === "row-1",
	"and the order it is given is the only order it knows"
);
check(
	station.next_row_key(order, "row-1") === "row-9" &&
		station.next_row_key(["row-1", "row-9"], "row-1") === "row-9",
	"the column after the last is the first on screen, whatever the keys are called"
);

console.log("\nwhat a column holds, counted over what was scanned and not over what was saved");
const counted = (registered, waiting, capacity, required) =>
	station.pending_counts(
		{ registered: registered, capacity: capacity, required: required === undefined ? capacity : required },
		waiting
	);

check(
	JSON.stringify(counted(3, 0, 5, 5)) ===
		JSON.stringify({ persisted: 3, pending: 0, scanned: 3, allowed: 5, excess: 0, remaining: 2 }),
	"a column with room has no excess and says how many more it needs"
);
check(
	JSON.stringify(counted(5, 0, 5, 5)) ===
		JSON.stringify({ persisted: 5, pending: 0, scanned: 5, allowed: 5, excess: 0, remaining: 0 }),
	"a column filled exactly is complete in hand, with no excess"
);
check(
	JSON.stringify(counted(5, 2, 5, 5)) ===
		JSON.stringify({ persisted: 5, pending: 2, scanned: 7, allowed: 5, excess: 2, remaining: 0 }),
	"five scanned and two waiting on a five-unit row is two excess, and it is not short"
);
check(
	JSON.stringify(counted(0, 7, 5, 5)) ===
		JSON.stringify({ persisted: 0, pending: 7, scanned: 7, allowed: 5, excess: 2, remaining: 0 }),
	"seven scans and nothing saved is still two over the quantity"
);
check(
	JSON.stringify(counted(6, 0, 5, 5)) ===
		JSON.stringify({ persisted: 6, pending: 0, scanned: 6, allowed: 5, excess: 1, remaining: 0 }),
	"a row already over its quantity in the database carries that excess, not a negative one"
);
check(
	JSON.stringify(counted(1, 1, 5, 0)) ===
		JSON.stringify({ persisted: 1, pending: 1, scanned: 2, allowed: 5, excess: 0, remaining: 0 }),
	"an optional column has a capacity and never a shortage"
);

console.log("\nthe sentence that blocks a save");
const blocked_one = station.save_blocked_message([{ model: "DS-2CD2166G2-ISU", excess: 2 }]);

check(
	blocked_one.indexOf("DS-2CD2166G2-ISU") > 0,
	"it names the item the operator has to correct"
);
check(blocked_one.indexOf("ذخیره انجام نشد") === 0, "it says the save did not happen, because it did not");
check(/۲|2/.test(blocked_one), "and it carries the excess count", blocked_one);
check(
	station
		.save_blocked_message([
			{ model: "A", excess: 1 },
			{ model: "B", excess: 3 },
		])
		.split("<br>").length === 3,
	"two over-full rows are two lines, one each"
);
check(station.save_blocked_message([]).indexOf("<br>") > 0, "and it holds together with no rows at all");

console.log("\nwhether the cursor may be moved for the operator");
const facts = (overrides = {}) =>
	Object.assign(
		{
			dialog_open: true,
			column_exists: true,
			input_disabled: false,
			focus_on_body: true,
			focus_in_this_column: false,
		},
		overrides
	);
check(station.should_refocus(facts()), "a cursor left on the document is taken back");
check(
	station.should_refocus(facts({ focus_in_this_column: true, focus_on_body: false })),
	"a cursor already in this column is left where it is"
);
check(
	!station.should_refocus(facts({ focus_on_body: false })),
	"a cursor in another column is not stolen"
);
check(!station.should_refocus(facts({ dialog_open: false })), "a closed window is not focused into");
check(!station.should_refocus(facts({ column_exists: false })), "a column that is gone is not focused into");
check(
	!station.should_refocus(facts({ input_disabled: true })),
	"a disabled field is never focused — the browser would ignore it anyway"
);
check(
	!station.should_refocus(facts({ focus_on_body: false, focus_in_this_column: false })),
	"a confirmation dialog, a delete button or any other control keeps its focus"
);

(async () => {
	console.log("\nwhich refusals mean 'already registered', and which mean something else");
const refusal = (parts) => Object.assign({}, parts);
// The fixtures are copied from live refusals, not composed from the module's names: the code that
// means "already registered" is ALREADY_REGISTERED, and the one for a full row is EXCESS_QUANTITY.
// A fixture written from the other constants in `core/validation.py` would have asserted codes the
// server never sends.
check(
	station.classify_failure(
		refusal({ message: "این سریال از قبل روی همین فاکتور ثبت شده است. [ALREADY_REGISTERED]" })
	) === "duplicate",
	"the refusal a real server sends for a label it already holds is a duplicate"
);
check(
	station.classify_failure(
		refusal({ _server_messages: "a message naming [ALREADY_REGISTERED]" })
	) === "duplicate",
	"and the same code arriving inside the server's message list is too"
);
check(
	station.classify_failure(
		refusal({
			message: "تعداد سریال بیش از تعداد فاکتور است؛ ۱ سریال اضافه است. [EXCESS_QUANTITY]",
		})
	) === "excess",
	"the refusal a real server sends for a row at its quantity is an excess"
);
check(
	station.classify_failure(
		refusal({ message: "این سریال در همین ردیف دو بار آمده است. [DUPLICATE_IN_ROW]" })
	) === "other",
	"a label repeated inside one submission is neither of the two"
);
check(
	station.classify_failure(refusal({ message: "شما اجازه دسترسی ... [AUTH_NOT_PERMITTED]" })) ===
		"other",
	"a permission failure is neither of the two"
);
check(
	station.classify_failure(new TypeError("Failed to fetch")) === "other",
	"a lost connection is neither of the two"
);
check(station.classify_failure(null) === "other", "and neither is a failure with nothing in it");
check(
	station
		.codes_in(refusal({ message: "two codes [EXCESS_QUANTITY] and [ALREADY_REGISTERED]" }))
		.join(",") === "EXCESS_QUANTITY,ALREADY_REGISTERED",
	"both codes are found when a refusal carries more than one"
);

console.log("\nthe wording the operator reads");
check(
	station.duplicate_warning("شا8326980") === "این سریال قبلاً ثبت شده است: شا8326980",
	"the duplicate warning names the serial exactly as it was scanned",
	station.duplicate_warning("شا8326980")
);
check(
	station.excess_warning() ===
		"تعداد سریال‌های این کالا تکمیل شده است. امکان ثبت سریال بیشتر وجود ندارد.",
	"and the excess warning is the sentence the business rule asked for, word for word",
	station.excess_warning()
);

console.log("\ntwenty labels from a scanner that types faster than the server answers");
	const queues = {};
	const finished = [];
	const send = (delay, label) =>
		new Promise((resolve) => {
			setTimeout(() => {
				finished.push(label);
				resolve(label);
			}, delay);
		});

	for (let scan = 1; scan <= 20; scan += 1) {
		// The first answer is slow on purpose: the nineteen behind it arrive while it is in flight,
		// which is exactly the case that used to drop them.
		station.enqueue(queues, "row-1", () => send(scan === 1 ? 120 : 10, `label-${scan}`));
	}

	await new Promise((resolve) => setTimeout(resolve, 600));

	check(finished.length === 20, "all twenty are sent, none dropped", `sent ${finished.length}`);
	check(
		finished.join(",") === Array.from({ length: 20 }, (_, index) => `label-${index + 1}`).join(","),
		"and in the order they were scanned"
	);

	const failure = {};
	const after = [];
	station
		.enqueue(failure, "row-2", () => Promise.reject(new Error("refused by the server")))
		.catch(() => {});
	station.enqueue(failure, "row-2", () => {
		after.push("label after a refusal");

		return Promise.resolve();
	});

	await new Promise((resolve) => setTimeout(resolve, 60));

	check(after.length === 1, "a refused label does not stall the label behind it");
	check(
		station.enqueue(failure, "row-3", () => Promise.resolve("other column")) instanceof Promise,
		"and another column is not waiting behind it at all"
	);

	console.log("\na duplicate in the middle of a session: only that label is refused");
	const labels = Array.from({ length: 20 }, (_, index) => `label-${index + 1}`);
	const duplicates = new Set(["label-3", "label-7", "label-19"]);
	const sessionQueue = {};
	const attempted = [];
	const registered = [];
	const refused = [];
	const classifiedAsDuplicate = [];

	const server = (label) =>
		new Promise((resolve, reject) => {
			// Slower than the scanner types, so every label after the first arrives while it is in
			// flight — the case that used to drop them.
			setTimeout(() => {
				if (duplicates.has(label)) {
					// The exact refusal shape a live server produces, code included.
					reject(new Error("این سریال از قبل روی همین فاکتور ثبت شده است. [ALREADY_REGISTERED]"));

					return;
				}

				resolve({ row: { registered: registered.length + 1 } });
			}, 12);
		});

	labels.forEach((label) =>
		station.enqueue(sessionQueue, "row-1", () =>
			server(label)
				.then(() => {
					attempted.push(label);
					registered.push(label);
				})
				.catch((error) => {
					attempted.push(label);
					refused.push(label);
					classifiedAsDuplicate.push(station.classify_failure(error) === "duplicate");
				})
		)
	);

	await new Promise((resolve) => setTimeout(resolve, 800));

	check(attempted.length === 20, "every scan in the session is sent, none dropped", `sent ${attempted.length}`);
	check(attempted.join(",") === labels.join(","), "and in the order they were scanned");
	check(registered.length === 17, "seventeen register", `${registered.length} registered`);
	check(
		refused.join(",") === "label-3,label-7,label-19",
		"the three duplicates are exactly the three refused",
		refused.join(",")
	);
	check(
		classifiedAsDuplicate.every(Boolean),
		"and each one is recognised as a duplicate rather than as an unexpected failure"
	);
	check(
		["label-4", "label-8", "label-20"].every((label) => registered.includes(label)),
		"the labels behind a duplicate still register"
	);
	check(
		refused.includes("label-19") && registered.includes("label-20"),
		"including the one immediately after the last duplicate"
	);

	// The two guards below read the source, which is the only way to assert them without a DOM.
	// They exist because the focus bug being fixed was exactly this: a field disabled mid-write.
	const source = fs.readFileSync(SOURCE, "utf8");

	console.log("\nsource guards for the regression this fix removes");
	check(
		!/sp-scan-input[^;]*\.prop\(\s*["']disabled["']\s*,\s*true/.test(source) &&
			!/\.prop\(\s*["']disabled["']\s*,\s*true\s*\)[^;]*sp-scan-input/.test(source),
		"the scan input is never disabled — the browser blurs a field it cannot hold"
	);
	check(source.includes("input_of("), "the input is re-queried rather than reused after a redraw");
	check(
		(source.match(/frappe\.dom\.freeze\(/g) || []).length === 0,
		"the global freeze overlay is still never used, so it cannot get stuck again"
	);
	check(
		(source.match(/draw_row\(/g) || []).length === 4,
		"a row is drawn on the first load, on a status load, from the waiting list, and after a write — nowhere else",
		`${(source.match(/draw_row\(/g) || []).length} call sites`
	);
	check(
		/const redraw_column = [\s\S]{0,200}?draw_row\(row, false\)/.test(source),
		"the redraw of a column that only changed on screen goes through one function",
		"so a waiting scan is drawn from the same rows the server last confirmed"
	);
	check(
		/if \(answer && answer\.row\) \{\s*draw_row\(/.test(source),
		"and the write-path redraw is guarded by the server having returned a row, which a refusal never does",
		"so a refused label cannot move a counter, change completion, or ring the chime"
	);
	check(
		source.indexOf('classify_failure(error) === "other"') < source.indexOf("frappe.msgprint"),
		"a refusal is classified before any dialog is shown, so the two it answers never open one"
	);

	// The refusal path of a save, read off the file: it records what happened and returns, and it
	// draws nothing. A refused label therefore cannot move a counter, change a completion or earn a
	// chime — the redraw on that path is guarded by the server having answered with a row, and a
	// refusal is the one answer that never carries one.
	const persist = source.slice(
		source.indexOf("const persist_row = function"),
		source.indexOf("const save_pending = function")
	);

	check(persist.length > 0, "the save path is where this expects to find it");
	check(
		!/\.catch\([\s\S]*?draw_row\(/.test(persist),
		"a refused label is recorded and nothing is redrawn from it"
	);
	check(
		/add_row_serials/.test(persist),
		"a column is written in one request, through the batch endpoint"
	);
	check(
		/serials: JSON\.stringify\(waiting\.map/.test(persist),
		"carrying every label that was waiting, in the order it was scanned"
	);
	check(
		!/step\(index \+ 1\)/.test(persist),
		"and not one request per label, which is what this replaced"
	);
	check(
		/waiting\.map\(\(entry\) => \(\{[\s\S]{0,120}?ok: false/.test(persist),
		"a refused batch is reported against every label it held — the column was refused whole"
	);
	check(source.includes("row.model || row.item_code"), "a column is titled by the model, with the item code behind it");
	check(
		source.includes('placeholder="${__("سریال را وارد کنید")}"'),
		"and the input says exactly what it should"
	);
	check(
		source.includes("entry.is_excess") && source.includes("row.excess"),
		"the excess serials are drawn from the server's own marking, one cell at a time"
	);

	console.log("\na scan is kept, and nothing is written for it");
	// Staging moved: every way a serial arrives — a scan, a typed label, a pasted token, a
	// serial a previewed file held — goes through `stage`, which is where the waiting list is
	// written, and it is the only place it is written.
	const adding = source.slice(
		source.indexOf("const stage = function"),
		source.indexOf("const drop_pending = function")
	);

	check(adding.length > 0, "the scan path is where this expects to find it");
	check(!/[\.]call\(/.test(adding), "a scan calls no endpoint — not one");
	check(!/enqueue\(/.test(adding), "it joins no queue, because there is nothing to wait for");
	check(!/with_busy\(/.test(adding), "and it marks no column busy, because nothing is in flight");
	check(
		/state\.pending\[row_key\]/.test(adding) && /\.push\(\{/.test(adding),
		"the label is kept in the column's waiting list"
	);
	check(
		/staged_identity\(identity\)/.test(adding) && /invoice_identity\(identity\)/.test(adding),
		"a duplicate is judged against every column of this invoice and against what it already holds"
	);
	check(
		/serial_refusal\(/.test(adding),
		"and a serial is judged by the shared refusal rule before it is compared with anything"
	);

	// The property that made a pasted list affordable: staging itself writes no DOM, so two
	// hundred serials are two hundred of these and then one redraw of each column that changed.
	const staging = source.slice(
		source.indexOf("const stage = function"),
		source.indexOf("const staged_identity = function")
	);

	check(
		!/redraw_column\(|flash\(|\.html\(/.test(staging),
		"and staging touches no DOM: a pasted list of two hundred is one redraw per column, not two hundred"
	);
	// `submit` is the one place a label leaves the box — whether the scanner ended it or the
	// settle timer did — so it is the slice the emptying guard is asserted against.
	const taking = source.slice(
		source.indexOf("const submit = function"),
		source.indexOf("const move_to_next_column = function")
	);

	check(taking.length > 0, "the one place a label is taken out of the box is where this expects it");
	check(
		box_emptied_before(taking, "add_pending(row_key, serial)"),
		"the box is emptied before the label is filed, so the next label needs no click",
		"and the blur that leaving it fires has nothing left to send"
	);
	check(
		!/waiting\.push\(/.test(taking),
		"and the scan path itself files nothing — it hands the label to the waiting list"
	);

	console.log("\nthe save writes what was checked, and reports what it wrote");
	const saving = source.slice(source.indexOf("const save_pending = function"));

	check(
		saving.indexOf("save_blocked_message") < saving.indexOf("persist_row("),
		"every row is checked for excess before the first request is sent"
	);
	check(
		/if \(blocked\.length\) \{[\s\S]{0,600}?return;/.test(saving),
		"and a batch with an over-full row is refused whole — nothing is sent at all"
	);
	check(
		!/if \(blocked\.length\) \{[\s\S]{0,600}?state\.pending/.test(saving),
		"the refused batch keeps every scan: the block touches no waiting list"
	);
	check(
		/const failed = all\.filter\(\(outcome\) => !outcome\.ok\)/.test(saving),
		"the outcome of a save is counted, successes and failures apart"
	);
	check(
		/failed\.length[\s\S]{0,4000}?ذخیره انجام نشد/.test(saving),
		"and a save that wrote only part of the batch says so, with the counts"
	);
	check(
		/grouped\.push\(line\)/.test(saving) && /lines\.join\("<br>"\)/.test(saving),
		"with one line per refused column rather than one per label"
	);
	check(
		!/frappe\.show_alert[\s\S]{0,200}?ذخیره انجام نشد/.test(saving),
		"the failure is never announced as an alert about success"
	);
	check(
		/refresh_save_button\(\)/.test(saving) && /state\.saving = false/.test(saving),
		"the button is released when the save ends, however it ended"
	);
	check(
		state_pending_untouched(source.slice(source.indexOf("const draw = function"), source.indexOf("const load_status = function"))),
		"a redraw of the whole window does not touch what is waiting",
		"so a refresh, or a file import landing mid-scan, cannot lose a scan"
	);
	check(
		source.includes('data-action="save-pending"'),
		"the window has one labelled save action"
	);
	check(
		/[data-action="save-pending"] \.\.\.[\s\S]{0,80}?disabled|data-action="save-pending"[\s\S]{0,40}?disabled/.test(source),
		"which starts disabled, because nothing is waiting on a window that has just opened"
	);
	check(
		/const close = function[\s\S]{0,700}?pending_total\(\)[\s\S]{0,700}?پنجره بسته شود/.test(source),
		"and closing the window asks before it throws unsaved scans away"
	);

	console.log("\nthe column header: the model, then the row number, and nothing else");
	const head = source.slice(
		source.indexOf('<header class="sp-scan-head">'),
		source.indexOf("</header>")
	);
	const heading = head.slice(
		head.indexOf('<div class="sp-scan-heading">'),
		head.indexOf('<div class="sp-scan-index">')
	);

	check(
		!/<button/.test(head) && !/icon\(/.test(head),
		"no button and no icon in the header — the one destructive control is not beside the model"
	);
	check(/<span class="sp-scan-tick">/.test(heading), "the completion tick stays on the model's line");
	check(
		heading.indexOf('class="sp-scan-title"') > heading.indexOf('class="sp-scan-tick"'),
		"with the model after it, so the model is what the line is about"
	);
	check(
		!/class="sp-scan-index"/.test(heading) && /<div class="sp-scan-index">#/.test(head),
		"the row number is on its own line under the model, not beside it"
	);
	check(
		head.indexOf('class="sp-scan-index"') < head.indexOf('class="sp-scan-counts"'),
		"and the counters follow the row number, as they did"
	);
	check(/<div class="sp-scan-counts">/.test(head), "the counters are still the header's last line");

	console.log("\nclearing a column is still there, and still asks first");
	check(source.includes('data-action="clear-column"'), "the action survives being moved out of the header");
	check(
		source.indexOf('class="sp-scan-foot"') < source.indexOf('data-action="clear-column"'),
		"and it sits at the foot of the column, under the serials it removes"
	);
	check(
		/frappe\.confirm\(/.test(source.slice(source.indexOf('action === "clear-column"'))),
		"clearing a column still asks, naming the row, before it deletes anything"
	);
	check(
		source.includes('toggleClass("is-empty"'),
		"a column with nothing to clear does not offer to clear it"
	);

	console.log("\nthe one interpreter: commands, splitting and judgement");

	// One grammar, read once, for a scanned value and a pasted token alike. Everything here is
	// the rule the three input paths share, so a change to any of it is a change to all three.
	const as_command = (token, configured) => station.read_command(token, configured);

	check(as_command("**", "**").kind === "next", "the configured command is the next-column command");
	check(as_command("  **  ", "**").kind === "next", "and it is recognised with the whitespace a scanner may send");
	check(as_command("AB**CD", "**") === null, "a serial containing the command is a serial, not a command");
	check(as_command("**1", "**") === null, "and neither is a value that only starts with it");
	check(as_command("@@", "**") === null, "a site that configured another command does not navigate on this one");
	check(as_command("**", "") === null, "and a site with no command configured navigates on nothing");
	check(as_command("#3", "**").kind === "row" && as_command("#3", "**").index === 3, "`#N` is the same command as `*N`");
	check(as_command("#123", "**").index === 123, "and N has no digit limit");
	check(as_command("*0", "**").kind === "row" && as_command("*0", "**").index === 0, "`*0` asks for a row that cannot exist, rather than becoming a serial");
	check(as_command("AB#1C", "**") === null, "a serial containing `#1` is a serial");
	check(as_command("##", "##").kind === "next", "a site that configured `##` navigates on `##`");
	check(as_command("##", "**") === null, "and on `**` when that is what it configured instead");
	check(as_command("*3", "**").kind === "row" && as_command("*3", "**").index === 3, "*N names an invoice row");
	check(as_command("*3", "").kind === "row", "and does so whether or not a next-column command is configured");
	check(as_command("*3x", "**") === null, "a row command has to be the whole token");
	check(as_command("", "**") === null, "and an empty value is nothing at all");

	// The splitter the window uses on a paste: literal separators from the site, longest first.
	const TAB_CHAR = String.fromCharCode(9);
	const CR = String.fromCharCode(13);
	const LF = String.fromCharCode(10);
	const COMMA = String.fromCharCode(44);
	const PERSIAN_SEMI = String.fromCharCode(0x61b);
	const tokens_of = (text, separators) => station.split_tokens(text, separators);

	check(tokens_of("A" + COMMA + "B", [COMMA]).join(",") === "A,B", "a configured comma splits");
	check(tokens_of("A" + COMMA + COMMA + "B", [COMMA]).join(",") === "A,B", "a separator typed twice is a keystroke, not an empty serial");
	check(tokens_of("A" + LF + "B", [CR + LF, LF]).join(",") === "A,B", "CRLF and LF both end a line, longest separator first");
	check(tokens_of("A" + TAB_CHAR + "B", [TAB_CHAR]).join(",") === "A,B", "a tab separates, which is what a spreadsheet paste is made of");
	check(tokens_of("A" + PERSIAN_SEMI + "B", [PERSIAN_SEMI]).join(",") === "A,B", "so does the Persian semicolon");
	check(tokens_of("AB-12", [COMMA]).join(",") === "AB-12", "a serial with no separator in it stays one serial");
	check(tokens_of("A" + COMMA + "B", []).length === 1, "and nothing splits when the site told the window nothing");

	// The rest of the separators this site actually serves, one at a time and together. Two of them
	// are configuration a person typed — `\\s` is served as the space it stands for, and the odd
	// `\\r?\\n` entry stands for the three characters CR, `?`, LF — and a separator that is
	// interpreted literally is what keeps a `?` from becoming a quantifier.
	const SPACE = String.fromCharCode(32);
	const PERSIAN_COMMA = String.fromCharCode(0x60c);
	const CRLF = CR + LF;

	check(tokens_of("A B", [SPACE]).join(",") === "A,B", "a space splits when the site configured one");
	check(tokens_of("A" + PERSIAN_COMMA + "B", [PERSIAN_COMMA]).join(",") === "A,B", "the Persian comma splits");
	check(
		tokens_of("A" + CRLF + "B", [CRLF]).join(",") === "A,B",
		"CRLF ends a line as one separator, whichever order the site lists it in"
	);
	check(
		tokens_of("A," + COMMA + ",B", [COMMA]).join(",") === "A,B",
		"separators typed in a row are keystrokes, not empty serials"
	);
	check(
		tokens_of("A" + CRLF + "B", [CR + "?" + LF, CRLF]).join(",") === "A,B",
		"and the odd `\\r?\\n` entry the site also carries cannot swallow a real line break",
		tokens_of("A" + CRLF + "B", [CR + "?" + LF, CRLF]).join(",")
	);
	check(
		tokens_of("A" + SPACE + SPACE + "B", [SPACE]).join(",") === "A,B",
		"and a doubled space does not make an empty serial"
	);
	check(
		tokens_of("A" + TAB_CHAR + "B" + CRLF + "C", [TAB_CHAR, CRLF, LF]).join(",") === "A,B,C",
		"a tab and a line break in one text split the same way wherever they came from"
	);

	// There is no "optional column" state any more, and its absence is the rule: a row whose item
	// is not marked for serials is not served to this window at all, so it has no column, no
	// colour and no count. What the window decides about a row is one of the four states above.
	check(
		typeof station.optional_is_filled === "undefined" && typeof station.row_state === "function",
		"a row has one state function and no second opinion about being populated"
	);

	// --- what arrived, before it means anything: a table keeps its shape -------------------
	// The step between the clipboard and the intake. A range copied out of a spreadsheet is a
	// table — rows on lines, columns between tabs — and reading it across instead of down is what
	// put a whole multi-column paste into the row the *last* command named: the first line's
	// commands are all consumed before a single serial arrives. The fixture below is the operator's
	// own `01.xlsx`, which the Python suite reads cell for cell out of the file.
	const decode = (text) => station.decode_intake_values(text);
	const rows_of = (lines) => lines.join(CR + LF);
	// The synthetic workbook in the repository (`tests/fixtures/synthetic-workbook.xlsx`),
	// column by column as the Python reader produces it. The same values are in the browser
	// harness and in `TestTheSyntheticWorkbook`, so the two languages are pinned to one file.
	const COLUMN_A = [
		"99999999",
	];
	const COLUMN_B = [
		"*1",
		"SYN-B-01",
		"SYN-B-02",
		"SYN-B-03",
		"SYN-B-04",
		"SYN-B-05",
	];
	const COLUMN_C = [
		"*2",
		"SYN-C-01",
		"SYN-C-02",
		"SYN-C-03",
		"SYN-C-04",
		"SYN-C-05",
		"SYN-C-06",
		"SYN-C-07",
		"SYN-C-08",
		"SYN-C-09",
		"SYN-C-10",
	];
	const COLUMN_D = [
		"*3",
		"SYN-D-01",
		"SYN-D-02",
		"SYN-D-03",
		"SYN-D-04",
		"10000000005",
		"SYN-D-06",
		"SYN-D-07",
		"1.23E+11",
		"SYN-D-09",
		"SYN-D-10",
		"SYN-D-11",
		"10000000012",
		"SYN-D-13",
		"SYN-D-14",
		"SYN-D-15",
		"SYN-D-16",
		"SYN-D-17",
		"10000000018",
		"SYN-D-19",
		"SYN-D-20",
	];
	const REAL_COLUMNS = [COLUMN_A, COLUMN_B, COLUMN_C, COLUMN_D];

	// What the server reads out of the workbook: every nonempty cell, column by column, top to
	// bottom — the order `core.spreadsheet.column_major_texts` produces. The stray cell of the
	// first column is read first because its column is first, wherever in that column it sits.
	const READ_COLUMN_MAJOR = [].concat(...REAL_COLUMNS);

	// What the clipboard carries when the same range is copied: row by row, cells between tabs,
	// with a cell that holds nothing still occupying its column. The stray cell sits in the
	// sixteenth row of the range, as it does in the file.
	const STRAY_ROW = 16;
	const REAL_CLIPBOARD = rows_of(
		COLUMN_D.map((_cell, index) =>
			[
				index + 1 === STRAY_ROW ? COLUMN_A[0] : "",
				COLUMN_B[index] || "",
				COLUMN_C[index] || "",
				COLUMN_D[index] || "",
			].join(TAB_CHAR)
		)
	);

	check(
		decode("A" + TAB_CHAR + "B").length === 1,
		"one line holding a tab is a list whose separator is a tab, not a table"
	);
	check(decode("A" + LF + "B").length === 1, "and newlines alone are never a table");
	check(
		decode("A" + TAB_CHAR + "B" + LF + "C" + TAB_CHAR + "D").join(",") === "A,C,B,D",
		"two tabbed lines are a table, read column by column, top to bottom",
		decode("A" + TAB_CHAR + "B" + LF + "C" + TAB_CHAR + "D").join(",")
	);
	check(
		decode(TAB_CHAR + "*1" + LF + TAB_CHAR + "A1").join(",") === "*1,A1",
		"a leading empty column shifts nothing"
	);
	check(
		decode("*1" + TAB_CHAR + LF + "A1" + TAB_CHAR + "B1" + TAB_CHAR).join(",") === "*1,A1,B1",
		"blank cells and trailing tabs are skipped rather than staged"
	);
	check(
		decode("*1" + TAB_CHAR + "*2" + LF + "A" + TAB_CHAR + "B" + LF + "C").join(",") ===
			"*1,A,C,*2,B",
		"columns of different lengths are read to the end of each one",
		decode("*1" + TAB_CHAR + "*2" + LF + "A" + TAB_CHAR + "B" + LF + "C").join(",")
	);
	check(
		decode("*1" + TAB_CHAR + "*2" + LF + "A" + TAB_CHAR + "B").join(",") === "*1,A,*2,B",
		"a command stays in the column it was written in — the decoder interprets nothing"
	);
	check(
		decode("*1" + TAB_CHAR + "X" + LF + "1.23E+11" + TAB_CHAR + "Y").indexOf("1.23E+11") === 1,
		"text that looks like a number is left exactly as it was pasted"
	);
	check(decode(TAB_CHAR + LF + TAB_CHAR + TAB_CHAR).length === 0, "an all-blank table is no values at all");
	check(
		decode(rows_of(["*1" + TAB_CHAR + "A", "*2" + TAB_CHAR + "B"])).join(",") ===
			decode(["*1" + TAB_CHAR + "A", "*2" + TAB_CHAR + "B"].join(LF)).join(","),
		"a clipboard that ended its lines with CRLF and one that ended them with LF are the same table"
	);

	// The equivalence the whole change is for: the operator's workbook and the clipboard copy of
	// the same range are one sequence of values — the same commands in the same places and the same
	// serials under them — so the one intake assigns them to the same invoice rows.
	const DECODED_CLIPBOARD = decode(REAL_CLIPBOARD);

	check(REAL_CLIPBOARD.indexOf(TAB_CHAR) >= 0, "the operator's clipboard is a table");
	check(
		DECODED_CLIPBOARD.join("|") === READ_COLUMN_MAJOR.join("|"),
		"the clipboard decodes to exactly what the workbook read: same values, same order",
		`clipboard ${DECODED_CLIPBOARD.length} values, workbook ${READ_COLUMN_MAJOR.length}`
	);
	check(
		DECODED_CLIPBOARD[0] === "99999999",
		"including the one cell of the column nobody wrote a command for, which is read first"
	);
	check(
		DECODED_CLIPBOARD.filter((value) => value === "*1").length === 1 &&
			DECODED_CLIPBOARD.indexOf("*1") === 1 &&
			DECODED_CLIPBOARD.indexOf("*2") === 1 + COLUMN_B.length &&
			DECODED_CLIPBOARD.indexOf("*3") === 1 + COLUMN_B.length + COLUMN_C.length,
		"and each command sits at the head of its own column, not in a block at the front"
	);
	check(
		DECODED_CLIPBOARD.slice(2, 7).join(",") === COLUMN_B.slice(1).join(",") &&
			DECODED_CLIPBOARD.slice(8, 18).join(",") === COLUMN_C.slice(1).join(",") &&
			DECODED_CLIPBOARD.slice(19, 39).join(",") === COLUMN_D.slice(1).join(","),
		"with five serials under `*1`, ten under `*2` and twenty under `*3`",
		`${COLUMN_B.length - 1}/${COLUMN_C.length - 1}/${COLUMN_D.length - 1}`
	);
	check(
		DECODED_CLIPBOARD.length === 39,
		"the whole range is 39 values: one stray cell, three commands and thirty-five serials",
		`${DECODED_CLIPBOARD.length}`
	);
	// The old reading, kept here so the regression is stated rather than remembered: splitting the
	// clipboard as one flat list puts the three commands side by side and every serial after them.
	check(
		tokens_of(REAL_CLIPBOARD, [TAB_CHAR, CR + LF, LF]).slice(0, 3).join(",") === "*1,*2,*3",
		"the flat reading this replaced would have put all three commands before every serial"
	);
	check(
		DECODED_CLIPBOARD.every((value) => value && value.indexOf(TAB_CHAR) < 0 && value.indexOf(LF) < 0),
		"no decoded value is empty or carries a cell or line boundary, so splitting it again cannot reopen the table"
	);

	// The table the site's own delimiters still take apart: a cell is a text like any other, and
	// two serials written in one cell are two serials in the row that cell's column named.
	check(
		decode("*1" + TAB_CHAR + "*2" + LF + "A" + COMMA + "B" + TAB_CHAR + "C")
			.map((value) => tokens_of(value, [COMMA]).join("+"))
			.join(",") === "*1,A+B,*2,C",
		"a cell holding a separated list is split by the site's separators inside its own column"
	);


	// Judging one serial: the same three refusals the server makes for one value.
	const judged = (text, max) => station.serial_refusal(text, max);

	check(Boolean(judged("", 60)), "an empty serial is refused with a reason");
	check(Boolean(judged("   ", 60)), "and so is one that is only whitespace");
	check(judged("S" + COMMA + "1", 60) === "", "a serial with punctuation in it is fine");
	check(Boolean(judged("S".repeat(61), 60)), "a serial past the site's length is refused");
	check(judged("S".repeat(60), 60) === "", "and one exactly at it is not");
	check(judged("S" + String.fromCharCode(0x200b) + "1", 60) !== "", "an invisible character is refused");
	check(judged("S" + String.fromCharCode(9) + "1", 60) !== "", "and so is a control character");
	check(judged("S-1", 0) === "", "with no length served, no length is judged");

	// The comparison a duplicate is decided by, which is the server's rule done coarsely.
	const same_serial = (text) => station.serial_identity(text);

	check(same_serial("ABC-1") === same_serial("abc-1"), "case does not make two serials");
	check(same_serial(" ABC-1 ") === same_serial("ABC-1"), "and neither does whitespace around it");
	check(same_serial("AB-12") !== same_serial("AB12"), "punctuation does");
	check(same_serial("AB 12") !== same_serial("AB12"), "and so does an internal space");

	console.log("\nthe next-column command comes from the site, and is never a serial");
	check(
		source.includes("status.scan_command"),
		"the command is read from the server's own answer, not written into this file"
	);
	check(
		!/["'`]\*\*["'`]/.test(source),
		"and the shipped default is not spelled anywhere in the station"
	);
	check(
		source.indexOf('input.val("")') < source.indexOf("add_pending(row_key, serial"),
		"and the box is emptied before the value is interpreted, so it leaves nothing behind"
	);
	check(
		/read_command\(token, state\.next_row_command\)/.test(source),
		"and one reader interprets commands for every input: a scanned value and a pasted token alike"
	);
	check(
		!/data\("submitted"/.test(source) || !/waiting/.test(source),
		"nothing is marked as sent any more: what was sent is what the waiting list says"
	);
	check(
		source.indexOf("is_next_row_command(serial") < source.indexOf("return add_pending(row_key, serial)"),
		"and before the scan it would have become — the command is not even kept as unsaved"
	);
	check(
		/state\.active = target;/.test(source),
		"navigation moves the active column, so a write still in flight cannot pull the cursor back"
	);
	check(
		/visible_row_keys\(\)/.test(source) && /\.find\("\.sp-scan-column"\)/.test(source),
		"the order it navigates by is read from the columns on screen, not from the payload"
	);

	// The move itself, read off the file: a scan still being saved belongs to the column it was
	// scanned into, and the one thing navigation must not do is touch it. The queue tests above
	// show a label is bound to a key when it is queued; this shows the move does not re-bind one.
	const move = source.slice(
		source.indexOf("const move_to_next_column = function"),
		source.indexOf("const remove_assignments = function")
	);

	check(move.length > 0, "the move to the next column is where this expects to find it");
	check(!/queues/.test(move), "it does not touch the queue a pending scan is waiting in");
	check(!/register\(/.test(move), "it does not register anything — the command is not a serial");
	check(!/busy/.test(move), "and it does not wait for, or clear, a write in flight");
	check(
		!/draw_row|summary|shortage|registered/.test(move),
		"nothing it does can move a counter, change a completion or ring the chime"
	);

	console.log(`\n${failures ? `FAIL (${failures} checks)` : "PASS"}`);

	process.exit(failures ? 1 : 0);
})();
