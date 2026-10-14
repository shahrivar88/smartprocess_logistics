// The Logistics Desk integration for Sales Invoice.
//
// Two form hooks and one scan station. Every count, every shortage and every refusal comes from
// the server: the endpoints answer with `core.quantities` coverage and with messages already
// written for an operator, and this file only draws them. A rule implemented here would be a rule
// the server tests could not reach.
//
// Why the station owns its own busy state
// ---------------------------------------
// Desk's overlay is counter-based (`frappe/public/js/frappe/dom.js:142-180`): `freeze()` appends
// `#freeze` once and increments `frappe.dom.freeze_count`, and `unfreeze()` **returns early when
// that counter is zero** — it only decrements and removes the overlay when it can. A busy state
// built on that pair is released only if every path calls `unfreeze()` exactly once. This dialog
// had paths that could not promise it: a submission still in flight when the next scan arrived, a
// `work()` that threw before it returned a promise (so `.finally` was never attached at all), and
// `frm.reload_doc()` freezing the form while ours was up. One unpaired `freeze()` leaves the
// counter above zero, and from that moment every `unfreeze()` in Desk is a silent no-op: the grey
// layer stays over the page until it is reloaded. That is the reported symptom — the row reached
// the database and the window was stuck on "در حال ثبت...".
//
// So the station never touches the global overlay. It shows its own progress in its own toolbar,
// disables only the column it is writing to, and releases both from a `finally` that cannot be
// skipped, because the promise it is attached to exists before the work starts.
//
// Assets are served as plain files from the app's `public` directory; see `hooks.py`.

frappe.provide("smartprocess_logistics");

smartprocess_logistics.ENDPOINT = "smartprocess_logistics.api.endpoints";
smartprocess_logistics.SETTINGS = "Logistics Settings";

smartprocess_logistics.ROLE = {
	viewer: "Logistics Serial Viewer",
	editor: "Logistics Serial Editor",
	manager: "Logistics Serial Manager",
};

/** Whether the acting user holds any of the roles an action needs. */
smartprocess_logistics.allowed = function (...roles) {
	if (frappe.user.has_role("System Manager")) {
		return true;
	}

	return roles.some((role) => frappe.user.has_role(role));
};

/** The Logistics Settings single, read once per page. */
smartprocess_logistics.settings = function () {
	if (!smartprocess_logistics._settings) {
		smartprocess_logistics._settings = frappe.db.get_single_value(
			smartprocess_logistics.SETTINGS,
			"enable_serial_registration"
		);
	}

	return smartprocess_logistics._settings;
};

/** Whether the acting user's company permissions reach this invoice's company. */
smartprocess_logistics.reaches_company = function (company) {
	if (!smartprocess_logistics._permissions) {
		// `get_user_permissions` answers with the object itself on this framework version and
		// is documented as answering with a promise on others. `Promise.resolve` takes either —
		// it flattens a promise and wraps a value — so the same code runs on both. Calling
		// `.then` on the plain object threw inside the form's `refresh` handler and the buttons
		// were never added, which is the failure this line exists to prevent.
		smartprocess_logistics._permissions = Promise.resolve(
			frappe.defaults.get_user_permissions()
		);
	}

	return smartprocess_logistics._permissions.then((permissions) => {
		const allowed = permissions && permissions.Company;

		if (!allowed || !allowed.length) {
			return true; // No company permission means every company, which is how the server reads it.
		}

		return allowed.some((entry) => entry.doc === company);
	});
};

// The verb each endpoint declares in its own whitelist. The reads are GET-only and the writes are
// POST-only, so the verb is stated here once rather than guessed at each call site: a POST to a
// GET-only endpoint is refused by the framework with 403 before the function runs, which is a
// failure that looks like a permission problem and is not one.
smartprocess_logistics.METHOD = {
	get_invoice_serial_status: "GET",
	get_serial_assignments: "GET",
	set_row_serials: "POST",
	add_row_serial: "POST",
	add_row_serials: "POST",
	delete_assignments: "POST",
	prepare_import_preview: "POST",
};

/** Call an endpoint and resolve with its message. */
smartprocess_logistics.call = function (method, args) {
	const type = smartprocess_logistics.METHOD[method];

	if (!type) {
		throw new Error(`unknown endpoint: ${method}`);
	}

	const path = `${smartprocess_logistics.ENDPOINT}.${method}`;

	return frappe
		.call({ method: path, args: args, type: type })
		.then((response) => response.message)
		.catch((error) => {
			// The server's message is written for the operator and carries the stable code. It is
			// shown here and the rejection is passed on, so the caller can put the station back
			// into a usable state — an error is not the end of a scan session.
			//
			// The two refusals the station answers in its own warning line are the exception: a
			// dialog in the middle of a scan session has to be dismissed before the next label can
			// be read, which is the interruption this window exists to avoid. Everything else — a
			// lost connection, a permission, a malformed serial — keeps the server's sentence in
			// full, because an unexpected failure is not something to swallow quietly.
			if (smartprocess_logistics.classify_failure(error) === "other") {
				// **Escaped, because a refusal quotes what the operator sent.** The server's
				// sentences carry the serial text they are about, and `frappe.msgprint` renders
				// what it is given: a label pasted out of a file is data, and a label that happens
				// to be `<img …>` must appear as those characters rather than as an image with an
				// event handler. The station's own places already escape (`warn` writes text, the
				// preview escapes each serial); this is the one that goes through Frappe's HTML.
				frappe.msgprint({
					indicator: "red",
					message: smartprocess_logistics.escape(
						smartprocess_logistics.failure_text(error) || __("درخواست انجام نشد.")
					),
					title: __("ثبت سریال"),
				});
			}

			return Promise.reject(error);
		});
};

/** Read a chosen file as the data URL the import endpoints accept. */
smartprocess_logistics.read_file = function (file) {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();

		reader.onload = () => resolve(reader.result);
		reader.onerror = () => reject(new Error(__("فایل خوانده نشد.")));
		reader.readAsDataURL(file);
	});
};

/** Escape for an attribute or a text node. One helper, so nothing is interpolated raw. */
smartprocess_logistics.escape = function (value) {
	return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
};

/**
 * A document-row name, as a CSS attribute selector value.
 *
 * `CSS.escape` is the right tool and is in every current browser; the fallback quotes the two
 * characters that would end the selector, so an older browser gets a working selector for a name
 * that has never held anything else. Matching on both sides through one helper is what keeps the
 * escaping from drifting.
 */
smartprocess_logistics.selector = function (row_key) {
	const value = String(row_key || "");

	if (window.CSS && typeof window.CSS.escape === "function") {
		return window.CSS.escape(value);
	}

	return value.replace(/["\\]/g, "\\$&");
};

// --- the completion chime ---------------------------------------------------------------

smartprocess_logistics.SOUND_KEY = "smartprocess_logistics.scan_alert_sound";

/**
 * Whether this user wants the completion chime. Kept in the browser, per user, so a shared
 * operator terminal remembers each person's choice. A browser that refuses storage (a locked-down
 * profile, private mode) simply does not remember it: the station must still work.
 */
smartprocess_logistics.sound_enabled = function (user) {
	let stored = null;

	try {
		stored = window.localStorage.getItem(
			`${smartprocess_logistics.SOUND_KEY}.${user || frappe.session.user}`
		);
	} catch (error) {
		stored = null;
	}

	return stored === null ? true : stored === "1";
};

smartprocess_logistics.set_sound_enabled = function (enabled, user) {
	try {
		window.localStorage.setItem(
			`${smartprocess_logistics.SOUND_KEY}.${user || frappe.session.user}`,
			enabled ? "1" : "0"
		);
	} catch (error) {
		// Deliberately silent: a preference that cannot be stored is not a failure an operator
		// can act on, and it must not interrupt a scan session.
	}

	return enabled;
};

/** The audio context, created once and only on demand. */
smartprocess_logistics.audio_context = function () {
	if (smartprocess_logistics._audio === undefined) {
		const Ctor = window.AudioContext || window.webkitAudioContext;

		try {
			smartprocess_logistics._audio = Ctor ? new Ctor() : null;
		} catch (error) {
			smartprocess_logistics._audio = null;
		}
	}

	return smartprocess_logistics._audio;
};

/**
 * Browsers only allow sound after a user gesture. The station calls this from its first keypress
 * or click, which is a gesture, so the chime is usually allowed by the time a column completes.
 */
smartprocess_logistics.unlock_audio = function () {
	const context = smartprocess_logistics.audio_context();

	if (context && context.state === "suspended") {
		context.resume().catch(() => {});
	}

	return true;
};

/**
 * A short two-note chime, about a second long and quiet, generated by the Web Audio API: no
 * asset to download, no library to load, no dependency this app has to keep in step.
 *
 * Returns whether it played. A refusal — a browser that has not been given a gesture, a context
 * that was blocked, an implementation without audio at all — is reported as `false` and never as
 * an exception, because the interface must not be disturbed by a sound that could not be made.
 */
smartprocess_logistics.play_completion_chime = function () {
	const context = smartprocess_logistics.audio_context();

	if (!context || context.state !== "running") {
		return false;
	}

	try {
		const start = context.currentTime;
		const gain = context.createGain();

		gain.gain.setValueAtTime(0.0001, start);
		gain.gain.linearRampToValueAtTime(0.08, start + 0.05);
		gain.gain.exponentialRampToValueAtTime(0.0001, start + 1);
		gain.connect(context.destination);

		[
			[880, 0],
			[1318.5, 0.12],
		].forEach(([frequency, offset]) => {
			const oscillator = context.createOscillator();

			oscillator.type = "sine";
			oscillator.frequency.setValueAtTime(frequency, start + offset);
			oscillator.connect(gain);
			oscillator.start(start + offset);
			oscillator.stop(start + 1);
		});

		return true;
	} catch (error) {
		return false;
	}
};

/**
 * Whether one server answer is a completion worth announcing.
 *
 * Three conditions, and each one is a case the station has to get right:
 *
 * - `announce` is false for a load. Opening the window on finished work, refreshing it, or
 *   reading a row that was already saved says nothing — the sound marks work *becoming* saved,
 *   not work being saved.
 * - The row is `SAVED` — the state the server's own numbers give, not the window's: a green row
 *   holding labels nobody has written yet is not a completion to announce, it is a completion to
 *   send, and the chime belongs to the answer.
 * - `was_saved` was not already true. A column emptied and filled again saves a second time and
 *   earns a second chime; a second answer about the same saved column does not.
 */
smartprocess_logistics.should_chime = function (announce, was_saved, state_name) {
	return Boolean(announce) && state_name === "SAVED" && was_saved !== true;
};

/**
 * What a keystroke means to a scan input.
 *
 * A keyboard-wedge scanner is a keyboard that types a label and then ends it — usually with
 * Enter, sometimes with Tab. Enter does nothing on its own, so the station takes it. Tab is the
 * browser's move-to-the-next-control key, and it is also a scanner's suffix: intercepted only
 * when there is something in the box to send, so an operator can still tab from an empty box to
 * the next column, and a Tab-suffixed wedge registers instead of wandering off.
 */
smartprocess_logistics.scanner_suffix = function (key, value) {
	if (key === "Enter") {
		return "submit";
	}

	if (key === "Tab") {
		return (value || "").trim() ? "submit" : "navigate";
	}

	return "ignore";
};

/**
 * Whether what was scanned is the site's next-column command rather than a serial.
 *
 * The whole value, exactly, both sides trimmed — never a prefix, a substring or a case-folded
 * match. A serial that happens to *contain* the command is a serial: `AB**CD` registers, and so
 * does `**1`. Matching loosely here would navigate away from a serial that was scanned correctly,
 * which is worse than a command that does not work: the operator sees a label missing from the
 * wrong row, or not at all.
 *
 * An unset command matches nothing, whatever was scanned. The value comes from the site's own
 * settings, and a station that invented a default for a site that has none would navigate on a
 * serial nobody configured it to navigate on.
 */
smartprocess_logistics.is_next_row_command = function (scanned, command) {
	const read = smartprocess_logistics.read_command(scanned, command);

	return Boolean(read && read.kind === "next");
};

/**
 * What a scanned or pasted token is: the next column, a named row, or a serial.
 *
 * One grammar, read in one place, because three input paths arrive here — a scanned value, a
 * pasted token, a serial the operator typed — and a second implementation of "is this a command"
 * would drift from this one without anybody noticing which path disagreed.
 *
 * Two commands, and both are the **whole token** or they are not commands:
 *
 * - the site's own next-column command, whatever this site configured it to be;
 * - `*N` or `#N`, which select the invoice row the columns show as `#N`. The two forms are the
 *   same command: an operator reading a packing list writes one and a barcode encodings sheet
 *   the other, and neither is more correct. `N` is any positive whole number, however long.
 *
 * Never a prefix and never a case-folded match: a serial that merely contains the configured
 * command is a serial, and a value that starts with it is not it. Matching loosely would navigate
 * away from a serial that was scanned correctly, which is worse than a command that does not work:
 * the operator sees a label missing from the wrong row, or not at all.
 *
 * A number this invoice has no row for is answered by the caller with a sentence rather than
 * registered as a serial: `*9` on a three-row invoice is an operator asking for a row that is not
 * there, and registering a serial named `*9` would be answering a different question. `*0` is the
 * same answer for the same reason — there is no row zero.
 */
smartprocess_logistics.read_command = function (token, command) {
	const value = String(token === null || token === undefined ? "" : token).trim();

	if (!value) {
		return null;
	}

	const configured = String(command || "").trim();

	if (configured && value === configured) {
		return { kind: "next" };
	}

	const numbered = /^[*#](\d+)$/.exec(value);

	return numbered ? { kind: "row", index: Number(numbered[1]) } : null;
};

/**
 * Characters a serial cannot contain, as the ranges of the ones the server refuses.
 *
 * `core.normalization` refuses Unicode categories `Cc`, `Cf`, `Cn`, `Co` and `Cs` outright rather
 * than deleting them, because a character that vanishes from an identity is a change nobody can
 * see. These are those categories' ranges as a serial can meet them — controls, the invisible
 * formatting characters, an unpaired surrogate, the private-use and specials blocks — written as
 * escapes so the line can be read rather than trusted.
 *
 * It is a mirror and not a second authority: a value this lets through is judged again by the
 * server, which refuses the column and names the serial it refused.
 */
smartprocess_logistics.INVISIBLE_CHARACTER =
	// Controls, the invisible formatting characters, an unpaired surrogate, and the private-use
	// and specials blocks — the ranges of the categories the server refuses. Written as escapes
	// so the line can be read rather than trusted.
	/[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF\uFFF9-\uFFFB\uD800-\uDFFF]/;

/**
 * Why this text cannot be a serial, or `""` when it can.
 *
 * The three refusals `core.normalization` makes about **one** serial, so a pasted list can drop
 * the pieces that are wrong and keep the rest — which is the whole reason each serial is judged
 * on its own rather than the batch being judged as a whole. The refusals about the *set* — a
 * duplicate, a row with no room — are decided by the caller, against the invoice.
 *
 * `max_length` is this site's own, served with the invoice. Zero means the window was not told
 * and checks nothing, which leaves that judgement to the server rather than inventing a limit.
 */
smartprocess_logistics.serial_refusal = function (text, max_length) {
	const value = String(text === null || text === undefined ? "" : text);

	if (!value.trim()) {
		return __("سریال خالی است.");
	}

	if (max_length && value.length > max_length) {
		return __("سریال بیش از {0} نویسه دارد.", [max_length]);
	}

	if (smartprocess_logistics.INVISIBLE_CHARACTER.test(value)) {
		return __("سریال نویسهٔ نامرئی یا کنترلی دارد.");
	}

	return "";
};

/**
 * The form two texts are compared in when asking whether they are the same serial.
 *
 * The server's identity is `NFKD`, marks dropped, recomposed, digits folded, case folded. A
 * serial the operator has not saved yet needs the same comparison done well enough to name a
 * duplicate before anything is sent, and where the two can disagree this one is the coarser: it
 * says "duplicate" a little too readily and never misses one. The server still decides
 * — it refuses the column and names the serial it refused — so a false positive costs the
 * operator one label they can add back, and a miss costs nothing.
 */
smartprocess_logistics.serial_identity = function (text) {
	return String(text === null || text === undefined ? "" : text)
		.normalize("NFKD")
		.replace(/[\u0300-\u036F]/g, "")
		.normalize("NFC")
		.replace(/[\u06F0-\u06F9]/g, (digit) => String(digit.charCodeAt(0) - 0x06f0))
		.replace(/[\u0660-\u0669]/g, (digit) => String(digit.charCodeAt(0) - 0x0660))
		.replace(/\u064A/g, "\u06CC")
		.replace(/\u0643/g, "\u06A9")
		.trim()
		.toLowerCase();
};

/**
 * Text into tokens, the way this site splits a list.
 *
 * `separators` is served with the invoice — this site's configured delimiters plus the ones a
 * typed list uses — as a list of **literal strings**, longest first, walked exactly as the
 * server's `core.delimiters.split_tokens` walks them. Literal strings and not a pattern: the
 * separators are site configuration, and a configuration value interpolated into a regular
 * expression is a regexp-injection hole with a mundane name.
 *
 * An empty token is dropped rather than kept. A separator typed twice is a keystroke, while the
 * same thing in a file is usually a mistake worth naming — which is why the two paths differ
 * here and only here, as `split_tokens` and `split_typed_list` differ on the server.
 */
smartprocess_logistics.split_tokens = function (text, separators) {
	const value = String(text === null || text === undefined ? "" : text);
	const ordered = (separators || [])
		.filter(Boolean)
		.slice()
		.sort((first, second) => second.length - first.length);
	const tokens = [];
	let buffer = "";
	let index = 0;

	while (index < value.length) {
		const found = ordered.find((separator) => value.startsWith(separator, index));

		if (found) {
			tokens.push(buffer);
			buffer = "";
			index += found.length;
			continue;
		}

		buffer += value[index];
		index += 1;
	}

	tokens.push(buffer);

	return tokens.map((token) => token.trim()).filter(Boolean);
};

// What a paste is split by when the window was not told what this site splits on: the
// separators a clipboard actually carries, because a spreadsheet's cell boundary is one of them
// whichever key produced it. Nothing else is assumed, so a site that configured a space as a
// delimiter is obeyed as soon as the server's own list arrives.
smartprocess_logistics.FALLBACK_SEPARATORS = (
	",",
	";",
	"؛",
	"،",
	"\r\n",
	"\n",
	"\r",
	"\t"
);

/**
 * One text decoded into the values the intake reads, in the order it reads them.
 *
 * This is the step between *what arrived* and *what it means*, and it exists because the two
 * inputs that arrive as text do not arrive in the same **shape**:
 *
 * - a **table** — a range of cells copied out of a spreadsheet, or a tab-separated answer from
 *   anything that renders one. Its rows are lines and its columns are the tabs between them, and
 *   its shape matters: an operator writes the command for a column in the first cell of that
 *   column, so `*1` over five serials and `*2` over ten are read one column at a time, top to
 *   bottom. Read the other way — across, row by row — the first line's commands are all consumed
 *   before a single serial arrives and every serial lands in the row the *last* command named.
 *   That is the difference this function makes, and it is the same order the server reads a
 *   workbook in (`core.spreadsheet.column_major_texts`), so a file and the paste of the same
 *   cells become the same sequence.
 * - a **list** — a serial a keyboard or a scanner produced, a column of cells, a line of text.
 *   It has no shape to lose, and it is returned whole so the site's own separators split it,
 *   which is what keeps a comma, a space or a newline working exactly as it did.
 *
 * A table is a shape that **spans rows**: two or more lines carrying a tab. One line with a tab
 * in it is a list whose separator happens to be a tab — and for a single line the two readings
 * agree, so nothing is decided by the guess. Newlines alone are never a table: a column of cells
 * copied out of a spreadsheet is one value per line, which is a list.
 *
 * Nothing here is interpreted. A cell is a text and stays a text: it is not trimmed into a
 * command, not validated as a serial, and not merged with its neighbours. Blank cells are not
 * returned — an empty cell names no row and is not a serial — and a blank row or column is
 * skipped without moving anything, because the row a value belongs to is chosen by the commands
 * in the values, never by where a cell sits. What makes a cell a command or a serial is `intake`,
 * and the values come back to it in this order.
 */
smartprocess_logistics.decode_intake_values = function (text) {
	const value = String(text === null || text === undefined ? "" : text);
	const lines = value.split(/\r\n|\r|\n/);
	const tabbed = lines.filter((line) => line.indexOf("\t") >= 0).length;

	if (tabbed < 2) {
		return [value];
	}

	const rows = lines.map((line) => line.split("\t"));
	const width = rows.reduce((widest, cells) => Math.max(widest, cells.length), 0);
	const values = [];

	for (let column = 0; column < width; column += 1) {
		rows.forEach((cells) => {
			const cell = String(cells[column] === undefined ? "" : cells[column]).trim();

			if (cell) {
				values.push(cell);
			}
		});
	}

	return values;
};

/**
 * The row key that follows this one, among the columns as they are shown.
 *
 * `keys` is the visible order — the columns in the window, in the order they are laid out, which
 * is the order the operator reads them in, right to left on a right-to-left page. Sorting by row
 * number, by name or by any key of the server's payload would be a different order than the one on
 * screen, and the cursor would jump somewhere the operator was not looking.
 *
 * Past the last column the answer is the first one: the operator scanning a row of six columns
 * and then going back to the top of the invoice is one instruction, not two. A column that is
 * already complete is stepped over by nobody — completion is not this function's business, and
 * an operator who needs to correct a finished column must still be able to reach it.
 */
/**
 * When a burst of keystrokes is a scanner's rather than a person's.
 *
 * A keyboard-wedge scanner is a keyboard that types a whole label at machine speed and ends it
 * with Enter or Tab. A wedge configured *without* a suffix never ends it at all — and then the
 * next label is typed straight onto the end of this one, which is two scans in one box: the
 * failure an operator reported, and the one this rule exists for.
 *
 * Nothing can tell two labels apart from one long label **while they are being typed**. What can
 * be told apart is who typed it: a scan's keystrokes are milliseconds apart, and a person's are
 * not. So a burst that took longer than `WEDGE_BURST_MS` end to end is somebody at a keyboard,
 * and is never filed on their behalf however long they pause — splitting a serial an operator is
 * typing by hand would be a worse fault than the one being fixed. The fallback is a fallback: a
 * wedge that sends its terminator needs none of it.
 *
 * What it cannot do, and what an operator should know: a wedge in continuous mode that sends no
 * terminator and starts the next label with no pause at all produces one unbroken run of
 * keystrokes, and no timing rule can split that — there is no gap in it to split at. This rule
 * covers the case that was reported, a trigger pulled once per label, where the pause between
 * pulls is what separates them. A scanner that can send Enter or Tab should send one.
 */
smartprocess_logistics.WEDGE_BURST_MS = 150;
smartprocess_logistics.WEDGE_SETTLE_MS = 120;
smartprocess_logistics.WEDGE_MIN_LENGTH = 3;

smartprocess_logistics.is_wedge_burst = function (burst) {
	if (!burst) {
		return false;
	}

	return (
		burst.length >= smartprocess_logistics.WEDGE_MIN_LENGTH &&
		burst.last - burst.started <= smartprocess_logistics.WEDGE_BURST_MS
	);
};

smartprocess_logistics.next_row_key = function (keys, current) {
	if (!keys || !keys.length) {
		return null;
	}

	const at = keys.indexOf(current);

	if (at < 0) {
		return keys[0];
	}

	return keys[(at + 1) % keys.length];
};

/**
 * What a column holds once the scans that are still only on screen are counted.
 *
 * The invoice's quantity is the **allowed** count. Everything scanned past it is an excess: it
 * stays visible, stays red and stays unsaved, and it is what the save refuses to write until an
 * operator removes it. Nothing here writes, and nothing here decides the server's answer — the
 * limit is enforced again, from the database, when the save sends anything.
 *
 * `persisted` is what the invoice already carries, `pending` what is waiting on screen, and
 * `scanned` is the two together: the number the operator has in hand, which is not the number the
 * invoice holds until they save.
 */
smartprocess_logistics.pending_counts = function (row, pending) {
	const record = row || {};
	const persisted = Number(record.registered || 0);
	const waiting = Number(pending || 0);
	const scanned = persisted + waiting;
	const allowed = Number(record.capacity || 0);
	const required = Number(record.required || 0);

	return {
		persisted: persisted,
		pending: waiting,
		scanned: scanned,
		allowed: allowed,
		excess: Math.max(0, scanned - allowed),
		remaining: Math.max(0, required - scanned),
	};
};

/**
 * The one state a serial row is in: `INCOMPLETE`, `STAGED`, `SAVED` or `CONFLICT`.
 *
 * The same four answers `core.eligibility.row_state` gives the server, from the same four numbers
 * and in the same order, because a window that decided for itself when a row is done is a window
 * whose rows and whose API disagree. What the window adds is the labels **it** is holding: the
 * counts here are the invoice's registrations and the operator's unsaved ones together, so a row
 * turns `STAGED` the moment the last label of a paste lands in it, and turns `SAVED` only when the
 * server has confirmed the write — the difference between the two is the whole reason `STAGED`
 * exists as a state of its own.
 *
 * Every card is drawn from this function and no other: a second definition of "complete" is how a
 * column came to be green in one place and gray in another.
 */
smartprocess_logistics.row_state = function (row, counts) {
	const record = row || {};
	const state = counts || {};
	const required = Number(record.required || 0);
	const allowed = Number(record.capacity || 0);
	const persisted = Number(state.persisted || 0);
	const staged = Number(state.pending || 0);
	const held = persisted + staged;

	if (held > allowed) {
		return "CONFLICT";
	}

	if (held < required) {
		return "INCOMPLETE";
	}

	return staged ? "STAGED" : "SAVED";
};

/**
 * The sentence that blocks a save, one line per row that is over its quantity.
 *
 * The item is named by its model rather than by its code, because the model is what is on the
 * column the operator is looking at, and the count is the excess — not the total — because that is
 * the number of labels they have to remove. It says nothing was saved, because nothing was.
 */
smartprocess_logistics.save_blocked_message = function (blocked) {
	const lines = (blocked || []).map((row) =>
		__("کالای «{0}»: {1} سریال بیش از تعداد فاکتور اسکن شده است.", [row.model, row.excess])
	);

	return `${__(
		"ذخیره انجام نشد: تعداد سریال‌های اسکن‌شده از تعداد فاکتور بیشتر است. سریال‌های اضافه را حذف کنید."
	)}<br>${lines.join("<br>")}`;
};

/**
 * The sentence a failure carries, whether it came from the server or from the network.
 *
 * The same extraction the call wrapper shows, in one place: a refusal the server wrote carries its
 * own Persian text, and a lost connection carries whatever the browser said. Either way the
 * operator gets the reason rather than a shrug.
 */
smartprocess_logistics.failure_text = function (error) {
	return (error && (error.message || error._server_messages)) || "";
};

/**
 * Whether the station may put the cursor back by itself.
 *
 * Focus is restored after every write, and this is the rule that keeps that from becoming a
 * nuisance: only when the cursor has nowhere to be — it is on the document, or already in this
 * very field — and never when the operator has moved somewhere on purpose, which includes another
 * column's input, a delete button, or the confirmation dialog that is asking them a question.
 * Their choice of where to look is not the station's to overrule, and a detached or disabled
 * field cannot take the focus anyway.
 */
smartprocess_logistics.should_refocus = function (facts) {
	return Boolean(
		facts.dialog_open &&
			facts.column_exists &&
			!facts.input_disabled &&
			(facts.focus_on_body || facts.focus_in_this_column)
	);
};

/**
 * The two refusals this window answers in its own line, by the codes `core` gives them.
 *
 * `CODE_ALREADY_REGISTERED` (`core/validation.py:30`) means the normalised label is already among
 * the invoice's registrations, on this row or any other. `CODE_EXCESS_QUANTITY` means the row
 * already holds as many labels as its invoiced quantity allows. Both were taken from live
 * refusals rather than from reading the module — re-scanning a registered label answers
 * `این سریال از قبل روی همین فاکتور ثبت شده است. [ALREADY_REGISTERED]`, and scanning past the
 * quantity answers the excess sentence with `[EXCESS_QUANTITY]` — because `_fail` puts the code in
 * the message on purpose: it is the one stable, machine-readable part of a refusal, and it is what
 * this file keys on instead of the Persian prose around it.
 *
 * The module's other duplication codes — `DUPLICATE_IN_ROW`, `DUPLICATE_IN_INVOICE` — are a
 * different failure and are deliberately not matched: they mean the same label appeared **twice
 * inside one submission**, a malformed input with a different remedy. A station sends one serial
 * per request, so they cannot arise from it; if one ever arrives it keeps the ordinary path, in
 * full view.
 */
smartprocess_logistics.CODE_ALREADY_REGISTERED = "ALREADY_REGISTERED";
smartprocess_logistics.CODE_EXCESS_QUANTITY = "EXCESS_QUANTITY";

/** Every bracketed code a refusal carries. */
smartprocess_logistics.codes_in = function (error) {
	const text = [
		error && error.message,
		error && error._server_messages,
		error && error.exc_type,
	]
		.filter(Boolean)
		.join(" ");

	return (text.match(/\[([A-Z_]{4,})\]/g) || []).map((bracketed) => bracketed.slice(1, -1));
};

/**
 * What a refusal means: "already registered", "this row is full", or something else entirely.
 *
 * The third answer is the load-bearing one. A lost connection, a permission the caller does not
 * hold, a malformed serial, a race with another operator — none of those is this station's to
 * interpret, and each keeps the path where the server's sentence is shown in full and the scanned
 * text stays in the box for correction. Only the two codes above are answered inline.
 */
smartprocess_logistics.classify_failure = function (error) {
	const codes = smartprocess_logistics.codes_in(error);

	if (codes.includes(smartprocess_logistics.CODE_ALREADY_REGISTERED)) {
		return "duplicate";
	}

	if (codes.includes(smartprocess_logistics.CODE_EXCESS_QUANTITY)) {
		return "excess";
	}

	return "other";
};

/** The line shown when a label has been refused as already registered. */
smartprocess_logistics.duplicate_warning = function (serial) {
	return __("این سریال قبلاً ثبت شده است: {0}", [serial]);
};

/** The line shown when the row has reached the quantity its invoice allows. */
smartprocess_logistics.excess_warning = function () {
	return __("تعداد سریال‌های این کالا تکمیل شده است. امکان ثبت سریال بیشتر وجود ندارد.");
};

/**
 * Run `work` after the previous `work` for the same key, and never in parallel with it.
 *
 * This is what makes twenty labels in a row survive a scanner that types faster than the server
 * answers. The previous version *dropped* a scan that arrived while the column was busy, and —
 * because the text was already recorded as sent — pressing Enter again did nothing either. The
 * label was gone with no message, which is what an operator experiences as "I have to click the
 * box before the next scan".
 *
 * The stored chain swallows rejections so one failed label cannot stall the ones behind it; the
 * caller still receives the real outcome through the returned promise.
 */
smartprocess_logistics.enqueue = function (queues, key, work) {
	const next = (queues[key] || Promise.resolve()).then(work, work);

	queues[key] = next.then(
		() => undefined,
		() => undefined
	);

	return next;
};

// --- the form -------------------------------------------------------------------------

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus === 2) {
			return; // A cancelled invoice takes no registrations.
		}

		smartprocess_logistics.settings().then((enabled) => {
			if (!enabled) {
				return; // The switch is documented as removing this button and nothing else.
			}

			return smartprocess_logistics.reaches_company(frm.doc.company).then((reaches) => {
				if (!reaches) {
					return;
				}

				if (smartprocess_logistics.allowed(smartprocess_logistics.ROLE.editor)) {
					// **One button, and one press opens the window.** It used to be a «سریال‌ها»
					// dropdown holding «ثبت سریال» beside a «جزئیات سریال‌ها» entry: two doors to one
					// room, and the door the operator wants behind another click. The dropdown argument
					// is therefore not passed — a button added with no group is a plain button in the
					// toolbar, and the framework reuses it by label rather than adding a second one, so
					// every refresh draws the same single button.
					//
					// The details window stays where it is (`open_details_dialog`, below) and is simply
					// no longer on the toolbar; nothing about it changed.
					frm.add_custom_button(__("سریال‌ها"), () =>
						smartprocess_logistics.open_serial_dialog(frm)
					);
				}
			});
		});
	},
});

// --- shared drawing ------------------------------------------------------------------

/** The coverage summary chips, drawn from the server's own totals. */
smartprocess_logistics.summary_html = function (summary) {
	if (!summary) {
		return "";
	}

	const chips = [
		// The totals cover the rows this dialog can work on, which are the invoice's serial rows.
		`<span class="sp-logistics-chip">${__("ردیف‌های نیازمند")}: ${summary.rows}</span>`,
		`<span class="sp-logistics-chip">${__("نیاز")}: ${summary.required}</span>`,
		`<span class="sp-logistics-chip">${__("ثبت‌شده")}: ${summary.registered}</span>`,
	];

	// Rows on this invoice that cannot take serials, and the labels they already carry. They are
	// not columns — nothing here works on them — but they are real records, and silently leaving
	// them out of a count would be the window pretending the invoice is smaller than it is.
	if (summary.ineligible_rows) {
		chips.push(
			`<span class="sp-logistics-chip">${__("ردیف‌های بدون ثبت سریال")}: ${
				summary.ineligible_rows
			}</span>`
		);
	}

	if (summary.shortage) {
		chips.push(
			`<span class="sp-logistics-chip is-short">${__("کسری")}: ${summary.shortage}</span>`
		);
	}

	if (summary.excess) {
		chips.push(
			`<span class="sp-logistics-chip is-short">${__("مازاد")}: ${summary.excess}</span>`
		);
	}

	if (summary.is_complete) {
		chips.push(`<span class="sp-logistics-chip is-complete">${__("کامل")}</span>`);
	}

	return `<div class="sp-logistics-summary">${chips.join("")}</div>`;
};

// --- the scan station ------------------------------------------------------------------

/**
 * One column per invoice item row, and a standing input per column.
 *
 * The station writes one serial at a time through `add_row_serial`, which *adds* to what the row
 * holds. That is the difference between this and a row-wide box: two operators scanning into the
 * same invoice no longer overwrite each other's labels, because neither sends the other's set.
 *
 * Columns scroll sideways as the invoice grows, and each column's registrations scroll inside the
 * column, so the item and its input stay where the operator's eyes are however tall the column
 * gets.
 */
smartprocess_logistics.open_serial_dialog = function (frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("ثبت سریال برای {0}", [frm.doc.name]),
		size: "extra-large",
		fields: [{ fieldname: "body", fieldtype: "HTML" }],
		primary_action_label: __("بستن"),
		primary_action() {
			close();
		},
	});

	dialog.$wrapper.addClass("sp-logistics-dialog sp-scan-dialog");

	const state = {
		status: null,
		// row_key -> whether the server last called that row complete. The chime is the transition
		// from false to true, so this map is the memory that keeps it to one sound per completion.
		complete: {},
		// row_key -> true while a write for that column is in flight. One write per column at a
		// time is what stops a barcode scanner's Enter from queuing a second registration.
		busy: {},
		// row_key -> the promise the next write for that column waits behind. A scan that arrives
		// while the previous one is still being saved joins this queue instead of being dropped.
		queues: {},
		// The column the operator is scanning into. Kept here rather than read from the document,
		// so it survives a column being redrawn — which is what lets a redraw, a refresh or a
		// queued write return the cursor to the right place.
		active: null,
		// Registrations were added or removed: the form is reloaded once, on close.
		dirty: false,
		// row_key -> the scans that exist only on this screen, in the order they were scanned.
		// Nothing here has been sent anywhere: a scan is a value in this list until an operator
		// presses Save, and the invoice holds nothing until that save succeeds. It is deliberately
		// the only place a scan lives, so a redraw, a refresh or an import landing mid-scan cannot
		// lose one — the cells are drawn *from* this map, not copied out of it.
		pending: {},
		// The identity of one pending scan, so a delete can name it across redraws. The array order
		// is the scan order, which is what makes the red excess cells the same cells every time.
		pending_seq: 0,
		// A save is in flight: the button says so and refuses a second press.
		saving: false,
		// row_key -> the burst of keystrokes being typed into that column, and the timer that will
		// file it if the scanner never ends it. Both are cleared the moment a label is taken.
		burst: {},
		settle: {},
		// The site's next-column command, read from Logistics Settings when the window opens.
		// Empty until that read answers, and empty if the user may not read the setting — in
		// which case nothing navigates and every scan registers, which is the safe way to be
		// wrong. It is never a literal here: the value belongs to the site, not to this file.
		next_row_command: "",
		// The two rules this site splits and judges pasted text by, served with the invoice: the
		// separators it splits on and the serial length it allows. `null` until that answer
		// arrives, and the window then splits a paste on the separators a clipboard carries and
		// judges no length — the site's own answer is what it uses, and nothing here is a second
		// opinion about what the site configured.
		intake: null,
		// The first load never announces anything, and never will: opening a window is not an event.
		loaded: false,
		// The file the operator chose, kept for the name the server needs to read it by. The bytes
		// are read once, on the click that asked for them.
		import_file: null,
		// One timer for the window's warning line. Held here rather than on the element so a
		// second rejection restarts it — the first warning's expiry must never clear the newer one.
		warning_timer: null,
	};

	const row_of = (row_key) =>
		((state.status || {}).rows || []).find((row) => row.row_key === row_key);

	/** The columns in the order they are shown: the document's own order, which is the page's. */
	const visible_row_keys = () =>
		dialog.$wrapper
			.find(".sp-scan-column")
			.map((index, node) => $(node).attr("data-row-key"))
			.get();

	// The toolbar chip is the **write** indicator. A file being read has a notice of its own, in the
	// place its result will appear, and saying «در حال ثبت…» there would name the wrong operation.
	const any_busy = () =>
		Object.keys(state.busy).some((key) => key !== "preview" && state.busy[key]);

	const refresh_progress = () => {
		const chip = dialog.$wrapper.find(".sp-scan-progress");

		if (any_busy()) {
			chip.removeClass("is-hidden");
		} else {
			chip.addClass("is-hidden");
		}
	};

	/**
	 * Mark one column busy while its write is in flight — visibly, and only over itself.
	 *
	 * `Promise.resolve().then(work)` is the whole defence against the stuck overlay this dialog
	 * used to leave behind: a promise exists before `work` runs, so `.finally` always runs, even
	 * if `work` throws before it can return anything of its own.
	 *
	 * The input is deliberately **not** disabled. Disabling the field the operator is scanning into
	 * makes the browser move the focus to the document — that is the platform's rule for a field
	 * that cannot hold the cursor — which is how the cursor was lost after every label. A scan that
	 * arrives while this one is in flight is queued instead (see `enqueue`), so nothing has to hold
	 * the keyboard away from the box to keep two writes from overlapping.
	 */
	const with_busy = (row_key, work) => {
		state.busy[row_key] = true;
		refresh_progress();
		dialog.$wrapper
			.find(`.sp-scan-column[data-row-key="${smartprocess_logistics.selector(row_key)}"]`)
			.addClass("is-busy");

		return Promise.resolve()
			.then(work)
			.finally(() => {
				delete state.busy[row_key];
				refresh_progress();
				dialog.$wrapper
					.find(`.sp-scan-column[data-row-key="${smartprocess_logistics.selector(row_key)}"]`)
					.removeClass("is-busy");
			});
	};

	/** Draw a column from one server row, and announce a completion if this is one. */
	const draw_row = function (row, announce) {
		const column = dialog.$wrapper.find(`.sp-scan-column[data-row-key="${CSS.escape(row.row_key)}"]`);

		if (!column.length) {
			return;
		}

		const was_complete = state.complete[row.row_key];
		// The scans that are still only on this screen, in the order they were scanned.
		const waiting = state.pending[row.row_key] || [];
		const counts = smartprocess_logistics.pending_counts(row, waiting.length);
		// Which of them are the excess ones. Everything past the quantity is red and nothing
		// inside it is — the labels the invoice can hold are not what is wrong with the column —
		// and the position is counted over the whole list, persisted first, so a row that already
		// holds too many in the database keeps its own red cells and the new ones join them.
		const first_excess = counts.scanned - counts.excess;

		const saved_cells = (row.assignments || [])
			.map(
				(entry) => `<div class="sp-scan-cell${
					entry.is_excess ? " is-excess" : ""
				}" data-assignment="${smartprocess_logistics.escape(entry.name)}">
					<span class="sp-scan-serial">${smartprocess_logistics.escape(
						entry.serial_no || entry.normalized_serial
					)}</span>
					<button type="button" class="sp-scan-icon" data-action="delete-one"
						data-assignment="${smartprocess_logistics.escape(entry.name)}"
						title="${__("حذف این سریال")}"
						aria-label="${__("حذف این سریال")}">${frappe.utils.icon("circle-x", "sm")}</button>
			</div>`
			)
			.join("");

		// Unsaved scans are drawn last and drawn differently: a dashed cell, so an operator can see
		// at a glance which labels the invoice does not have yet, without reading a word. The delete
		// control is the same control, and drops the label without asking the server for anything.
		const waiting_cells = waiting
			.map(
				(entry, index) => `<div class="sp-scan-cell is-pending${
					counts.persisted + index >= first_excess ? " is-excess" : ""
				}" data-pending="${entry.id}">
					<span class="sp-scan-serial">${smartprocess_logistics.escape(entry.serial)}</span>
					<button type="button" class="sp-scan-icon" data-action="delete-pending"
						data-pending="${entry.id}"
						title="${__("حذف این سریالِ ذخیره‌نشده")}"
						aria-label="${__("حذف این سریالِ ذخیره‌نشده")}">${frappe.utils.icon("circle-x", "sm")}</button>
			</div>`
			)
			.join("");

		const cells = saved_cells + waiting_cells;
		// **One state, and every card is drawn from it.** Which of the four a row is in is decided
		// by `smartprocess_logistics.row_state` — the window's copy of the rule the server states —
		// and the classes below are only how that state is painted: gray for a row that is short,
		// green for one that holds exactly what it needs with labels still in hand, blue for one
		// whose labels are all in the database, red for one holding more than the invoice allows.
		const state_name = smartprocess_logistics.row_state(row, counts);

		column.toggleClass("is-incomplete", state_name === "INCOMPLETE");
		column.toggleClass("is-staged", state_name === "STAGED");
		column.toggleClass("is-saved", state_name === "SAVED");
		column.toggleClass("is-conflict", state_name === "CONFLICT");
		column.find(".sp-scan-counts").html(
			[
				`<span class="sp-scan-count">${__("نیاز")} <b>${row.required}</b></span>`,
				// What the **invoice** holds: the database's own number, which is what a later save
				// is compared against and what the state's `SAVED` is judged on.
				`<span class="sp-scan-count">${__("ثبت‌شده")} <b>${counts.persisted}</b></span>`,
				// How many more labels this column needs, counted over what has been scanned —
				// persisted and waiting together — because that is what the operator has in
				// hand. What the *invoice* holds is the count beside it, and the unsaved ones
				// are called out on their own line so the difference is never hidden.
				`<span class="sp-scan-count ${
					counts.remaining ? "is-short" : "is-ok"
				}">${__("باقی‌مانده")} <b>${counts.remaining}</b></span>`,
			]
				.concat(
					counts.pending
						? [
								`<span class="sp-scan-count is-waiting">${__("ذخیره‌نشده")} <b>${
									counts.pending
								}</b></span>`,
							]
						: []
				)
				.concat(
					counts.excess
						? [
								`<span class="sp-scan-count is-short">${__("مازاد")} <b>${
									counts.excess
								}</b></span>`,
							]
						: []
				)
				.join("")
		);
			column.find(".sp-scan-title").attr("title", row.model || row.item_code || "");
		// A literal tick, not an icon: it is the mark the operator is looking for, and a font
		// sprite that happened to be missing would leave the one signal that matters invisible.
		// It marks the **saved** state and not the staged one, because the tick is the answer the
		// invoice gave — a green column with unsaved labels has not been written yet, and a tick
		// there would say it had.
		column
			.find(".sp-scan-tick")
			.text(state_name === "SAVED" ? "✓" : "")
			.attr("aria-hidden", "true");
		column.find(".sp-scan-cells").html(
			cells || `<div class="sp-scan-empty">${__("سریالی ثبت نشده است.")}</div>`
		);
		// The clear control belongs to a column that has something to clear — saved or waiting.
		// Hidden rather than removed, so it never has to be re-bound and cannot be reached by a
		// keyboard that is tabbing through a column with nothing in it.
		column
			.find(".sp-scan-foot")
			.toggleClass("is-empty", !(row.assignments || []).length && !waiting.length);

		state.complete[row.row_key] = state_name === "SAVED";

		if (smartprocess_logistics.should_chime(announce, was_complete, state_name)) {
			if (smartprocess_logistics.sound_enabled()) {
				smartprocess_logistics.play_completion_chime();
			}

			frappe.show_alert(
				{ indicator: "green", message: __("ستون {0} کامل شد.", [row.model || row.item_code]) },
				4
			);
		}
	};

	/**
	 * One column's markup. The input is a standing one: it is never re-created for a write.
	 *
	 * The heading is the model alone, on a line of its own, because the model is the only thing
	 * here an operator has to read to know which column they are in — and on a real invoice the
	 * model is long (`DS-2CD2166G2-ISU (2.8mm)` is a short one). It is given the full width of the
	 * header and allowed to wrap onto as many lines as it needs; nothing truncates it, and no
	 * height is fixed, because an ellipsis on a model name is a column that cannot be identified.
	 *
	 * The row number sits on a second line, under the model and quieter than it — it is a
	 * position, not a name, and the operator is reading the model. The counts follow on the third
	 * line, where they were.
	 *
	 * Clearing the whole column is a real action — it deletes every serial in the column, after
	 * asking — and it used to be an icon beside the model. It is now a labelled control at the
	 * foot of the column, under the serials it deletes, and it appears only when the column has
	 * something to clear: an icon beside a model is read as decoration, and the one destructive
	 * action in the window should not be. Nothing else about it changed, including the
	 * confirmation, which names the row.
	 */
	const column_html = function (row) {
		return `<section class="sp-scan-column" data-row-key="${smartprocess_logistics.escape(
			row.row_key
		)}">
			<header class="sp-scan-head">
				<div class="sp-scan-heading">
					<span class="sp-scan-tick"></span>
					<span class="sp-scan-title">${smartprocess_logistics.escape(
						row.model || row.item_code
					)}</span>
				</div>
				<div class="sp-scan-index">#${row.row_index}</div>
				<div class="sp-scan-counts"></div>
			</header>
			<div class="sp-scan-entry">
				<input type="text" class="form-control sp-scan-input"
					data-row-key="${smartprocess_logistics.escape(row.row_key)}"
					placeholder="${__("سریال را وارد کنید")}"
					autocomplete="off" autocapitalize="off" spellcheck="false">
				<div class="sp-scan-flash" aria-live="polite"></div>
			</div>
			<div class="sp-scan-cells"></div>
			<div class="sp-scan-foot">
				<button type="button" class="sp-scan-clear" data-action="clear-column"
					title="${__("حذف همه سریال‌های این ستون")}"
					aria-label="${__("حذف همه سریال‌های این ستون")}">${__("حذف همه")}</button>
			</div>
		</section>`;
	};

	const draw = function (status) {
		state.status = status;
		state.loaded = true;

		// The site's next-column command, served with the invoice and already checked on the
		// server against that site's own delimiters and serial length — an unusable one arrives
		// empty, so a value nobody could match can never navigate an operator past a serial.
		// Read once, in `draw`: it is a property of the site, not of this invoice, and nothing
		// that happens inside this window can change it.
		state.next_row_command = (status.scan_command || "").trim();
		// And the site's delimiters and serial length, for the text the window judges itself —
		// a paste, which cannot wait for a round trip per serial. Same argument, same place.
		state.intake = status.intake || null;

		const rows = status.rows || [];

		dialog.fields_dict.body.$wrapper.html(`
			<div class="sp-scan-toolbar">
				${smartprocess_logistics.summary_html(status.summary)}
				<span class="sp-scan-progress is-hidden">${__("در حال ثبت…")}</span>
				<label class="sp-scan-sound">
					<input type="checkbox" class="sp-scan-sound-toggle" ${
						smartprocess_logistics.sound_enabled() ? "checked" : ""
					}>
					<span>${__("هشدار صوتی تکمیل ستون")}</span>
				</label>
				<button class="btn btn-xs btn-default" data-action="refresh">
					${frappe.utils.icon("refresh", "xs")} ${__("به‌روزرسانی")}
				</button>
				<button class="btn btn-xs btn-primary" data-action="save-pending" disabled>
					${__("ذخیره سریال‌ها")}
				</button>
			</div>
			${
				rows.length
					? `<div class="sp-scan-columns">${rows.map(column_html).join("")}</div>`
					: `<div class="sp-logistics-empty">${__("این فاکتور ردیف قابل ثبت ندارد.")}</div>`
			}
			<details class="sp-scan-import">
				<summary>${__("ثبت گروهی از فایل (متنی یا XLSX)")}</summary>
				<div class="sp-logistics-toolbar">
					<div>
						<input type="file" class="sp-logistics-file" accept=".txt,.xlsx,.xlsm">
						<div class="sp-logistics-file-hint">
							${__(
								"فایل متنی یا XLSX. سریال‌های آن در ستون‌ها قرار می‌گیرند و با «ذخیره سریال‌ها» ثبت می‌شوند."
							)}
						</div>
					</div>
					<button class="btn btn-xs btn-default" data-action="preview">
						${__("بارگذاری و پیش‌نمایش")}
					</button>
				</div>
				<div class="sp-scan-preview"></div>
			</details>
			<div class="sp-scan-warning" role="status"></div>
		`);

		// Every column from the first load, with no announcement: a window that opens on finished
		// work must open silently, however many of its columns are complete.
		rows.forEach((row) => draw_row(row, false));

		// Nothing is waiting on a window that has just been drawn, but the button is drawn from the
		// same place every time so a redraw can never leave it enabled over an empty batch.
		refresh_save_button();

		// The column the operator was last in, or the first one. A redraw — a refresh, or a load
		// that lands after one — must not send the cursor back to the top of the invoice.
		const target = state.active
			? input_of(state.active)
			: dialog.$wrapper.find(".sp-scan-input").first();

		if (target.length) {
			target.trigger("focus");
		}
	};

	const load_status = function (announce) {
		return smartprocess_logistics
			.call("get_invoice_serial_status", { sales_invoice: frm.doc.name })
			.then((status) => {
				if (!state.status) {
					draw(status);

					return status;
				}

				state.status = status;
				dialog.fields_dict.body.$wrapper.find(".sp-logistics-summary").replaceWith(
					smartprocess_logistics.summary_html(status.summary)
				);
				(status.rows || []).forEach((row) => draw_row(row, Boolean(announce)));

				return status;
			});
	};

	/** The message an operator sees next to the input, and how long it stays. */
	const flash = function (row_key, message, ok) {
		const box = dialog.$wrapper.find(
			`.sp-scan-column[data-row-key="${smartprocess_logistics.selector(row_key)}"] .sp-scan-flash`
		);

		box
			.text(message)
			.removeClass("is-ok is-error")
			.addClass(ok ? "is-ok" : "is-error");

		window.clearTimeout(box.data("timer"));
		box.data(
			"timer",
			window.setTimeout(() => box.text("").removeClass("is-ok is-error"), ok ? 2500 : 6000)
		);
	};

	/**
	 * Answer a refused label in the window's own warning line.
	 *
	 * At the bottom of the window, one line, three seconds, no dialog and nothing to acknowledge:
	 * a scanner keeps working while this is on screen, and the operator reads it without leaving
	 * the box their next label is going into.
	 *
	 * One timer for the whole line, cleared and restarted on every warning, so a second rejection
	 * replaces the first one's text *and* its deadline. Setting the text from the first warning's
	 * expiry would otherwise wipe a message that arrived after it.
	 */
	const warn = function (message) {
		const bar = dialog.$wrapper.find(".sp-scan-warning");

		bar.text(message).addClass("is-visible");

		window.clearTimeout(state.warning_timer);
		state.warning_timer = window.setTimeout(() => {
			bar.removeClass("is-visible").text("");
		}, 3000);
	};

	/** One column's input, re-queried. A redraw replaces nodes, and a stale node is inert. */
	const input_of = (row_key) =>
		dialog.$wrapper.find(
			`.sp-scan-column[data-row-key="${smartprocess_logistics.selector(row_key)}"] .sp-scan-input`
		);

	/**
	 * Put the cursor back in the column the operator is scanning into.
	 *
	 * Three things it has to get right:
	 *
	 * - **After the write, never inside it.** The field is re-queried here rather than reused from
	 *   the call that started the request — a redraw, a refresh or a re-render leaves the earlier
	 *   node detached, and a detached field cannot take the focus. Deferring by a turn also puts
	 *   this after the busy state has been released.
	 * - **Only when the cursor has nowhere better to be.** `should_refocus` owns that rule: a lost
	 *   cursor, or our own field, belongs to the station; another column's input, a delete button
	 *   or an open confirmation dialog belongs to the operator.
	 * - **Only for the column they are still in.** A queued write for column A must not pull the
	 *   cursor back out of column B if they moved on while it was saving.
	 */
	const restore_focus = function (row_key) {
		window.setTimeout(() => {
			if (state.active && state.active !== row_key) {
				return;
			}

			const column = dialog.$wrapper.find(
				`.sp-scan-column[data-row-key="${smartprocess_logistics.selector(row_key)}"]`
			);
			const input = column.find(".sp-scan-input");
			const active = document.activeElement;

			const may = smartprocess_logistics.should_refocus({
				dialog_open: dialog.$wrapper.is(":visible"),
				column_exists: column.length > 0,
				input_disabled: input.length ? Boolean(input.prop("disabled")) : true,
				focus_on_body:
					!active || active === document.body || active === document.documentElement,
				focus_in_this_column: input.length > 0 && active === input[0],
			});

			if (may) {
				input.trigger("focus");
			}
		}, 0);
	};

	/**
	 * Keep the cached status in step with a row the server has just answered about.
	 *
	 * Every redraw of a column reads the row from one place — the status the window was drawn from
	 * — so that place has to hold what the server last said. It used to be updated only on a full
	 * load, and a save that drew each row from its own answer and then redrew the window from the
	 * cache put the *old* rows back on screen: five labels written, and a column showing none.
	 */
	const remember_row = function (row) {
		const rows = (state.status || {}).rows;

		if (!rows || !row) {
			return;
		}

		const at = rows.findIndex((candidate) => candidate.row_key === row.row_key);

		if (at >= 0) {
			rows[at] = row;
		}
	};

	/** One column, redrawn from the server's row and whatever is still waiting on screen. */
	const redraw_column = function (row_key) {
		const row = row_of(row_key);

		if (row) {
			draw_row(row, false);
		}
	};

	/** How many scans are waiting to be saved, across every column. */
	const pending_total = () =>
		Object.keys(state.pending).reduce((total, key) => total + state.pending[key].length, 0);

	/** The count on the save button, and whether it can be pressed at all. */
	const refresh_save_button = () => {
		const button = dialog.$wrapper.find("[data-action='save-pending']");

		if (!button.length) {
			return;
		}

		const waiting = pending_total();

		button.prop("disabled", state.saving || !waiting);
		button.text(waiting ? __("ذخیره سریال‌ها ({0})", [waiting]) : __("ذخیره سریال‌ها"));
	};

	/**
	 * One serial into one column, or the reason it cannot go there.
	 *
	 * This is the only place a serial is staged, and every way one arrives ends here: a scanned
	 * label, a label the operator typed, each token of a pasted list, and each serial a previewed
	 * file turned out to hold. That is what makes the three input paths one workflow instead of
	 * three — the rules about what a serial *is* are decided once, against the same invoice, and
	 * a change to them changes all three.
	 *
	 * Two refusals are possible, and they are different questions:
	 *
	 * - **Not a serial at all** — empty, too long, or carrying a character the server refuses.
	 *   Judged by `serial_refusal`, per serial, so a pasted list keeps the pieces that are fine.
	 * - **A duplicate** — the same identity already on this invoice: registered on any of its rows,
	 *   or waiting in any of its columns. Scoped to this invoice and nothing else, which is why a
	 *   serial another invoice carries is not a duplicate here and stays eligible; the identity the
	 *   server would give it is what is compared, through `serial_identity`.
	 *
	 * It touches no DOM. Staging a pasted list of two hundred is two hundred of these and then one
	 * redraw per column that changed, rather than two hundred redraws of a growing column.
	 */
	const stage = function (serial, row_key) {
		const text = String(serial === null || serial === undefined ? "" : serial).trim();
		const refusal = smartprocess_logistics.serial_refusal(
			text,
			(state.intake || {}).max_length || 0
		);

		if (refusal) {
			return { ok: false, reason: refusal, serial: text };
		}

		const identity = smartprocess_logistics.serial_identity(text);
		const duplicate = { ok: false, duplicate: true, serial: text, reason: smartprocess_logistics.duplicate_warning(text) };

		if (staged_identity(identity)) {
			return duplicate;
		}

		const row = row_of(row_key);

		if (!row) {
			return { ok: false, reason: __("ردیفی برای این سریال پیدا نشد."), serial: text };
		}

		// Registered on this invoice already — on this row or on another of its rows. The stored
		// `normalized_serial` is the identity the server computed, so this compares its rule with
		// its own rather than with a second opinion about what the text means.
		if (invoice_identity(identity)) {
			return duplicate;
		}

		state.pending_seq += 1;
		(state.pending[row_key] = state.pending[row_key] || []).push({
			id: state.pending_seq,
			serial: text,
			identity: identity,
		});

		return { ok: true, serial: text, row_key: row_key };
	};

	/** Whether this identity is waiting in any of this invoice's columns. */
	const staged_identity = function (identity) {
		return Object.keys(state.pending).some((key) =>
			(state.pending[key] || []).some((entry) => entry.identity === identity)
		);
	};

	/** Whether this identity is registered on this invoice, on any of its rows. */
	const invoice_identity = function (identity) {
		return ((state.status || {}).rows || []).some((row) =>
			(row.assignments || []).some(
				(entry) => smartprocess_logistics.serial_identity(entry.normalized_serial || entry.serial_no) === identity
			)
		);
	};

	/**
	 * Values into the columns: the one interpreter every input path share.
	 *
	 * `values` is a sequence of input values in the order they arrived — one for a scanned label,
	 * one for a paste, and one per nonempty cell for a file — and `split` says whether each of them
	 * is a text this site's separators take apart. Everything after that is the same for all of
	 * them: each token is read as a command or as a serial, commands move the cursor, serials are
	 * staged through `stage`, and the report says what happened to each one.
	 *
	 * **A text is decoded before it is split** (`decode_intake_values`), so a paste carrying a
	 * spreadsheet's table is read column by column like a workbook, while a list keeps the
	 * sequential reading a list has. A cell from a file is a text like any other and takes the same
	 * step: one with no tab in it comes back unchanged, and one holding a whole table — a
	 * single-cell workbook, or a cell somebody pasted a range into — is read the way the same text
	 * pasted into a box would be.
	 *
	 * **The selected row persists across the values.** That is what makes a workbook work: `#2` in
	 * the first cell of a column selects row 2 for every value beneath it, and the next column's
	 * `#3` moves it again — the cell's boundary ends its value and changes nothing else. A value
	 * that is not a command and holds no command is staged where the previous value left the
	 * cursor, which is `start_row_key` until something says otherwise: the column a paste arrived
	 * in, or the invoice's first row for a file, which is the one input with no column of its own.
	 *
	 * Nothing is redrawn here. The caller draws the columns that changed, once, and says what was
	 * refused in whatever voice suits where the input came from.
	 */
	const intake = function (values, start_row_key, options) {
		const rules = state.intake || {};
		const separators =
			rules.delimiters && rules.delimiters.length
				? rules.delimiters
				: smartprocess_logistics.FALLBACK_SEPARATORS;
		const chunks = Array.isArray(values) ? values : [values];
		const tokens = [];

		chunks.forEach((chunk) => {
			const text = String(chunk === null || chunk === undefined ? "" : chunk);

			if ((options || {}).split) {
				// Decoded first, split second: what a spreadsheet's clipboard carries is a table and
				// is read column by column, and what a list carries is one text the separators below
				// take apart. Splitting first would flatten the table and lose which row each cell's
				// command had named.
				smartprocess_logistics
					.decode_intake_values(text)
					.forEach((decoded) =>
						smartprocess_logistics
							.split_tokens(decoded, separators)
							.forEach((token) => tokens.push(token))
					);

				return;
			}

			// One value, taken whole: a scanner's label is one label whatever characters are in it.
			const whole = text.trim();

			if (whole) {
				tokens.push(whole);
			}
		});

		const report = { staged: 0, duplicates: [], invalid: [], commands: 0, columns: [] };
		let current = start_row_key;

		tokens.forEach((token) => {
			const command = smartprocess_logistics.read_command(token, state.next_row_command);

			if (command && command.kind === "next") {
				current = move_to_next_column(current) || current;
				report.commands += 1;

				return;
			}

			if (command && command.kind === "row") {
				const target = column_for(command.index);

				if (target) {
					current = target;
					state.active = target;
					report.commands += 1;

					return;
				}

				// A row of this invoice that is not a serial row is a different answer from a row
				// that is not on the invoice at all, and the operator is looking at the invoice, so
				// the window says which one it is. Neither answer moves the cursor: the labels after
				// this command belong to the row that was named, and to no other.
				const hidden = ineligible_at(command.index);

				report.invalid.push({
					serial: token,
					reason: hidden
						? __("ردیف {0} ({1}) برای ثبت سریال فعال نیست.", [
								command.index,
								hidden.model || hidden.item_code || "",
							])
						: __("ردیف {0} در این فاکتور نیست.", [command.index]),
				});

				return;
			}

			const outcome = stage(token, current);

			if (outcome.ok) {
				report.staged += 1;

				if (report.columns.indexOf(current) < 0) {
					report.columns.push(current);
				}

				return;
			}

			if (outcome.duplicate) {
				report.duplicates.push(outcome.serial);

				return;
			}

			report.invalid.push({ serial: outcome.serial, reason: outcome.reason });
		});

		report.columns.forEach((row_key) => redraw_column(row_key));
		refresh_save_button();
		restore_focus(current);

		return report;
	};

	/** The column showing this invoice row number, or nothing. `*N` and `#N` name the `#N` shown. */
	const column_for = function (row_index) {
		const found = ((state.status || {}).rows || []).find(
			(row) => Number(row.row_index) === Number(row_index)
		);

		return found ? found.row_key : null;
	};

	/**
	 * The row this command named, whether or not it is one this window can register on.
	 *
	 * `column_for` answers `null` for both "no such row" and "that row is not a serial row", and
	 * the two need different sentences: a command naming a row that **exists** and cannot take
	 * serials must say so, because the operator can see that row on the invoice in front of them.
	 * What must never happen in either case is the command being obeyed somewhere else — the
	 * labels after it belong to the row the operator named, and to nothing else.
	 */
	const ineligible_at = function (row_index) {
		return ((state.status || {}).ineligible_rows || []).find(
			(row) => Number(row.row_index) === Number(row_index)
		);
	};

	/**
	 * The invoice's first item row: where a file's values go when nothing has said otherwise.
	 *
	 * **Only a file starts here**, and only because a file has no column of its own to have been
	 * dropped into: the picker is in the window's footer, so a workbook that carries no command at
	 * all has nowhere else to begin. Everything an operator aims at a column — a scanned label, a
	 * typed label, a paste — begins in that column, which is what clicking it said. `*N` and the
	 * next-column command move the cursor from wherever it started.
	 */
	const first_row_key = function () {
		const first = ((state.status || {}).rows || [])[0];

		return first ? first.row_key : null;
	};

	/**
	 * What an intake did, in the voice of the place it came from.
	 *
	 * A single label says so next to its own column, because that is where the operator is looking
	 * and it is one line. A list says so in the window's warning line as a count with the reasons,
	 * because a pasted or imported list can refuse twenty labels and twenty lines next to one column
	 * would be unreadable — and because the ones that were refused are still theirs to fix.
	 */
	const report_intake = function (report, row_key, options) {
		const single = Boolean((options || {}).single);
		const one = report.duplicates.length + report.invalid.length === 1;

		if (!report.invalid.length && !report.duplicates.length) {
			if (report.staged && row_key) {
				flash(row_key, __("ذخیره نشده"), true);
		}

			return;
		}

		// One label, one sentence: what the operator scanned, and why it was not taken. A list
		// is a count with the reasons, because twenty lines beside one column is not a message.
		if (single && one) {
			warn(report.duplicates.length ? report.duplicates[0] && smartprocess_logistics.duplicate_warning(report.duplicates[0]) : report.invalid[0].reason);

			return;
		}
		if (!report.invalid.length && !report.duplicates.length) {
			if (report.staged && row_key) {
				flash(row_key, __("ذخیره نشده"), true);
			}

			return;
		}

		const lines = [];

		if (report.duplicates.length) {
			lines.push(
				__("{0} سریال تکراری نادیده گرفته شد: {1}", [
					report.duplicates.length,
					report.duplicates.slice(0, 3).join("، "),
				])
			);
		}

		report.invalid.slice(0, 3).forEach((entry) => {
			lines.push(`${entry.serial}: ${entry.reason}`);
		});

		if (report.invalid.length > 3) {
			lines.push(__("و {0} مورد دیگر نامعتبر", [report.invalid.length - 3]));
		}

		warn(lines.join(" — "));
	};

	/**
	 * Take one scanned label, and write nothing.
	 *
	 * This is the whole of the scan path: one value in, one label staged, the column redrawn and the
	 * box emptied so the next label needs no click. No request, no busy state, no server answer —
	 * which is what makes an over-quantity scan as ordinary as any other, and what keeps a fast
	 * scanner from outrunning the server.
	 */
	const add_pending = function (row_key, serial) {
		state.active = row_key;

		report_intake(intake([serial], row_key, { split: false }), row_key, { single: true });
	};

	/** Drop one waiting label, by the identity it was given when it was scanned. */
	const drop_pending = function (row_key, id) {
		const waiting = state.pending[row_key] || [];
		const at = waiting.findIndex((entry) => String(entry.id) === String(id));

		if (at >= 0) {
			waiting.splice(at, 1);
		}

		redraw_column(row_key);
		refresh_save_button();
	};

	/**
	 * Drop several waiting labels at once, and redraw once.
	 *
	 * The batch that was just written leaves the list together, so the column is drawn once with
	 * all of them gone rather than once per label — which, for a column of three hundred, is the
	 * difference between a redraw and three hundred of them at the end of a save.
	 */
	const drop_pending_many = function (row_key, ids) {
		const wanted = new Set(ids.map((id) => String(id)));
		const waiting = state.pending[row_key] || [];

		for (let index = waiting.length - 1; index >= 0; index -= 1) {
			if (wanted.has(String(waiting[index].id))) {
				waiting.splice(index, 1);
			}
		}

		redraw_column(row_key);
		refresh_save_button();
	};

	/**
	 * Write one column's waiting labels, in the order they were scanned, in **one** request.
	 *
	 * `add_row_serials` appends a row's labels in a single transaction, so a column is one request
	 * whatever the scanner produced. That is the difference between a save an operator can watch
	 * and one that queues twenty round trips behind a disabled button — and it is the difference
	 * between one answer for one column and twenty answers that can disagree with each other.
	 *
	 * The endpoint **appends**: nothing another operator registered on this row is replaced, which
	 * is why this is not the whole-row write — a station whose promise is "my scans are added" must
	 * not delete work it cannot see.
	 *
	 * A label leaves the waiting list only once the server has answered with the row it belongs to,
	 * so a retry sends what is left and never a duplicate. The refusal is whole-column, because the
	 * write is: when the server refuses, nothing of that column was written and every one of its
	 * labels stays waiting and stays reported. Saying "saved" for a column the server refused is
	 * the report this window must never give.
	 */
	const persist_row = function (row_key, waiting) {


		if (!waiting.length) {
			return Promise.resolve([]);
		}

		return smartprocess_logistics
			.enqueue(state.queues, row_key, () =>
				with_busy(row_key, () =>
					smartprocess_logistics
						.call("add_row_serials", {
							sales_invoice: frm.doc.name,
							sales_invoice_item: row_key,
							serials: JSON.stringify(waiting.map((entry) => entry.serial)),
						})
						.then((answer) => {
							state.dirty = true;

							if (answer && answer.row) {
								remember_row(answer.row);
								state.status.summary = answer.summary || state.status.summary;
							}

							// The labels leave the waiting list *before* the redraw, so the column is
							// drawn from the row the server just confirmed rather than from a list
							// that still holds what was written. Only the labels that were sent:
							// the operator may have scanned more while this request was in flight,
							// and those were never part of it.
							drop_pending_many(
								row_key,
								waiting.map((entry) => entry.id)
							);

							if (answer && answer.row) {
								draw_row(answer.row, true);
							}

							return waiting.map((entry) => ({
								row_key,
								serial: entry.serial,
								ok: true,
							}));
						})
						.catch((error) => {
							const kind = smartprocess_logistics.classify_failure(error);
							const reason = smartprocess_logistics.failure_text(error);

							return waiting.map((entry) => ({
								row_key,
								serial: entry.serial,
								ok: false,
								kind,
								reason,
							}));
						})
				)
			);
	};

	/**
	 * Save every waiting scan: check the whole batch first, then write it column by column.
	 *
	 * The check comes before the first request, on purpose. A batch that would put *any* row over
	 * its quantity is refused whole — nothing is sent at all — so a save can never half-succeed
	 * because one column was too full, and the operator's correction is one delete rather than a
	 * repair. The server's own limit is untouched and still decides: if another operator filled the
	 * row between this check and the request, the refusal arrives per label and is reported.
	 */
	const save_pending = function () {
		if (state.saving) {
			return;
		}

		const blocked = [];
		const to_save = [];

		Object.keys(state.pending).forEach((row_key) => {
			const waiting = state.pending[row_key] || [];
			const row = row_of(row_key);

			if (!waiting.length || !row) {
				return;
			}

			const counts = smartprocess_logistics.pending_counts(row, waiting.length);

			if (counts.excess) {
				blocked.push({ model: row.model || row.item_code, excess: counts.excess });
			} else {
				to_save.push(row_key);
			}
		});

		if (blocked.length) {
			const message = smartprocess_logistics.save_blocked_message(blocked);

			warn(message.replace(/<br\s*\/?>/g, " — "));
			frappe.msgprint({
				indicator: "red",
				message: message,
				title: __("ذخیره سریال‌ها"),
			});

			return;
		}

		if (!to_save.length) {
			return;
		}

		// Snapshot first, and synchronously: what a column holds at the moment Save is pressed is
		// what that save writes. A scan arriving while the request is in flight belongs to the next
		// save, which is also what makes `Promise.resolve().then(...)` below safe to use.
		const batches = to_save.map((row_key) => ({
			row_key: row_key,
			waiting: (state.pending[row_key] || []).slice(),
		}));
		state.saving = true;
		refresh_save_button();
		refresh_progress();

		const outcomes = [];
		const step = (index) => {
			if (index >= to_save.length) {
				return Promise.resolve(outcomes);
			}

			return persist_row(batches[index].row_key, batches[index].waiting).then((row_outcomes) => {
				outcomes.push(...row_outcomes);

				return step(index + 1);
			});
		};

		// `Promise.resolve().then(() => step(0))` and not `step(0).then(...)`: the promise the
		// `.finally` below hangs off has to exist *before* any of the work runs, or a throw from
		// inside the first step — however unlikely — leaves `state.saving` true for ever and the
		// Save button disabled with it. Same shape as `with_busy`, for the same reason.
		return Promise.resolve()
			.then(() => step(0))
			.then((all) => {
				const saved = all.filter((outcome) => outcome.ok).length;
				const failed = all.filter((outcome) => !outcome.ok);

				if (!failed.length) {
					frappe.show_alert(
						{ indicator: "green", message: __("{0} سریال ذخیره شد.", [saved]) },
						4
					);

					return;
				}

				// A save that wrote some of the batch says exactly that. "Saved" when part of it was
				// refused is the one report this window must never give: the operator would close it
				// believing the invoice holds labels it does not.
				//
				// One line per refused *column*, not per label: a column is written in one request
				// and refused whole, so twenty labels refused for the same reason are one problem
				// with twenty names, not twenty problems. The column is named by its product, which
				// is what the operator reads above the box.
				const grouped = [];
				const by_column = {};

				failed.forEach((outcome) => {
					const key = `${outcome.row_key}\u0000${outcome.reason || ""}`;

					if (by_column[key]) {
						by_column[key].serials.push(outcome.serial);

						return;
					}

					const line = {
						row_key: outcome.row_key,
						serials: [outcome.serial],
						reason: outcome.reason,
					};

					by_column[key] = line;
					grouped.push(line);
				});

				const lines = grouped.map((line) => {
					const row = row_of(line.row_key);
					const column = row ? row.model || row.item_code : line.row_key;

					return `${smartprocess_logistics.escape(
						column
					)} — ${smartprocess_logistics.escape(
						line.serials.join("، ")
					)}: ${smartprocess_logistics.escape(line.reason || __("دلیل نامشخص"))}`;
				});

				frappe.msgprint({
					indicator: "red",
					title: __("ذخیره سریال‌ها"),
					message: `${__(
						"ذخیره انجام نشد. سریال‌های ذخیره‌نشده در پنجره باقی ماندند."
					)}<br>${__("ذخیره‌شده: {0} — ذخیره‌نشده: {1}", [
						saved,
						pending_total(),
					])}<br>${lines.join("<br>")}`,
				});
			})
			.finally(() => {
				state.saving = false;
				refresh_save_button();
				refresh_progress();

				// Every column is redrawn from the rows the server last confirmed and from what is
				// still waiting, so a batch that stopped halfway leaves the screen showing exactly
				// which labels were written and which were not.
				dialog.$wrapper.find(".sp-scan-column").each((index, node) => {
					redraw_column($(node).attr("data-row-key"));
				});
			});
	};

	/** Stop waiting to file a burst in this column — it has been filed, or refused. */
	const stop_settling = function (row_key) {
		window.clearTimeout(state.settle[row_key]);
		delete state.settle[row_key];
		delete state.burst[row_key];
	};

	/**
	 * One keystroke of a burst, and the timer that files it if nothing else ever does.
	 *
	 * Called from the `input` event, which is what a scanner's keystrokes produce whether it ends
	 * them or not. The gap check starts a new burst when the previous keystroke is already too old
	 * to belong to the same one, so a label typed after a pause is measured on its own.
	 */
	const note_keystroke = function (row_key) {
		const now = window.performance.now();
		const burst = state.burst[row_key] || (state.burst[row_key] = { started: now, last: now, length: 0 });

		if (now - burst.last > smartprocess_logistics.WEDGE_BURST_MS) {
			burst.started = now;
		}

		burst.last = now;
		burst.length += 1;

		window.clearTimeout(state.settle[row_key]);
		state.settle[row_key] = window.setTimeout(() => {
			delete state.settle[row_key];

			const input = input_of(row_key);

			if (!smartprocess_logistics.is_wedge_burst(state.burst[row_key])) {
				return;
			}

			if (input.length && (input.val() || "").trim()) {
				submit(input);
			}
		}, smartprocess_logistics.WEDGE_SETTLE_MS);
	};

	const submit = function (input) {
		const row_key = input.attr("data-row-key");
		const serial = (input.val() || "").trim();

		if (!serial) {
			return;
		}

		// Emptied here, at the boundary, before anything is decided — this is the fault an
		// operator reported. A label left in the box is a label the next scan is appended to, so
		// every path out of this function leaves the box empty: the value has been taken, and no
		// later event can take it again because there is nothing left in the box to take. The
		// marker that used to guard against a second event carrying the same text is gone with it;
		// an empty box cannot send its own value twice, and a value that never leaves the box is
		// one no second event can carry.
		input.val("");
		input.removeData("submitted");
		stop_settling(row_key);

		// One value, taken whole: a scanner's label is one label whatever characters are in it,
		// which is the difference between this call and the one the paste handler makes.
		return add_pending(row_key, serial);
	};

	/**
	 * A paste is a list, so it is split and staged the way a list is.
	 *
	 * The clipboard is the one input that is not a keyboard: what lands in it comes from a
	 * spreadsheet, a text file or a column of cells, and a spreadsheet's cell boundary arrives as a
	 * tab or a newline whether the operator asked for it or not. A paste of one value is left to the
	 * browser — that is a serial somebody copied and is about to look at before pressing Enter — and
	 * a paste of more is taken over and spread across the columns: a token that is a command moves
	 * the cursor, a token that is a serial is staged where the cursor is, and everything refused is
	 * named without stopping the rest.
	 *
	 * A paste that is a **table** keeps its shape: `intake` decodes it first and reads it column by
	 * column, exactly as the same cells would be read from a workbook, so a command written at the
	 * head of a column rules that column and no other. A range copied out of a spreadsheet is the
	 * case this is for; a tab-separated *list* on one line is read as a list, because a shape has to
	 * span rows to be a shape.
	 *
	 * **The field it was pasted into is where its serials begin.** Clicking the input of invoice row
	 * 3 and pasting a list is an operator saying "these belong here"; starting such a paste at the
	 * invoice's first row sent every one of them to the wrong product, and a list that carried no
	 * command had nothing to correct it with. The field's own row is the starting row, and a command
	 * in the pasted text still moves it from there for everything that follows — `*2` in a list
	 * pasted into row 3 sends the rest to row 2 and leaves the beginning where it was.
	 *
	 * The default is prevented only for the list, because that is the case where the browser's own
	 * paste would put thirty serials in one box.
	 */
	dialog.fields_dict.body.$wrapper.on("paste", ".sp-scan-input", function (event) {
		const clipboard = event.originalEvent && event.originalEvent.clipboardData;
		const text = clipboard ? clipboard.getData("text") : "";
		const row_key = $(this).attr("data-row-key");
		const tokens = smartprocess_logistics.split_tokens(
			text,
			state.intake && state.intake.delimiters && state.intake.delimiters.length
				? state.intake.delimiters
				: smartprocess_logistics.FALLBACK_SEPARATORS
		);

		if (tokens.length < 2) {
			return; // One value: the browser's paste, the operator's eyes, then Enter.
		}

		event.preventDefault();

		// The column the paste arrived in, and only failing that the invoice's own first row — a
		// field always carries its row key, so the fallback is a guard and not a rule.
		const report = intake([text], row_key || first_row_key(), { split: true });

		report_intake(report, null);

		if (report.staged) {
			flash(
				report.columns[0] || row_key,
				__("{0} سریال در ستون‌ها قرار گرفت", [report.staged]),
				true
			);
		}
	});

	/**
	 * Move the cursor to the next column, leaving everything already in flight exactly as it is.
	 *
	 * Nothing here touches the queue, the counters or any pending write. A label scanned into this
	 * column a moment ago is already bound to **this column's** row key — it was given that key
	 * when it was scanned, not when it is written — so moving on cannot re-aim it at another row,
	 * and it still lands in the right one however long the server takes to answer.
	 *
	 * What the move does change is `state.active`, and deliberately: that is the field every late
	 * answer consults before putting the cursor back (`restore_focus`), so a write that finishes
	 * after the operator has moved on returns the cursor to where the operator is now, not to the
	 * column they left. The box being left is emptied and unmarked first, because leaving it fires
	 * its own `blur` and a box still holding the command would send it a second time.
	 */
	const move_to_next_column = function (row_key) {
		const target = smartprocess_logistics.next_row_key(visible_row_keys(), row_key);

		if (!target) {
			// A window with no columns at all: the scan is dropped as it was before the command
			// existed, rather than held somewhere it could never be sent from.
			return null;
		}

		state.active = target;

		input_of(target).trigger("focus");
		flash(target, __("ستون بعدی"), true);

		// Returned so the interpreter knows where the cursor ended up: a next-column command in the
		// middle of a pasted list moves the column the *rest of that list* belongs to, which is the
		// whole point of a command being in a list at all.
		return target;
	};

	const remove_assignments = function (names, row_key) {
		if (!names.length) {
			return Promise.resolve();
		}

		return with_busy(row_key, () =>
			smartprocess_logistics
				.call("delete_assignments", { names: names })
				.then(() => {
					state.dirty = true;

					// The counters come from the server, not from arithmetic done here: a change
					// another user made meanwhile is drawn by the same read.
					return load_status(true);
				})
				.catch(() => {
					// The refusal is already on screen; the column stays as the server last said.
					restore_focus(row_key);

					return null;
				})
		);
	};

	// --- events -------------------------------------------------------------------------

	dialog.fields_dict.body.$wrapper.on("keydown", ".sp-scan-input", function (event) {
		// An Enter that ends an IME composition is the input method committing a word, not a
		// scanner ending a label: taking it files half a serial and empties the box the operator is
		// still typing into. Measured, not assumed: jQuery builds its own event object for a
		// delegated handler and copies a fixed list of properties into it, and `isComposing` is not
		// on that list — so `event.isComposing` is `undefined` here however true it was on the
		// event the browser dispatched. The original is where the fact lives.
		// `keyCode === 229` is the shape the same keypress takes where `isComposing` is not
		// reported at all, and jQuery does copy `keyCode`.
		const composing = event.originalEvent ? event.originalEvent.isComposing : event.isComposing;

		if (composing || event.keyCode === 229) {
			return;
		}

		// A barcode scanner is a keyboard that ends every label — with Enter on most of them, with
		// Tab on some. `scanner_suffix` decides which keys that is; nothing else is intercepted, so
		// typing and pasting behave the way they do everywhere else.
		const action = smartprocess_logistics.scanner_suffix(event.key, $(this).val());

		if (action !== "submit") {
			return; // Including an empty box's Tab, which is the operator moving to another column.
		}

		// Keeping the focus here is the point. A Tab that was allowed through would move the cursor
		// to the next control and the next label would be typed into that control instead.
		event.preventDefault();

		submit($(this));
	});

	dialog.fields_dict.body.$wrapper.on("input", ".sp-scan-input", function () {
		// Every keystroke, whether the scanner ends the label or not — this is the event a wedge
		// produces, and the one that decides whether a burst is filed on its own. See
		// `is_wedge_burst`: a burst typed at a scanner's speed that nobody ended is filed after a
		// short pause, and a burst a person typed is left alone however long they take.
		note_keystroke($(this).attr("data-row-key"));
	});

	dialog.fields_dict.body.$wrapper.on("focusin", ".sp-scan-input", function () {
		// The operator's own choice of column, recorded so that a redraw, a refresh or a write
		// finishing late returns the cursor here rather than to the first column of the invoice.
		state.active = $(this).attr("data-row-key");
	});

	dialog.fields_dict.body.$wrapper.on("blur", ".sp-scan-input", function () {
		// A scanner configured with Tab instead of Enter, and an operator clicking away, both end
		// here. `submitted` keeps one label from being registered twice.
		submit($(this));
	});

	/**
	 * One thing at a time in this window, and the flag that says so cannot be left behind.
	 *
	 * `refresh_progress` shows «در حال ثبت…» while anything is in flight, so a flag that is set and
	 * then never cleared is a window that looks busy for ever — and the way that happened was a
	 * call site setting the flag and then failing *before* the promise that would clear it existed.
	 * Here the promise comes first: `Promise.resolve().then(work)` exists whatever `work` does,
	 * `.finally` always runs, and every caller gets the same guarantee without having to remember
	 * it. Nothing in this window sets a busy flag any other way.
	 */
	const run_exclusive = function (work) {
		if (state.busy.preview) {
			return Promise.resolve(null);
		}

		state.busy.preview = true;
		refresh_progress();

		return Promise.resolve()
			.then(work)
			.catch(() => null)
			.finally(() => {
				delete state.busy.preview;
				refresh_progress();
			});
	};

	/**
	 * The file the operator chose, read and handed to the preview.
	 *
	 * Read once, on the click that asked for it: a File input is not something to read twice, and
	 * nothing here needs the bytes after this request.
	 */
	const read_file_choice = function () {
		const input = dialog.fields_dict.body.$wrapper.find(".sp-logistics-file")[0];
		const file = input && input.files && input.files[0];

		if (!file) {
			return Promise.resolve(null);
		}

		state.import_file = file;

		return smartprocess_logistics.read_file(file);
	};

	/**
	 * A file into the columns, through the same staging every other input goes through.
	 *
	 * The server reads the file — only it can read an `.xlsx` — and answers with the serials it
	 * holds, the invoice row each belongs to, and what is wrong with the ones that are wrong. Those
	 * serials are then staged exactly as scanned ones are, so they appear in the same columns, count
	 * the same way, are marked red when a row has no room, and are written by the same
	 * «ذخیره سریال‌ها» and by nothing else. A file import is therefore a way of filling the columns,
	 * not a second way of registering serials: there is no confirm step, no session to spend, and
	 * nothing is written to the database until the operator saves.
	 *
	 * What the server answers with is the file's **values** — a workbook's nonempty cells column by
	 * column, or a text file's whole text — and nothing else has been decided about them. They go
	 * into the same `intake` a paste goes into, in the order they arrived, which is what makes the
	 * commands in a workbook's first row select the rows for the columns beneath them. A text
	 * file's whole text is decoded on the way in exactly as a pasted text is, so a tab-separated
	 * table written to a `.txt` is read column by column like the workbook it came from.
	 */
	const run_preview = function (content) {
		if (!content) {
			frappe.msgprint(__("فایلی انتخاب نشده است."));

			return Promise.resolve();
		}

		return run_exclusive(() => {
			begin_import();

			// The request is made *inside* a `then`, not before one: a call that fails before it ever
			// returns a promise — which is what `frappe.call` does when it throws on the way out — would
			// otherwise skip everything chained to it, leaving the window saying it is still working and
			// its button disabled for as long as it stays open.
			return Promise.resolve()
				.then(() =>
					smartprocess_logistics.call("prepare_import_preview", {
						sales_invoice: frm.doc.name,
						content: content,
						filename: (state.import_file || {}).name,
					})
				)
				.then((answer) => stage_values(answer))
				.catch((error) => failed_import(error))
				.finally(() => end_import());
		});
	};

	/**
	 * What the window says and does while a file is being read, and how it puts itself back.
	 *
	 * A file is the one input that cannot be answered from the screen: its bytes are read, sent, and
	 * parsed on the server, and a workbook of any size takes long enough that an operator presses the
	 * button again. So while the request is out the notice stands in the place the result will appear
	 * — unmistakable, and where they are already looking — and the control and the picker are taken
	 * out of reach so a second press cannot queue a second read behind the first.
	 *
	 * Everything put back is put back by `end_import`, called from a `finally` and therefore after a
	 * failure as well as after a success. Nothing here writes, and the columns are not touched: the
	 * notice replaces the *previous* receipt until there is a new one to draw.
	 */
	const begin_import = function () {
		dialog.fields_dict.body.$wrapper
			.find(".sp-scan-import")
			.addClass("is-busy")
			.find("[data-action='preview'], .sp-logistics-file")
			.prop("disabled", true);

		dialog.fields_dict.body.$wrapper.find(".sp-scan-preview").html(
			`<div class="sp-import-progress" role="status">${__(
				"لطفاً منتظر بمانید؛ فایل در حال پردازش است…"
			)}</div>`
		);
	};

	const end_import = function () {
		dialog.fields_dict.body.$wrapper
			.find(".sp-scan-import")
			.removeClass("is-busy")
			.find("[data-action='preview'], .sp-logistics-file")
			.prop("disabled", false);
	};

	/**
	 * Why a file could not be read, in the place its result would have been.
	 *
	 * The refused request has already been answered by the call wrapper, which shows the server's
	 * own sentence — a refused workbook carries its reason in Persian. What this adds is the window
	 * saying that *this* was a file read and that it is over, so the notice is not left standing and
	 * the operator knows the next press will try again.
	 */
	const failed_import = function (error) {
		const reason = smartprocess_logistics.failure_text(error);

		dialog.fields_dict.body.$wrapper.find(".sp-scan-preview").html(
			`<div class="sp-logistics-issues"><div class="sp-issue is-error"><div>${__(
				"خواندن فایل انجام نشد."
			)}${reason ? ` ${smartprocess_logistics.escape(reason)}` : ""}</div></div></div>`
		);
	};

	/** Read a file's values into the columns, and say what became of them. */
	const stage_values = function (answer) {
		const values = (answer && answer.values) || [];
		const report = intake(values, first_row_key(), { split: true });

		// What the reader noticed about the file rather than about any one serial: cells Excel had
		// already stored as numbers, so a leading zero in one of them is gone before this app sees
		// it, and which sheets were read. Named as warnings, because they are facts about the file.
		const warnings = [];

		if (answer && answer.numeric_cells) {
			warnings.push({
				message: __(
					"{0} سلول به‌صورت عدد ذخیره شده بود؛ صفر ابتدایی آن‌ها قابل بازیابی نیست.",
					[answer.numeric_cells]
				),
			});
		}

		draw_preview(report, { values: values.length, sheets: (answer && answer.sheets) || [] }, warnings);

		return report;
	};

	/**
	 * What a file turned into, on one screen: the counts, the refusals, and what to do next.
	 *
	 * The serials are already in the columns by the time this is drawn, so this is a receipt and
	 * not a decision — which is why it holds no confirm action and no preview table: the columns
	 * themselves show where every serial went, how many each row still needs, and which are red.
	 */
	const draw_preview = function (report, file, warnings) {
		const chips = [
			`<span class="sp-logistics-chip">${__("در ستون‌ها قرار گرفت")}: ${report.staged}</span>`,
			`<span class="sp-logistics-chip">${__("مقدارهای فایل")}: ${(file || {}).values || 0}</span>`,
		];

		if (report.duplicates.length) {
			chips.push(
				`<span class="sp-logistics-chip is-short">${__("تکراری")}: ${
					report.duplicates.length
				}</span>`
			);
		}

		if (report.invalid.length) {
			chips.push(
				`<span class="sp-logistics-chip is-short">${__("نامعتبر")}: ${
					report.invalid.length
				}</span>`
			);
		}

		const refusals = report.invalid
			.slice(0, 20)
			.map(
				(entry) =>
					`<div class="sp-issue is-error"><div>${smartprocess_logistics.escape(
						entry.serial
					)}: ${smartprocess_logistics.escape(entry.reason)}</div></div>`
			)
			.join("");

		const more =
			report.invalid.length > 20
				? `<div class="sp-issue-code">${__("و {0} مورد دیگر", [report.invalid.length - 20])}</div>`
				: "";

		const next_step = report.staged
			? `<div class="sp-preview-note">${__(
					"برای ثبت، «ذخیره سریال‌ها» را بزنید."
				)}</div>`
			: "";

		dialog.fields_dict.body.$wrapper.find(".sp-scan-preview").html(`
			<div class="sp-logistics-summary">${chips.join("")}</div>
			${next_step}
			${
				refusals || more
					? `<div class="sp-logistics-issues">${refusals}${more}</div>`
					: ""
			}
			${
				(warnings || []).length
					? `<div class="sp-logistics-issues">${warnings
							.map(
								(issue) =>
									`<div class="sp-issue is-warning"><div>${smartprocess_logistics.escape(
										issue.message
									)}</div></div>`
							)
							.join("")}</div>`
					: ""
			}`);

		if (report.staged && report.columns.length) {
			flash(
				report.columns[0],
				__("{0} سریال در ستون‌ها قرار گرفت", [report.staged]),
				true
			);
		}
	};

	// The first gesture is the one a browser accepts as permission to make a sound.
	dialog.fields_dict.body.$wrapper.on("keydown", () => smartprocess_logistics.unlock_audio());
	dialog.fields_dict.body.$wrapper.on("click", () => smartprocess_logistics.unlock_audio());

	dialog.fields_dict.body.$wrapper.on("change", ".sp-scan-sound-toggle", function () {
		smartprocess_logistics.set_sound_enabled($(this).prop("checked"));
		smartprocess_logistics.unlock_audio();
	});

	dialog.fields_dict.body.$wrapper.on("click", "[data-action]", function () {
		const action = $(this).attr("data-action");

		if (action === "refresh") {
			load_status(false);
		}

		if (action === "delete-one") {
			const name = $(this).attr("data-assignment");
			const row_key = $(this).closest(".sp-scan-column").attr("data-row-key");

			remove_assignments([name], row_key);
		}

		if (action === "delete-pending") {
			drop_pending(
				$(this).closest(".sp-scan-column").attr("data-row-key"),
				$(this).attr("data-pending")
			);

			return;
		}

		if (action === "save-pending") {
			save_pending();

			return;
		}

		if (action === "clear-column") {
			const column = $(this).closest(".sp-scan-column");
			const row_key = column.attr("data-row-key");
			const row = row_of(row_key);
			const names = ((row || {}).assignments || []).map((entry) => entry.name);
			const waiting = (state.pending[row_key] || []).length;

			if (!names.length && !waiting) {
				frappe.msgprint(__("این ستون سریالی ندارد."));

				return;
			}

			// Removing a whole column is the one action here that cannot be undone one label at a
			// time, so it is asked about — naming the row, not just "are you sure" — and it says
			// which of the two kinds it is about to remove, because an unsaved scan is deleted on
			// this screen and a saved one is deleted on the server.
			frappe.confirm(
				names.length && waiting
					? __("همهٔ {0} سریال ثبت‌شده و {1} سریال ذخیره‌نشدهٔ ستون {2} حذف شوند؟", [
							names.length,
							waiting,
							(row || {}).model || (row || {}).item_code || "",
						])
					: waiting
						? __("همهٔ {0} سریال ذخیره‌نشدهٔ ستون {1} حذف شوند؟", [
								waiting,
								(row || {}).model || (row || {}).item_code || "",
							])
						: __("همهٔ {0} سریال ستون {1} حذف شوند؟", [
								names.length,
								(row || {}).model || (row || {}).item_code || "",
							]),
				() => {
					state.pending[row_key] = [];
					redraw_column(row_key);
					refresh_save_button();

					if (names.length) {
						remove_assignments(names, row_key);
					}
				}
			);
		}

		if (action === "preview") {
			// The bytes are read by the browser before anything is asked of the server, and a file it
			// cannot read fails here rather than there. Both failures end up in the same place: the
			// window says what went wrong, and the button comes back.
			read_file_choice()
				.then(run_preview)
				.catch((error) => failed_import(error));
		}
	});

	// --- opening and closing -------------------------------------------------------------

	const close = function () {
		const waiting = pending_total();

		// Scans that have not been saved exist only in this window. Closing it silently would throw
		// away work an operator did with a scanner, one label at a time, and they would have no way
		// of knowing it never reached the invoice — so the window asks, and says how many labels
		// are at stake. Nothing is written and nothing is kept: closing is still closing.
		if (waiting) {
			frappe.confirm(
				__("همهٔ {0} سریالِ ذخیره‌نشده از بین می‌روند. پنجره بسته شود؟", [waiting]),
				() => {
					state.pending = {};
					finish();
				}
			);

			return;
		}

		finish();
	};

	const finish = function () {
		dialog.hide();

		// One reload, at the end, and only if something was written: the invoice itself never
		// changes here, and reloading the form after every label would fight the scanner for focus.
		if (state.dirty) {
			frm.reload_doc();
		}
	};

	// The window opens even when the first read failed, and says so. An operator who sees why can
	// try again; an empty dialog with nothing in it looks like an invoice that has no rows.
	load_status(false)
		.catch(() => {
			dialog.fields_dict.body.$wrapper.html(`
				<div class="sp-logistics-empty">
					<div>${__("خواندن وضعیت سریال‌ها ناموفق بود.")}</div>
					<button class="btn btn-xs btn-default" data-action="refresh">
						${__("تلاش دوباره")}
					</button>
				</div>
			`);
		})
		.then(() => dialog.show());
};

// --- the invoice serial details dialog -------------------------------------------------

/** A read-only view of an invoice's registrations, for a user who may only look. */
smartprocess_logistics.open_details_dialog = function (frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("سریال‌های {0}", [frm.doc.name]),
		size: "large",
		fields: [{ fieldname: "body", fieldtype: "HTML" }],
		primary_action_label: __("بستن"),
		primary_action() {
			dialog.hide();
		},
	});

	dialog.$wrapper.addClass("sp-logistics-dialog");

	const can_delete = smartprocess_logistics.allowed(smartprocess_logistics.ROLE.manager);

	const draw = function (status, page) {
		const rows = (page && page.assignments) || [];

		const table = rows.length
			? `<table class="table table-bordered">
					<thead><tr>
						<th>${__("کالا")}</th>
						<th>${__("سریال")}</th>
						${can_delete ? `<th>${__("حذف")}</th>` : ""}
					</tr></thead>
					<tbody>${rows
						.map(
							(row) => `<tr>
								<td>${smartprocess_logistics.escape(row.item_code)}</td>
								<td>${smartprocess_logistics.escape(
									row.serial_no || row.normalized_serial
								)}</td>
								${
									can_delete
										? `<td><input type="checkbox" class="sp-delete" value="${smartprocess_logistics.escape(
												row.name
											)}"></td>`
										: ""
								}
							</tr>`
						)
						.join("")}</tbody>
				</table>`
			: `<div class="sp-logistics-empty">${__("سریالی ثبت نشده است.")}</div>`;

		dialog.fields_dict.body.$wrapper.html(`
			<div class="sp-logistics-toolbar">
				${smartprocess_logistics.summary_html(status && status.summary)}
				${
					can_delete
						? `<button class="btn btn-xs btn-danger" data-action="delete">
								${__("حذف انتخاب‌شده‌ها")}
							</button>`
						: ""
				}
			</div>
			${table}
		`);
	};

	const load = function () {
		return Promise.all([
			smartprocess_logistics.call("get_invoice_serial_status", {
				sales_invoice: frm.doc.name,
			}),
			smartprocess_logistics.call("get_serial_assignments", {
				sales_invoice: frm.doc.name,
				limit: 500,
			}),
		]).then(([status, page]) => draw(status, page));
	};

	dialog.fields_dict.body.$wrapper.on("click", "[data-action='delete']", function () {
		const names = dialog.fields_dict.body.$wrapper
			.find(".sp-delete:checked")
			.map(function () {
				return $(this).val();
			})
			.get();

		if (!names.length) {
			frappe.msgprint(__("هیچ ارجاعی انتخاب نشده است."));

			return;
		}

		frappe.confirm(__("سریال‌های انتخاب‌شده حذف شوند؟"), () => {
			const button = dialog.fields_dict.body.$wrapper.find("[data-action='delete']");

			button.prop("disabled", true);

			smartprocess_logistics
				.call("delete_assignments", { names: names })
				.then(() => {
					frappe.show_alert({ indicator: "green", message: __("حذف شد.") });
					frm.reload_doc();

					return load();
				})
				.catch(() => null)
				.finally(() => button.prop("disabled", false));
		});
	});

	load()
		.catch(() => null)
		.then(() => dialog.show());
};
