# Test manifest

Every record this app creates while testing is listed here, so cleanup is exact rather than a
guess. Nothing in this file is created automatically.

Rules:

1. Add a row **before** creating the record, so an interrupted run still leaves a trace.
2. Names carry the `SPT-` prefix, which marks a record as test data at a glance.
3. Delete in reverse order of creation. A child row cannot be deleted before its parent, and
   an assignment cannot be deleted before the invoice it points at.
4. Never delete a record this file does not list.
5. Delete only what this app owns. Serial and assignment records are owned by the app;
   Company, Customer, Item and Sales Invoice records belong to the site.

The proof scripts named below were development-environment artifacts. They are recorded as
evidence but are not part of the 0.5.0 release candidate.

| Date | Phase | Site | DocType | Name | Purpose | Created by | Deleted on |
| ---- | ----- | ---- | ------- | ---- | ------- | ---------- | ---------- |
| 2026-10-13 | 4 | SmartProcessERP | Item | `SP-TEST-WARRANTY-ITEM` | A non-stock item carrying `sp_serial_required`, so the report and the button have something eligible | main agent | **kept** |
| 2026-10-13 | 4 | SmartProcessERP | Sales Invoice | `IN-260017` | One submitted invoice with three required serials | main agent | **kept** |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Serial | `LS-2026-00001` … `LS-2026-00016` | Serial identities created by the registration, correction, TXT and XLSX tests | main agent | 2026-10-13 |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Serial | `LS-2026-00017` | The one serial the kept invoice still points at | main agent | **kept** |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Serial Assignment | `LSA-2026-00007` … `LSA-2026-00009` | Assignments on `IN-260017`; the extras were replaced by the correction tests, leaving one | main agent | partly |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Import Session | `8b38ev48hm`, `8bto91828p`, `a90mqv2f0b`, `b8btb303on`, `gt6tctqkmh` | One row per preview of a TXT or XLSX payload | main agent | 2026-10-13 |
| 2026-10-13 | 4 | SmartProcessERP | User | `sp_ui_viewer@` / `sp_ui_editor@` / `sp_ui_manager@` / `sp_ui_none@example.com` | The four principals of the permission matrix; each holds `Accounts User` so the report's own ref-doctype check can be reached | main agent | 2026-10-13 |

| 2026-10-13 | 5 | SmartProcessERP | Sales Invoice | `IN-260018` | A two-row invoice: one row of two, one of one, for the duplicate and partial-import cases | main agent | **kept, cancelled** |
| 2026-10-13 | 5 | SmartProcessERP | Sales Invoice | `IN-260019` | A one-row invoice of three, for the concurrency and rollback cases | main agent | **kept, cancelled** |
| 2026-10-13 | 5 | SmartProcessERP | Logistics Serial | the serials created by the phase-5 cases, `LS-2026-00020` … `LS-2026-00030` among them | One row per serial the tests registered | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | Logistics Import Session | one row per preview, including two raced at once | Preview rows, all applied or expired | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | User | `sp_p5_probe@example.com` | One principal for the live permission matrix, with a Company user permission | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | File | `sp-phase5-xlsx-test.xlsx` in the site's `public/files` | A workbook the browser could fetch, to test the XLSX path through the real dialog | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | Sales Invoice | `IN-260020` | A one-row invoice of two, for the typed-list check through the dialog itself | main agent | **kept, cancelled** |

| 2026-10-13 | 6 | SmartProcessERP | Company | `SP TEST COMPANY` | A second company, created with `create_chart_of_accounts_based_on = Existing Company`, to prove company isolation with two real companies | main agent | **kept** |
| 2026-10-13 | 6 | SmartProcessERP | Sales Invoice | `IN-260021` (draft) | One draft invoice in the second company, with two serials registered by an Editor who could reach only that company | main agent | **kept, draft** |
| 2026-10-13 | 6 | SmartProcessERP | Logistics Serial | the two serials the company-B draft points at | Created by that Editor's confirmed import | main agent | **kept** |
| 2026-10-13 | 6 | SmartProcessERP | User | `sp_p6_company_b@example.com`, `sp_p6_final@example.com` | Editors with a Company user permission for the second company only: one for the isolation matrix, one for the create-only session check | main agent | 2026-10-13 |
| 2026-10-13 | 6 | SmartProcessERP | Logistics Import Session | previews made while testing the sweep, all applied or expired | The cleanup job then expired and removed them | main agent | 2026-10-13 |
| 2026-10-13 | 6 | SmartProcessERP | File | `smartprocess_logistics-uninstall-<timestamp>.json` in `private/files` | Written by calling `install.before_uninstall` on live data, to check the guard without uninstalling | main agent | 2026-10-13 |

| 2026-10-13 | 7 | SmartProcessERP | Sales Invoice | `IN-260021` (draft) | The company-B draft, and the two assignments and serials that pointed at it | main agent | 2026-10-13 |
| 2026-10-13 | 7 | SmartProcessERP | Logistics Serial | five serials nothing pointed at any more | Released by the replacements the phase-6 tests made | main agent | 2026-10-13 |

Two notes on this run.

The rows were written **after** the records were created, which breaks rule 1 and is recorded
here rather than hidden. The names are exact, so cleanup is still a list and not a search.

A user cannot be deleted while anything links to it, and on this site four things do: a
`Raven User` row (created by Raven when the user is created), a `Contact` (created by ERPNext
for the same reason), and the `Has Role` children the app itself added. The `Contact` and the
`Raven User` row had to be deleted first, in that order. The one item, one invoice and one
assignment are **kept on purpose**: they are what makes the Desk button, the dialog and the
report demonstrable on an otherwise empty module, and they are named so that they read as test
data. Remove them in reverse order when they are no longer wanted: the invoice cannot be
deleted while it is submitted, so cancel it first.

`IN-260018` and `IN-260019` were **cancelled rather than deleted**. Cancelling reverses their
accounting entries, so they now affect no balance and no report; deleting them would mean
removing the `GL Entry` rows the framework refuses to delete through, which is a write to
accounting data this app does not own and should not make on a site it is only tested against.
They can be deleted from the Desk by anyone who decides otherwise. `IN-260020` is cancelled for
the same reason, and `IN-260021` is a **draft**, so it has no accounting entries at all.

`SP TEST COMPANY` is left in place on purpose. It exists for one check — that a user restricted
to it cannot reach another company's invoice through any endpoint, while the report shows only
its own rows — and removing a company means removing its chart of accounts and every link to it,
which is a larger operation than the check it would clean up. Delete it from the Desk when it is
no longer wanted; it holds one draft invoice and no transactions.

## Phase 7 — the unified serial intake

The batch endpoint was verified on a real database in the **isolation lab**, not on
`SmartProcessERP`: `deployment/lab/verify_batch_intake.py` ran there, and it writes no row this
table has to track — everything it plants, it removes.

| Date | Phase | Site | DocType | Name | Purpose | Created by | Deleted on |
| ---- | ----- | ---- | ------- | ---- | ------- | ---------- | ---------- |
| 2026-10-14 | 7 | `sp_logistics_lab` | Sales Invoice | `ACC-SINV-2026-00007`, `ACC-SINV-2026-00008` | The batch verifier's draft invoice and its donor (the second invoice an identity is reused onto) | `verify_batch_intake.py arm` | 2026-10-14, by its own `disarm` |
| 2026-10-14 | 7 | `sp_logistics_lab` | Logistics Serial, Logistics Serial Assignment | the `BATCH-` labels and the assignments they were put on, including the ones a lost race created and rolled back | The batch checks: refusal, reuse, quantity, concurrency, retry | `verify_batch_intake.py` | 2026-10-14, by its own `disarm` (14 assignments, 17 serials) |
| 2026-10-14 | 7 | `sp_logistics_lab` | User | `lab-batch-editor@`, `lab-batch-viewer@`, `lab-batch-norole@`, `lab-batch-restricted@example.test` | The four principals of the permission and company checks | `verify_batch_intake.py arm` | 2026-10-14, with their User Permissions |
| 2026-10-14 | 7 | `sp_logistics_lab` | Sales Invoice | `ACC-SINV-2026-00005` | The submitted-then-cancelled invoice the lifecycle check needs; `docstatus 2` is reachable only by cancelling, and deleting it would mean removing the reversed `GL Entry` rows the framework keeps | `verify_batch_intake.py arm` | **kept** — a lab record holding no serials |
| 2026-10-22 | Stock Entry item filter | SmartProcessERP | Item, Stock Entry, User | `SP-SEF-X/Y/Z/ONLY-D/ZERO/V`, `MAT-STE-2026-00116` … `00128`, `sp-sef-tester@`, `sp-sef-restricted@` | Isolated stock in `SP-PH-C - SP`, `SP-PH-D - SP` and `SP-VW-A - SP`, one submitted transfer and one disposable Warehouse-restricted user, to test the source-warehouse Item filter | `stock_entry_item_filter_proof.py` | 2026-10-22, by `cleanup_stock_entry_item_filter.py`; zero Bin rows and no Items remain |
| 2026-10-22 | SRBNB audit | SmartProcessERP | Item, Purchase Invoice, Purchase Receipt, Landed Cost Voucher | `SP-SRBNB-LCV-067734`, `SP-SRBNB-PLAIN-067734`; `BI-260138` … `BI-260140`, `BR-260168` … `BR-260170`, `MAT-LCV-2026-00027` | Two controlled matched 5-of-5 return chains, with and without a 100,000 LCV, to explain the 733,333.33 SRBNB balance | the release-prep audit script | 2026-10-22; every document cancelled and deleted, both Items deleted, zero Bin rows |
| 2026-10-22 | Release-prep rerun | SmartProcessERP | Item, User | `SP-UP-*-352867`, `SP-VW-SALE-857266`, `SP-SEF-*`; `sp-audit-http@`, `sp-audit-browser@`, `sp-audit-item@`, `sp-audit-dev@` | Rehearsed purchase, virtual-sale and Item-filter proofs after the boundary fix, plus disposable principals for the HTTP, Item-user-permission and browser composition checks | main agent | 2026-10-22; documents cancelled, Items and Users deleted, zero Bin rows |
| 2026-10-22 | Commitment isolation | `sp-release-0.5.0` temp tree | — | — | A temporary copy of only the approved files, with the paused commitment modules removed, to prove the release composition imports and registers without them; no site records created | main agent | 2026-10-22; the temp tree was removed after the checks |
| 2026-10-22 | I30 Delivery Note audit | SmartProcessERP | Item, Delivery Note, Product Bundle | `SP-DN-AUDIT-221496`, `SP-DN-AUDIT-212141`, `RG-260037` (draft), `SP-VW-BUNDLE-730714`, `SP-VW-COMP-730714` | An unlinked managed draft Delivery Note refused by both the approved and combined validators; a non-managed physical draft allowed and deleted; the virtual bundle Delivery Note refused and the physical bundle invoice posted and cancelled | main agent | 2026-10-22; drafts and Items deleted, documents cancelled, zero Bin rows |

Nothing was created on `SmartProcessERP`. Its backend was restarted once, under the approval that
names that step, and nothing else on it was touched: `Logistics Serial` 71, `Logistics Serial
Assignment` 37, `Logistics Import Session` 4 and `Sales Invoice` 13 were read before the restart
and again after it, unchanged — the same four numbers the earlier phases left there.

The isolation lab's own backend was restarted as well, because a worker holds the Python it
imported at start and `add_row_serials` is new code. Nothing else in that stack was touched, and
neither was 8081.

## Phase 8 — one intake for three input paths

No record of this phase belongs in the table above, and that is the honest record: the work was a
refactor of the window and of what the status call serves, and it was verified without writing a
business record anywhere.

The Python suite runs against no database, the node checks run the station's own rules, and the
browser harness runs the real station — real `public/js` and real `public/css` — against an
in-memory server of its own, which is where a paste, a file's staged serials and the recovery from
a failed call are exercised. The two lab verifications were re-run against the isolation lab's
MariaDB, and both passed: `verify_batch_intake.py` and `verify_scan_station.py` create and remove
their own rows inside `sp_logistics_lab` and leave nothing behind, as their `disarm` phases report.

Nothing was created on `SmartProcessERP`, and its backend was not restarted for this phase: the
window's own files are served from the bind mount, and the two values the status call now serves
arrive with it.

## Phase 9 — the last refinements of the intake

No record of this phase belongs in the table above: the work was the file reader, the command
grammar and the column heading, and it was verified without writing a business record anywhere.

The Python suite runs against no database — including the operator's own `01.xlsx`, read from this
machine and asserted value for value — and the browser harness runs the real station against an
in-memory server of its own, with the real workbook's values as its fixture and the model name
measured on the rendered page. Both lab verifications were re-run against the isolation lab's
MariaDB and both passed; they create and remove their own rows inside `sp_logistics_lab`.

Nothing was created on `SmartProcessERP`, and its backend was not restarted for this phase: the
window's files are served from the bind mount, and the only Python change the window needs — the
values a preview answers with — is in the endpoint the window calls, so it takes effect when that
worker is next restarted.

## Phase 10 — a pasted table is read like a workbook

No record of this phase belongs in the table above: the work was the decoder between an arriving
text and the intake, and it was verified without writing a business record anywhere.

The Python suite runs against no database — the operator's `01.xlsx` is read from this machine and
asserted column for column, 39 values with `*1`, `*2` and `*3` at the head of their own columns —
and the JavaScript suites run the real station's own rules (`verify_scan_logic.js`) and the real
window in a browser (`preview_scan_header.html`). Both check the same thing from two sides: the
clipboard copy of that range decodes to exactly the sequence the workbook reads, and the window
stages both to the same rows, with the same serials in the same order and the same validation
result. No lab verification was run for this phase, because nothing here touches the database: the
decoder is in the window's own file, and `prepare_import_preview` was not changed.

Nothing was created on `SmartProcessERP`, and nothing was restarted there. Its counts were read
through the read-only probe at the start and the end of this phase, and they **changed between the
two reads — because the operator saved a batch while this phase was being written**, not because
anything here wrote: `Logistics Serial` 71 → 75 and `Logistics Serial Assignment` 37 → 72, with
`Sales Invoice` 13 and `Logistics Import Session` 5 unchanged. The 35 assignments are all dated
`2026-10-14 22:46:18` and belong to `IN-260025`, the invoice the operator is testing: **20 on one
row, 10 on another, 5 on the third**, saved in one batch, which is the mapping the workbook and the
clipboard copy of the same range are both supposed to produce. Four of the thirty-five carry serial
text that does not appear in `01.xlsx` as it stands on this machine — `6954532846`, `6931584718`,
`6931111189`, and `6.95543E+13`, the last of which is how a spreadsheet renders the fourteen-digit
`69554273642846` — so the pasted text itself differed from the file's text for those cells, which is
a fact about that clipboard rather than about the reader. `Logistics Import Session` stands at 5
rather than the earlier 4 because the **restore** preview (`preview_serial_restore`) still stores a
session, a path this phase did not touch.

## Phase 11 — where a paste begins, and what an optional column looks like

> Superseded in phase 13: rows whose item is not marked are no longer columns at all, and the
> "populated optional column" this phase added was removed with them. The paste and the file-read
> parts of this phase stand.

No record of this phase belongs in the table above: the work was the row a paste is aimed at, the
state vocabulary of an optional column, and the notice a file read shows while it runs. All three
were verified without writing a business record.

The Python suite runs against no database (1,099 tests at the end of phase 19, both interpreters), the station's own rules
run under node (`verify_scan_logic.js`, 216 checks) and the window runs in a real browser against an
in-memory server (`preview_scan_header.html`, 158 checks) — including three rows pasted into one at
a time, three rows typed into, a command that overrides the column and switches again, the workbook
pasted into row 1 and into row 3, an optional column's painted header before and after being emptied
and pasted into, and a file read that is slow, refused, and interrupted before it starts.

The rule the browser checks were validated against: the paste fix was **temporarily reverted** in the
served file and the harness re-run, which failed exactly the seven checks that assert it — a paste
into row 2 and into row 3, the serial before a command, the command after it, the commanded columns
of a table pasted into row 3, and the cell that no command names — and then the file was restored
byte for byte (same digest) and the harness re-run green. A test that cannot fail is not evidence.

The operator's own `01.xlsx` was read again for this phase and had changed since the last one — one
cell of column D, `69315847189120` → `693158479120` — so the three copies of that fixture (the Python
test, the node checks and the browser harness) were re-measured from the file rather than carried
forward. Its numbers are stored as numbers by the spreadsheet, and 14-digit ones arrive from the
reader intact; see the note on the operator's clipboard below.

Nothing was created on `SmartProcessERP` and nothing was restarted there. Its status payload was read
through a read-only probe for `IN-260025`, which is the invoice this phase's bug was reported on:
row 1 (5 required, 5 registered) `is_complete` true, row 2 (10 required, 10 registered) `is_complete`
true, and row 3 — optional, 20 units, 20 registered — `is_complete` false and **`is_filled` true**,
which is the state the window now draws green.

### The four serials that do not match the workbook

The saved batch of 22:46 on `IN-260025` holds four serial texts that `01.xlsx` does not contain:
`6954532846`, `6931584718`, `6931111189` and `6.95543E+13`. The last is exactly how a spreadsheet
*displays* the fourteen-digit `69554273642846` in a General-formatted cell, and it is what the
clipboard carried for that cell — the app stored the text it was given, and every assignment's
stored identity matches its serial row (I3 holds). Reading the same file through the app's own reader
gives the full `69554273642846`, because the cell is a number and `number_text` returns the integer
rather than a float's repr. So the divergence is between the **spreadsheet's display formatting and
its stored value**, not something the app did: a numeric cell copied to the clipboard carries what
Excel showed, and no reader downstream can recover the digits it did not send. Nothing was rewritten,
and the remedy belongs where the data is authored — serials stored as text in the workbook, which is
how most of that column already is.

## Phase 12 — one serial button instead of a dropdown

No record of this phase belongs in the table above: the work was the Sales Invoice toolbar, and it
was verified without writing a business record.

The button is drawn by the form's own `refresh`, so the coverage is split the way the code is: five
static checks in `tests/test_desk_artifacts.py` (`TestTheSerialToolbar`) pin the shape of the call —
one `add_custom_button`, two arguments and no group, the window's own opening function as the
action, no «جزئیات سریال‌ها» label left, one definition of each dialog, and the Editor role gate
inside the hook — and the browser harness
(`deployment/lab/preview_scan_header.html`) drives the real hook through its stand-in Desk: one
button, its label, no menu, a press that opens the real station with an input in every row, and no
button at all on a cancelled invoice.

Both halves were validated by reverting the change: the file was put back to the two-button
dropdown, the Python tests failed three of the five and the browser harness failed its four toolbar
checks with `buttons: ثبت سریال | جزئیات سریال‌ها` in the message, and the file was then restored
byte for byte (same digest) and every suite re-run green.

Nothing was created on `SmartProcessERP` and nothing was restarted there: the file is served from
the mount, and the change is one toolbar entry.

## Phase 13 — which rows are serial rows, and one state machine

No record of this phase belongs in the table above: the work was which rows the invoice offers, one
rule for the colour of every row, and the same rule on the write path. Nothing was added to the
database and nothing was removed from it.

The rule is one predicate — `core.eligibility.is_eligible`, the item's `sp_serial_required` — read
by three surfaces that used to disagree: the report's own statement, the status endpoint (which now
serves only those rows and reports the rest as `ineligible_rows`), and `core.validation`
(`ROW_NOT_ELIGIBLE` for a registration on any other, which is what a window that was not reloaded,
an old client or a script would meet). The colour of a row is one function,
`core.eligibility.row_state` — `INCOMPLETE`, `STAGED`, `SAVED`, `CONFLICT` — drawn by the window
through its own copy of the same four numbers, with the labels in the operator's hand added.

Coverage: `tests/test_eligibility.py` (the predicate and all four states, including the order the
answers are decided in), `tests/test_validation.py::TestIneligibleRows` (a registration refused, the
refusal naming the row that was asked for, and emptying such a row still allowed),
`deployment/lab/verify_scan_logic.js` (the state function and the chime rule, at the window's own
level) and `deployment/lab/preview_scan_header.html` (the lifecycle on the real window: gray, four
scans to green, save to blue with the tick, one scan past the quantity to red, removal back to blue,
a paste to green and a removal back to gray; a row whose item is not marked is not drawn and its
registrations appear nowhere; `*4` earns a sentence and lands on nothing; and with the hidden row in
the **middle** of the invoice, `*2` does not become the second visible column).

The negative control: the eligibility check was disabled in `core/validation.py` and four of
`TestIneligibleRows`' five tests failed, and the file was restored byte for byte (same digest) and
the suite re-run green. The two browser-level negative controls were **not** run — the session's
permission mode refused the page evaluation that would have read their result — so the lifecycle
checks are evidenced by their passing run and by the state function's own tests, not by a deliberate
breakage.

Verified on the operator's own data, read-only, through `bench execute` on `SmartProcessERP`:
`IN-260025` has three item rows, and the endpoint now answers with **two** — `PR89887` (5 of 5,
`SAVED`) and `PR89888` (10 of 10, `SAVED`) — while row 3's item `PR89889` is unmarked and is reported
as one ineligible row holding **18** registrations. `get_serial_assignments('IN-260025')` still
returns all **33** assignments, which is the maintenance path: they are readable and removable and
nothing touched them.

## Phase 15 — the pre-release gate

No record of this phase belongs in the table above: it fixed what the pre-release audit found, and
the only file it added is a test fixture.

| What was found | What changed |
| -------------- | ------------ |
| `apply_serial_restore` wrote registrations onto a **cancelled** invoice | `core/export.World` carries the cancelled invoices, read from the stored document; `plan` reports them as `INVOICE_CANCELLED` and places nothing — mixed batches included. Four tests in `tests/test_export.py::CancelledInvoiceTest` |
| A missing invoice and one in another company were refused with different **codes** | `core.authz` raises one code and one sentence for both, and `tests/test_authz.py` asserts it on every invoice-bound endpoint |
| The status read loaded every registration of an invoice | Bounded by `core.limits.MAX_INVOICE_ASSIGNMENTS` and refused when the page fills, with `status_read_is_capped` and its tests |
| The refusal `frappe.msgprint` rendered was unescaped | The station escapes it before it reaches Frappe's HTML |
| `confirm_import` and its machinery were a second, unreachable write surface | Removed, with the gateway methods, the two plans, the authz table entries and 18 tests that covered them |
| The invariants document claimed controller enforcement that does not exist (I3, I4, I11) and an invariant whose endpoint is gone (I12) | Rewritten to say what is enforced and what is merely the app's habit |
| The workbook tests depended on `~/Desktop/01.xlsx` and carried the operator's serials | A synthetic workbook in the repository pins the order; the operator's file is an optional shape check with no serials in the code |
| A scratch form script and two environment probes could be published | Specific `.gitignore` rules; personal paths in `deployment/lab/README.md` replaced with `<repo-root>`; `export-ignore` for the internal-only files |

The negative control for the restore fix: `CancelledInvoiceTest` fails against the plan without the
`cancelled_invoices` check — run by removing it, seeing the four tests fail, and restoring the file
byte for byte (same digest).

Nothing was created on `SmartProcessERP`, nothing was restarted, no migration ran, and no site,
container or schema was touched. The suites are the evidence: 895 Python tests on both
interpreters, 222 node checks, 179 browser checks.

## Phase 16 — the cancel/amend architecture

Read-only, and the only phase that added no code: the question was what happens to a cancelled
invoice's registrations when ERPNext amends it, and what a lossless transfer would have to look
like. What the source showed, and what the design follows from:

| What was read | What it means |
| ------------- | -------------- |
| `amended_from` is set client-side and amend creates **new** child rows with new names | the cancelled invoice and its registrations are retained untouched, so nothing is lost and nothing is moved: the amendment reads as a shortage |
| `on_cancel` only sets `status = Cancelled`, and children are deleted only with the parent | a cancelled invoice's rows are readable indefinitely, which is what makes them a source |
| `amend` requires the `amend` permission and one amendment exists per cancelled document | the pair is `source → target` and the link is `target.amended_from == source` |
| `docstatus` gates writes, and `require_live_invoice` refuses only **cancelled** | an already-submitted amendment is an ordinary live invoice, so transferring onto it needs no exemption |
| `delete_doc` refuses a submitted document to everyone, the Administrator included | a submitted record of the transfer cannot be destroyed through the framework |

## Phase 17 — lossless transfer onto the amendment

| Decision | Where it lives | Its tests |
| -------- | -------------- | --------- |
| Which serials can move, which rows have room, which are already there, which need an acknowledgment | `core/transfer.plan_transfer`, a pure function | `tests/test_transfer.py`, 6 classes |
| What the record says, key for key, and what the operator is shown, in the source's own order | `core/transfer.record_of` and `preview_of` | `TheRecordTest`, `ThePreviewOfWhatHappenTest` |
| The registrations a transfer writes, derived like any other | `core/transfer.assignment_values` → `core.assignment.derive_assignment_values` | `TheWriteIsDerivedLikeAnyOtherTest` |
| The record's schema, and that its `Select` is the refusal vocabulary in both directions | the two DocTypes | `tests/test_transfer_schema.py`, 5 classes |
| Two invoices authorized as what they are — source read and cancelled, target written and live | `core.authz.require_transfer_invoices` | `tests/test_authz.py::TestTransferInvoices` |
| One button, one window, drawn from the server's list | `public/js/sales_invoice_serial_transfer.js` | `TestTheTransferWindow`, `deployment/lab/verify_transfer_logic.js` |

### Phase 17, continued — the historical restore, and format 2

The second half of the phase, after the decision it needed was taken: a cancelled invoice's own
registrations can be put back on it, by a path of its own and nothing else.

| Decision | Where it lives | Its tests |
| -------- | -------------- | --------- |
| What `plan` does with a row on a cancelled invoice, as two named policies rather than a flag | `core/export.CANCELLED_REFUSED` / `CANCELLED_ONLY` | `HistoricalRestoreTest`, `CancelledInvoiceTest` |
| The one endpoint pair that may place such a row, and only for cancelled invoices | `preview_cancelled_serial_history`, `restore_cancelled_serial_history` | `HistoricalRestoreTest`, the endpoint lists |
| A named role on top of a level, for those two endpoints | `core.authz.ENDPOINT_ROLES` | `TestTheArchivistRole` |
| The transfer records travelling in the file and never being re-created | `core/export.SECTIONS`, `SECTION_VERSION` | `TransferRecordsInTheFileTest` |
| A version 1 file still reading as it did | `sections_of(version)` in `read` and in the declared counts | `TransferRecordsInTheFileTest` |

The negative controls for this half: 14 mutations, each applied, the named test watched failing, and
the file restored byte for byte. Two more were attempted and **dropped as behaviour-preserving** —
renaming a policy constant changes nothing, because every use of it is symbolic — and one was
repointed at the test that actually observes it, after the first attempt matched a case that test
does not cover. Both are recorded rather than counted as passes: a control that cannot fail is not
evidence.

The two DocTypes are new, so this phase's evidence is not in the table above: nothing was installed
and no migration ran. The Python suite is 1088 tests, the node harnesses are 222 checks for the
station and 59 for the transfer window, the browser harness is 207 checks — the station's own 187
plus 20 for the transfer window — and 55 negative controls were run across the phase, each one
applied, the named test watched failing, and the file restored byte for byte.

Four of those runs found real defects rather than confirming a rule. Two of them were found by the
controls: a dead branch in `record_of` (a filter that could never match, removed) and a test that
compared the refusal vocabulary with itself, which could not notice a deleted sentence until it was
rewritten to read the DocType. Two more were found by extending `preview_scan_header.html`, and
neither could have been found any other way:

| What the browser caught | Why nothing else could |
| ----------------------- | ---------------------- |
| The table was drawn from `preview.rows`, a key the endpoint does not send | The node harness calls `transfer_table` with a hand-built state, so it agreed with the window about a shape neither had | 
| The first preview sent an explicit *empty* selection, which means "move nothing" — six rows, all «انتخاب نشده» | The server answers that request correctly; only a real first press shows what the window asked for |
| A serial that had been un-ticked kept no checkbox, so a mis-tick could not be taken back | A rule about a control that must still be there after a redraw is a rule about the DOM |

The last one is a design change rather than a fix: a row now carries its tick when the operator's
own answer controls it — including the row they have taken out of the selection — and no control
when the *server* decided, which is what makes the untick reversible inside the window instead of
by closing it.

## Phase 18 — the archive validator, the historical UI, and the integration plan

| Decision | Where it lives | Its tests |
| -------- | -------------- | --------- |
| An archive of transfer records must agree with itself, whatever its digest says | `core.export.check_transfer_records` | `MalformedArchiveTest`, 9 cases |
| One flow, three restores, and the whole difference in one table | `RESTORES` in `public/js/logistics_settings_transfer.js` | `TestTheTwoRestores`, `preview_settings_restore.html` |
| The historical restore is a button of its own, for the archivist | `is_archivist()` and the third `add_custom_button` | the same two, plus `TestTheTwoRestores` |
| What an isolated run has to prove before a release | `docs/integration-test-plan.md` | not a test — the plan the tests are written from |

The browser harness for the settings page is new, and it is the reason this phase could ship a UI at
all: the settings script had no harness, so Phase 17 left its second button unbuilt rather than
unverified. `preview_settings_restore.html` runs the real script against a stand-in Desk and drives
all three modes end to end — 56 checks, including that the archivist sees four buttons, that each
restore's preview carries its own warning, that confirming sends the preview's own token to the
endpoint of the mode that was pressed, and that the sentence about the file's transfer records is
the true one in each mode. That last check exists because the sentence was not: it said the records
are not restored in all three, including the mode that restores them.

The negative controls: 14 for this phase, and two of them found tests that passed **for a reason
they did not mean** — a refusal asserted by a phrase that also appears in a different refusal's
sentence, and a warning asserted over a slice of the file that contained the button's label. Both
were tightened; a control that cannot fail is not evidence, and a test that passes through the
wrong branch is the same problem with the opposite sign.

## Phase 18, continued — restoring the audit records

The decision the first half of this phase stopped on was taken, and this is what it built.

| Decision | Where it lives | Its tests |
| -------- | -------------- | --------- |
| A restored record cannot claim to have been performed here | `RESTORE_FIELDS` written by `_plan_records` from the file's own `site` | `TheMarkerTest` |
| A name collision is refused, never resolved | the pre-flight in `_plan_records` | `TheRefusalsTest` |
| A record is whole or it is not placed | the second pass: one blocked row blocks the record | `TheRefusalsTest` |
| Three restores as three named modes, disjoint | `core.export.MODES` | `RecordRestoreTest`, `HistoricalRestoreTest` |
| A record and its rows as one document | `FrappeGateway.insert_transfers` | the endpoint tests, phase 8 of the plan |

The controls: 12 for this half, and two more tests were found **passing through the wrong branch** —
one asserted a refusal with a phrase that another refusal's sentence also contains, and one read the
first warning in the file after a third mode had been added above it. Both were narrowed, and the
second was narrowed twice before it was right.

## Phase 19 — the isolated end-to-end run

The plan in `docs/integration-test-plan.md` was written in phase 18 and executed here, on the
isolated lab (`sp_logistics_lab` on 8095, `sp-logistics-isolation-lab`), never on
`SmartProcessERP`. Seven of the eight phases pass; phase 7's removal and reinstall steps are a
person's, and what it asked about the export was measured inside phase 6 instead.

| Phase | What it proved on a real site | Script |
| ----- | ----------------------------- | ------ |
| 1 | migrate creates both DocTypes, and the `Select` options in `tabDocField` are exactly `core.transfer.RECORDABLE_REASONS` and `TRANSFER_RESULTS`, in order | `verify_lab_transfer.py` (the table) |
| 2 | the four roles; no role may write, delete, cancel or amend a record; a Manager **without** the Archivist role is refused by name; a company-restricted caller is refused exactly the other company's rows | `verify_lab_authorization.py` |
| 3 | the record is submitted, `_validate_update_after_submit` refuses a changed one, `delete_doc` refuses Administrator and Manager alike, and the source's registrations are byte-for-byte what they were | `verify_lab_transfer.py` |
| 4 | 18 labels on a cancelled invoice, an amendment invoicing 10: 10 moved, 8 `NO_ROOM`, a second press writing a second record with no moves, and an acknowledgment turning a live-elsewhere label into a move | `verify_lab_transfer.py` |
| 5 | two processes released on the same instant for ten places: one moved exactly ten, the other was told «درخواست دیگری هم‌زمان…» and wrote nothing, and the row ends at ten | `verify_lab_concurrency.py` |
| 6 | the ordinary restore still refuses a cancelled row and names the historical path; the historical restore places that row and refuses live ones (`NOT_HISTORY`); a full row is `EXCESS_QUANTITY`; the second attempt creates nothing | `verify_lab_restore_history.py` |
| 8 | seven crafted files: placement with the marker and the original actor, `RECORD_EXISTS`, `RECORD_LINK_CANCELLED`, `SERIAL_UNRESOLVED` taking the record down whole, and a hand-edited file refused as `EXPORT_BAD_SHAPE`/`EXPORT_ROW_INVALID` before any write | `verify_lab_restore_records.py` |

**Four defects, none of which any database-free suite reached.** Three were in the app and one in
its packaging; all four are in the `Unreleased` changelog, and each got a test that fails when the
fix is removed:

- the two app-level navigation files were deleted as orphans by every migrate, because v16 looks
  for `<folder>/<scrub(name)>.json` and the Persian document names were not in the file names;
- `gateway.read_target_rows` counted `ExistingAssignment` objects through a helper written for row
  dicts, so the **second** preview of a transfer raised `AttributeError` — the first button press
  in every earlier test had an empty target;
- a company-restricted caller restoring a file that named another company's serial collided on the
  `Logistics Serial` primary key, and the whole request was reported as a concurrent one;
- the records preview never passed a restore marker to the planner, so
  `preview_restore_audit_records` answered `EXPORT_BAD_SHAPE` for every file and the third restore
  was unreachable through the API.

`verify_http_round_trip.py` was run as well, because the records-preview defect was invisible in-process and through a stubbed `frappe.call`: it drives the front end, reads the form meta, and calls the third restore's two endpoints through the real dispatcher. Before the fix that script answered `EXPORT_BAD_SHAPE`; after it, it places nothing and reports thirty-four `RECORD_EXISTS`.

**Controls.** Four, one per fix: the file name restored to its Latin form, `_count_by_row` restored
in `read_target_rows`, the taken-name rule removed from `plan`, and `stamp=` removed from
`_preview_export`. Each was run with the defect present, the named test failed, and the file was
restored byte-identically (checked by digest).

**Three harness defects, found the same way and worth recording.** The concurrency script's `arm`
did not commit, so the two callers could not see the amendment it had just created and both were
refused with «فاکتور یافت نشد» — the first version of a race test can hide its own subject. The
parent then counted the site through a `REPEATABLE READ` snapshot taken before the race and saw
zero, where the transfer had moved ten. And two checks read the preview's *sample* of conflicts,
which is capped by design, so they passed on one run and failed on the next. All three were the
harness's, and all three were fixed in the harness.

**Not run.** Phase 7's removal and reinstall (`deployment/lab/README.md`, steps 4 and 6) — the
workspace guard refuses those to the agent, by design. What phase 7 asked about the export was
measured in phase 6 instead: the file carries `format_version` 2 with the `transfers` and
`transfer_items` sections, and both assignment modes report those records without offering to
place one.

## Phase 20 — the security review, and what it found

An independent review of the uncommitted work (`security-permissions-reviewer`) went over the new
cross-company read, the sixteen endpoints, the archive validator, the audit DocTypes and the
permission layer. It confirmed the absence of `ignore_permissions`, of `set_user`, of core
modification and of raw writes, and it produced five findings. Three were defects and are fixed;
two were documentation and disclosure questions and are now recorded rather than papered over.

| Finding | Verdict | What changed |
| ------- | ------- | ------------ |
| A restored record's `docstatus` came from the file, so a hand-made archive could place a **draft** — the one state I24 exists to prevent | fixed | a fifth rule in `check_transfer_records` refuses such a file, and `_plan_records` writes the submitted value rather than carrying the file's; four tests, two controls |
| Four endpoints read the site's **settings** before they were authorized, while each carried a comment saying nothing is read before the request is allowed | fixed | the read moved after the check in `get_serial_assignments`, `set_row_serials`, `add_row_serial`, `add_row_serials`, and inside `_transfer_request` for both transfer endpoints; the callers' text is now parsed after authorization too (the delimiters are settings as well) |
| A record name held by **another company** reached the insert and was reported as a concurrent request — an invitation to retry what can never succeed | fixed | the records' insert has its own handler and says the name is taken |
| `World.serial_names` is a cross-company existence oracle (one bit per name in the file) | kept, bounded and pinned | two tests in `tests/test_company_scope.py` — the world carries the name and not the identity, and the read asks for `name` and nothing else (one field widened, one removed, as controls) — and **I28** in `docs/integrity-invariants.md` names it as the sole sanctioned cross-company read |
| `_plan_records`' docstring claimed the *rows* are stamped | fixed | the sentence now says the record is, and why the child rows are not |

Two more tests came out of the review's first finding by asking the same question of the two
places that answer it: the archive validator and the plan. `test_no_endpoint_reads_the_site_settings_before_it_is_authorized`
is the structural one — it compares line numbers for every endpoint *and* every helper that both
reads the settings and authorizes, which is what makes a delegated read (`_transfer_request`) as
checkable as an endpoint's own.

**The other suites, re-run after all of it.** 1,107 database-free tests, OK; both Node harnesses,
PASS; `preview_scan_header.html` in a real DOM, **214 checks, no failures** — seven of them new
this phase, covering the refusal of an apply and the retry it has to leave possible; and
`preview_settings_restore.html`, **56 checks, no failures**. The lab was read-only this phase, so
`verify_lab_read_only.py` was written for it: every read endpoint on the live site, with the four
tables counted before and after — eight calls, none of them writing a row.

## Phase 22-A — the release candidate, formatted and rehearsed

Three things happened, in this order, and each one is evidence for something a release needs.

**The formatter ran, and it was proven safe rather than assumed.** `ruff format` rewrote 53 files
(the repository had never been formatted under the config its own pre-commit hook uses). The
question that matters — did it change anything the code *does* — was answered with ASTs: for every
tracked file, the committed content was formatted in a copy and `ast.dump` compared before and
after. **69 files, 0 differences.** The formatting-only commit this makes possible was measured
the same way: **26 files, +136/−183 lines against HEAD**, whitespace and line breaks only —
small enough to review, which a 53-file mixed diff would not have been.

**I28 got the boundary tests the decision asked for.** The cross-company serial-*name* read may
exist to stop a colliding insert and nothing else, so four tests now say so: no endpoint names the
fact (`api/endpoints.py` cannot return it), no endpoint takes a serial to look up, the read is
bounded to the names the document carries (a world with no document reads only the caller's own),
and a taken name changes exactly one thing in the plan — the insert — with a row that would have
been placed becoming a named `SERIAL_UNRESOLVED` refusal rather than a crash. Controls: widening
the read's field list, giving an endpoint a `serial_name` parameter, and making the skip silent
each fail the test they should.

**The recovery was rehearsed, and it found a defect.** `verify_lab_recovery.py` exports the site's
own file, removes one registration from a live invoice and one from a cancelled invoice, puts both
back (the second through the historical path), transfers a cancelled invoice's serials and reads
the audit record back, places an audit record from a file, and then requires that a file which
*cannot* complete — two records, the second carrying a name taken by a record of another company,
invisible to the pre-flight — leaves **nothing** behind, including the record inserted before the
failure. All six steps pass, and the file that exercises step 6 is where the defect surfaced:
`world_names` read the documents a file mentions from its serial and assignment rows only, so a
file with no assignments named no invoice and the records restore answered `INVOICE_MISSING` for
every record with its amendment on the site. The records' own invoices and company are named now
(CHANGELOG, Fixed), with a test.

**Two lab assertions were brittle and are now not.** Both had counted on how much the lab had
accumulated: one read a preview's capped conflict *sample* for a sentence, and the other asserted
that every record in a file was refused as `RECORD_EXISTS` — true until the lab held records of
another company, which are refused for their invoice instead. Both now assert the rule, not the
number, and both scripts pass again.

**Counts.** 1,119 database-free tests, OK; `ruff check` clean; both Node harnesses PASS (222 and
59); both browser harnesses PASS (214 and 56 checks); and on the lab: read-only smoke, transfer,
authorization, concurrency, historical restore, audit records, recovery and the HTTP round trip —
eight scripts, all PASS, with the isolation re-confirmed from the volumes, the networks, the site's
database name and a hostname that the `erpnext` stack cannot resolve.

## Environment

The only site this app is tested against is `SmartProcessERP`, reached on port 8080. That
environment holds test data, not production data, but it is still not written to without
explicit approval for the specific step.

The stack on port 8081 is out of scope for this project: its containers, its Compose invocation
and its apps are not to be touched, and none of them was.

It is **not** a second environment, and this was measured rather than assumed. Both stacks report
the same `sites` directory (the same inode) and the same `frappe.conf.db_name`
(`_939af57aabbacf4d`) over the same `db` host, and both see the same rows — nine Sales Invoices
and the same two companies, including every record this project created. The 8081 backend has no
`smartprocess_logistics` in its app list, so it serves the shared data without this app's code
present, and it answers 200.

So "out of scope" means: do not restart it, do not change it, do not install anything into it. It
does not mean the data differs. Every write this project made was authorized for the disposable
test site and is visible through both front doors; an uninstall or a migration ordered against
one of them is ordered against the other.

## The isolation lab

From 0.2.0 the removal-and-reinstall rehearsal runs in a stack of its own,
`deployment/lab/compose.isolation-lab.yaml`: Compose project `sp-logistics-isolation-lab`, its own
network, its own five volumes (`sp_logistics_lab_db`, `_sites`, `_logs`, `_redis_cache`,
`_redis_queue`), its own site `sp_logistics_lab`, and one published port on loopback (8095). It
shares **nothing** with the `erpnext` project — measured, not assumed: its MariaDB reports a
different hostname and holds only its own database, and no volume of one project is mounted into
the other.

Records the rehearsal creates, all inside that stack and all removed by
`docker compose … down -v`:

| Record | Purpose |
| ------ | ------- |
| `Company` `SP LAB COMPANY` | a company for the invoices to belong to |
| `Item` `SP-LAB-WARRANTY-ITEM` | a non-stock item carrying `sp_serial_required` |
| `Customer` `SP LAB CUSTOMER` | the invoices' customer |
| two `Sales Invoice` documents with 3 and 2 serial rows | realistic rows for the serials to be registered on, through the app's own endpoint |
| `Logistics Serial` — `LAB-W-0001` … `LAB-W-0005` | the serials those invoices carry, and the identities the export has to keep |
| the export document | written by the `export` phase into `<site>/private/files/lab-serial-export-*.json`; it is the copy the cycle is about |
| `<site>/private/files/lab-cycle-<phase>-*.json` | one evidence file per phase |

The rehearsal's phases are `seed`, `export`, `after-removal` and `after-reinstall`; the removal and
the reinstall between them are run by a person, because the workspace's own safety hook refuses
those two commands to the agent. `deployment/lab/README.md` holds them.

`deployment/lab/verify_scan_station.py` writes its own records there and removes them again in the
same run — a draft `Sales Invoice` with rows of 20, 2 and 3 units, the `SCAN-` serials the checks
register, and every assignment it makes, including the one it **moves** onto a full row to produce
the over-filled state the app refuses to write. Its `disarm` phase deletes the assignments, then
the serials that nothing points at any more, then the invoice. Its `check` phase reports what it
left behind; a run that ends without that line did not finish.

Three of its checks are the ones a fake cannot make, because they are about time rather than
about logic: two processes released at the same instant for a row's last place, twice — once with
labels that do not exist yet and once with labels the site already holds — and one caller that
reads a row, waits while another fills it, and then writes. The measured result of the last one is
worth recording here: the write is refused with MariaDB `1020 Record has changed since last read`,
raised by the locking read that counts the row, and the retry is refused by the cap with
`[EXCESS_QUANTITY]`. Neither by itself is the guarantee; together they are — the row ends at three
of three on every run, and did not before that read was made locking.

No row of this list belongs to `SmartProcessERP`, and none of it is written to a site that
matters. `verify_isolation_cycle.py` refuses to run unless both the site name and the database
name say it is the lab.

## Phase 3B — invoice-first integrity guards (2026-10-20)

The validation-only `doc_events` (`smartprocess_logistics/integrity.py`) are covered by
`tests/test_integrity.py`: database-free tests over the pure rules in
`smartprocess_logistics/core/integrity.py` and the registration/safety properties of the
handlers. The full suite is **1,165 tests, OK (1 skip)**; `ruff check` and `ruff format --check`
pass. The rules and their scope are recorded as I29–I33 in `docs/integrity-invariants.md`;
I8 and I9 were amended because the app now has validation-only hooks on four financial
documents.

The production proof is `deployment/proofs/step3b_production_check.py`, run from
`bench --site SmartProcessERP console` on **8080** with disposable `SP-3B` records. It exercises
the four guards, partial deliveries, a partial receipt and SRBNB settlement, a purchase return
with the missing `purchase_invoice_item`, a debit note, cancellation, amendment, a multi-invoice
receipt with the same rate (allowed) and with different rates (refused), a non-managed standard
sales invoice and a non-managed foreign purchase at the market rate (both allowed). All expected
checks passed. The run leaves its identified records on 8080; it changes no setting and runs no
migration.

## Simplified virtual/physical warehouses (2026-10-21)

The Transit-only arrival service and its per-company JSON configuration were removed. A
warehouse is virtual when its standard `Warehouse Type` is in `sp_virtual_warehouse_types`
(default `Virtual`, `مجازی`, `Transit`); every other leaf warehouse is physical. The
database-free suite still runs **1,218 tests, OK (1 skip)** under `.venv`; `ruff check` and
`ruff format --check` pass.

`tests/test_warehouse_policy.py` covers the type classification, the four allowed transfer
directions, the Material-Transfer-only rule for virtual movements and the virtual sales refusal.
`tests/test_purchase_rules.py` now covers the renamed warehouse-neutral `purchase/receipt.py`
and the surviving safety checks. `tests/test_integrity.py` also pins the return link when the
original receipt row has no `purchase_invoice_item`: the unique invoice row is used, a missing
or ambiguous row is refused, a Purchase Receipt Item name is never written into that field,
and a return with no Purchase Invoice anywhere is left to the standard workflow. The
development-only `tests/test_commitment.py` adds the virtual committed-warehouse refusal, the
zero-sellable virtual source and its availability arithmetic; that test module is not part of
this release candidate. The invariants are recorded as I39.

The integration proof is `deployment/proofs/virtual_warehouse_workflow.py` on **8080** with
isolated `SP-VW` records. It passed: a full USD Purchase Invoice with no stock and one supplier
liability; 30 units into virtual A and 70 into physical C with `per_received = 100`; SRBNB
reconciled to zero; an LCV on the virtual receipt; virtual→virtual, virtual→physical,
physical→virtual and physical→physical transfers with no GL and total quantity 100; physical
D sellable 15 with physical total 75; virtual A sellable 0; inventory GL 505,000,000; a
foreign Payment Entry clearing the invoice; a purchase return of 2 from virtual; no Serial No
created; virtual Sales Invoice, Delivery Note and Stock Reservation Entry refused with Persian
messages; cross-company and Guest permission refusals; and a second submit of the same Stock
Entry leaving stock unchanged.

`deployment/proofs/virtual_concurrent_submit.py` ran twice at once against 15 + 15 from a
virtual balance of 23: one submitted, the other raised `QueryDeadlockError`; the final balances
were A 8 and B 15 with no extra stock. `deployment/proofs/virtual_physical_sale_proof.py`
submitted a physical `Sales Invoice update_stock=1` and refused the same invoice from virtual
A, then cancelled both documents. `deployment/proofs/virtual_bundle_guard_proof.py` created a
Product Bundle and proved its `packed_items` cannot ship from virtual A, while the same bundle
sold from physical C normally; both documents were cancelled.
`deployment/proofs/virtual_unmanaged_return_proof.py` submitted a linked Purchase Receipt for an
unmanaged item and then its standard return, confirming the new link fill and the stricter
stock-receipt rule do not break an unmanaged item's return.
`deployment/proofs/virtual_warehouse_cleanup.py` proved a Purchase Receipt cannot be cancelled
while its stock has been transferred (`NegativeStockError`), then cancelled the whole
disposable chain; no non-zero Bin remained for the test items.

## Unified invoice-first purchase policy (2026-10-21)

`core.integrity.purchase_invoice_problems` now applies to every stock Purchase Invoice, not
only foreign or `sp_serial_required` rows. `update_stock = 1` is refused when any non-zero
inventory row would post stock. The guard is currency-agnostic; the handler reads each Item's
`is_stock_item` and `is_fixed_asset`. `purchase.rules.stock_receipt_problems` now requires a
submitted Purchase Invoice on every non-zero stock Purchase Receipt row, regardless of the
managed flag. Every linked stock row must also match the invoice's company, supplier, item,
`purchase_invoice_item` parent, UOM and conversion factor; the existing rate/valuation check
now runs for all linked stock rows, not only managed ones. Service/non-stock rows, fixed assets,
zero quantities, returns/Debit Notes, opening entries and internal inter-company transfers keep
their standard workflow. A submitted historical document is never inspected.

`tests/test_integrity.py` and `tests/test_purchase_rules.py` cover the new scope: stock rows
with and without `update_stock`, service/non-stock, fixed asset, zero quantity, return,
opening, internal transfer, historical document, unlinked stock receipts, linked/unmanaged
receipts, draft/cancelled invoices, supplier/company/item/PI-item identity, UOM/conversion
factor and the named row in the Persian message. The full suite is
**1,228 tests, OK (1 skip)** under `.venv`; `ruff check` and `ruff format --check` pass.

`deployment/proofs/unified_purchase_policy_proof.py` ran on **8080** with disposable records:

- domestic IRR Purchase Invoice 10,000,000 with no SLE and full payable/SRBNB accounting;
- domestic receipts 30 into virtual A and 70 into physical C, `per_received = 100`, SRBNB zero;
- domestic LCV 2,000,000 with no LCV GL;
- domestic Purchase Receipt return of 5 and a submitted Debit Note of 100,000 with no SLE;
- foreign USD Purchase Invoice 100 USD at 500,000 with no SLE;
- foreign receipt of 10 units and a payment of 52,000,000 IRR for 100 USD, producing
  `سود/زیان تبدیل - SP` 2,000,000 and clearing the invoice;
- a service/non-stock Purchase Invoice with `update_stock = 1` submitted with no SLE;
- domestic and foreign `update_stock = 1` attempts refused with the same Persian message;
- an unlinked stock Purchase Receipt refused with the Persian invoice-first message;
- Guest permission refused; and the whole chain cancelled with zero test stock.

`deployment/proofs/purchase_link_integrity_probe.py` exercised the malicious/inconsistent
links on **8080**: wrong supplier, wrong company, wrong item, a forged `purchase_invoice_item`,
and a different UOM were refused by SmartProcess Logistics with the named Persian messages;
sequential over-receipt was refused by ERPNext `OverAllowanceError`; a financial-only Debit
Note posted no SLE; a stock-affecting PI Return was refused by SmartProcess Logistics; the
valid partial receipts reached 100%, a Purchase Receipt Return reduced `received_qty` to 9
and `per_received` to 90, and cancellation/amendment restored/carried the links correctly.
The two-process concurrency probe produced one submitted receipt and one `QueryDeadlockError`,
with `received_qty = 6` and no extra stock.

`deployment/proofs/purchase_fx_regression.py` re-checked the existing FX safeguard: a
matching-rate receipt was accepted and a receipt at 600,000 against a 500,000 invoice was
refused with «نرخ تبدیل». Both documents were cancelled.

## Stock Entry Item Code filter by source warehouse (2026-10-22)

ERPNext 16.35.0 has no warehouse-aware Item selector for Stock Entry. The form's only query is
`erpnext.controllers.queries.item({is_stock_item: 1})`; that query filters Items by themselves
and does not join `Bin`, and `erpnext.queries.setup_warehouse_query` filters warehouses by the
chosen item — the reverse direction. `erpnext.ItemSelector` is not wired to Stock Entry. So the
filter is a custom Link query, the mechanism `frm.set_query` already supports.

The client script `public/js/stock_entry_item_stock_filter.js` replaces the `item_code` query
only when `purpose == "Material Transfer"`, resolving the source as the row's `s_warehouse`,
else `from_warehouse`. Without a source it shows the Persian "select the source first" alert
and returns no rows rather than an unfiltered list. Every other purpose keeps ERPNext's own
query. A row source change re-checks the selected item through the same endpoint and shows a
Persian warning without clearing the row or changing its quantity.

The server endpoint `smartprocess_logistics.warehouse.item_search.items_in_stock` is a
whitelisted read-only Link query. It refuses an unreadable, missing, group or wrong-company
warehouse; requires Item read; applies `get_match_cond("Item")` (role permissions, Item user
permissions, `if_owner` and the site's permission-query hooks) to the raw SQL; joins `Bin` for
that warehouse with `actual_qty > 0`; and searches code and name with paging. `core/stock_search.py`
builds the statement and formats the `Stock: N UOM` description without importing Frappe.

`tests/test_stock_search.py` adds 16 database-free tests. The full suite is
**1,244 tests, OK (11 skipped)**; `ruff check` and `ruff format --check` pass.

`deployment/proofs/stock_entry_item_filter_proof.py` ran on **8080** with isolated `SP-SEF`
items: C returned only X/Y/V, D only Z/ONLY-D, virtual A only V with `Stock: 4 Nos`, the
zero-stock and elsewhere-stock items were absent, name and code search worked, exact lookup of
a stockless item was empty, wrong company / empty / missing warehouse returned nothing, paging
returned distinct pages, and a Warehouse-restricted user saw D but not C.
`cleanup_stock_entry_item_filter.py` cancelled the entries and deleted the users and items;
all test Bin rows are zero.

The real browser was exercised on 8080 after a backend restart (hooks.py changes are cached in
the Python worker): the Material Transfer dropdown listed only C's items with quantities and
the filter description; keyboard ArrowDown/Enter selected `SP-SEF-X`; changing the row source
to D produced the Persian warning and kept the row; with the row at D the dropdown showed only
D's items; virtual A showed only V; no source showed the Persian prompt and an empty list;
"Lamp" matched item names; a Material Transfer of 2 units submitted as `MAT-STE-2026-00119`
and moved 5→3 in C and 0→2 in D, then cancelled back; Material Receipt still used ERPNext's
unfiltered Item query.

## 0.5.0 release preparation and SRBNB reconciliation (2026-10-22)

`core/stock_search.filter_values` now validates the three documented Link filters. A present
non-string value (`{"warehouse": ["=", "WH-001"]}`, an int, a dict) is refused with an empty
list at the API boundary; missing keys and `None` mean "not sent", and ordinary string filters
behave as before. Six database-free tests in `tests/test_stock_search.py` cover valid strings,
missing keys, `None`, each malformed type, non-dict payloads and unknown keys. The full suite is
**1,250 tests, OK (1 skipped)** under `.venv` with `openpyxl`; without `openpyxl` it is 1,250
tests, OK with 11 skipped. `ruff check` and `ruff format --check` pass.

Direct HTTP checks on 8080 after a safe backend restart: a malformed warehouse filter returned
`200` and `{"message": []}`; a valid filter returned `200` and four rows. Guest and a
Warehouse-restricted user still get no rows outside their permission.

The 733,333.33 `Stock Received But Not Billed` balance in the unified purchase proof is fully
reconstructed from the stored GL:

- 100 units at 100,000: Purchase Invoice debits SRBNB 10,000,000; the 30- and 70-unit receipts
  credit it back, leaving zero.
- A 2,000,000 LCV on the 30-unit receipt reposts that receipt under its own voucher: stock
  becomes 5,000,000, SRBNB is credited 3,000,000, and the freight expense is credited
  2,000,000. SRBNB is still zero; valuation is 166,666.67 per unit.
- The 5-unit Purchase Receipt Return debits SRBNB 833,333.33 (5 × 166,666.67).
- The 1-unit financial Debit Note credits SRBNB 100,000 (invoice rate, no SLE).
- Net: 733,333.33 debit, comprising 400,000 of invoice value for the four units physically
  returned but not financially credited, plus 333,333.33 of landed cost on the five returned
  units.

Two controlled matched 5-of-5 return chains confirmed the rule: without LCV the SRBNB net is
zero; with a 100,000 LCV on ten units it is 50,000 (five units × 10,000). The residual is
standard ERPNext treatment of a landed cost that the supplier invoice never billed, not a
duplicate posting. Clearing it requires a freight credit note or an LCV adjustment; the app
does not force it to zero.

Version `0.5.0` and its CHANGELOG/README entry are prepared for release but not committed. The
release boundary audit found one cross-feature dependency: `integrity.py` imported
`core.commitment.commitment_token`, and the working tree registered the paused Sales Invoice
commitment lifecycle and Delivery Note commitment validation. That dependency is removed from
the release candidate; the isolation is recorded below. The Item filter is independent of all of
it; purchasing and virtual warehouses share the approved `integrity.py`.

The final isolated release candidate, built from the approved files only, ran **1,220
database-free tests, OK with 2 skipped** under `.venv` with `openpyxl` (12 skipped without it),
and `ruff check` and `ruff format --check` passed over 104 files. It imported and registered only
the approved hooks, and on 8080 it returned the warehouse-filtered Item rows, refused a virtual
Delivery Note, and refused an unlinked managed Delivery Note with `DN_NOT_LINKED`. No document,
Item or Bin row remained. The paused development tree is not part of that candidate.

## Commitment isolation before 0.5.0 (2026-10-22)

This section records the development-tree separation that made the release boundary possible.
The release candidate contains none of the paused modules or tests named here.

The cross-feature dependency was `integrity.py`'s module-level
`from smartprocess_logistics.core.commitment import commitment_token`, used only by
`_commitment_state` and `validate_delivery_note`. The approved purchase validators lived in the
same module, so importing the purchase module imported the paused commitment rules even though
it never called them.

The separation is structural and behavior-preserving:

- `core/integrity.py` owns the approved managed Delivery Note rule (I30):
  `delivery_note_problems` plus the `delivery_identity_problems`, `delivery_source_problem` and
  `delivery_second_deduction_problem` helpers. It imports no commitment code.
- `core/commitment_delivery.py` owns the committed-warehouse half and composes it with those
  helpers. A 900-case differential test against the pre-refactor function produced identical
  problem codes, messages and rows.
- `integrity.py` owns the approved `validate_delivery_note` and the shared invoice-context and
  row builders; it imports no commitment module. `commitment_delivery.py` owns
  `_commitment_state` and the combined validator, passing the commitment lookup into the shared
  context.
- `hooks_composition.py` registers the approved Delivery Note pair:
  `integrity.validate_delivery_note` then `warehouse.sales.validate_delivery_note`. In the
  development tree `hooks_commitment.py` replaces the first entry with the combined
  `commitment_delivery.validate_delivery_note`, so the final list and order are unchanged.
- `hooks_commitment.py` adds the committed-stock panel script, the two Sales Invoice lifecycle
  handlers and the combined Delivery Note validator only when present.
- The paused tests moved to `tests/test_commitment_delivery.py`; `tests/test_integrity.py` keeps
  the approved I30 tests and the approved hook composition. `test_lifecycle` keeps the
  development composition test, skipped when the extension is absent, and the approved test.

Evidence:

- `doctype_js` is identical to the baseline. The development `doc_events` has the same handlers
  and order; the Delivery Note handler path is the combined validator because the function moved.
- The 900-case differential test compared the combined function with the baseline for every
  branch and random combinations: **identical**. The approved I30 function matched the baseline
  on all 203 neutral-commitment cases.
- The full development suite is **1,268 tests, OK (1 skipped)** under `.venv` with `openpyxl`;
  without it, 1,268 tests, OK with 11 skipped. The 48 paused commitment tests pass explicitly.
- The live I30 draft-Delivery-Note check refused an unlinked managed row with the Persian
  `DN_NOT_LINKED` message through **both** `integrity.validate_delivery_note` and the
  development hook; no document or Item remained.
- A physical, non-managed draft Delivery Note (`SP-3B-PLAIN` in `مرکزی - SP`) was allowed by
  the approved validator, inserted as a draft and deleted; no stock moved. The Product Bundle
  proof again refused the virtual Delivery Note while the physical bundle invoice posted and was
  cancelled; its Items were deleted and Bin rows are zero.
- After a safe restart, the development site still registers the two Sales Invoice lifecycle
  handlers, the combined Delivery Note validator, the committed-stock panel script and the
  Stock Entry filter script. The Sales Invoice and Stock Entry form metas contain their scripts;
  the Stock Entry Material Transfer query still resolves to the source warehouse.
- In the browser, a disposable user loaded the Sales Invoice form with both serial scripts and
  the committed-stock script in its meta, and the Stock Entry form with the filter script and a
  Material Transfer query for `بختیار - SP`. The user was deleted after the check; no document
  was saved.
- The integration proofs were re-run after the separation: the Item filter proof, the unified
  purchase proof (SRBNB 733,333.33, zero final stock) and the virtual physical sale proof (virtual
  refusal, physical sale, cancellation). All check lines matched the previous audit, and their
  disposable Items were removed with zero Bin rows.
- A temporary release tree under `%TEMP%\sp-release-0.5.0` contained only the approved files,
  with the paused modules and tests removed and the paused `authz`/CSS hunks stripped. It
  imported with the bench Python without loading any commitment module, registered the approved
  Delivery Note pair, returned `DN_NOT_LINKED` from the approved I30 rule, and ran **199**
  approved database-free tests (1 skipped: the development composition test). The tree was then
  removed.

Version `0.5.0` and the CHANGELOG/README entries are prepared but not committed. The paused
development tree remains intact.

## I30 Delivery Note audit (2026-10-22)

The pre-refactor `integrity.validate_delivery_note` was not one feature. Its conditions split
into three groups:

| Condition | Rule | Owner | Active in dev | In release |
| --------- | ---- | ----- | ------------- | ---------- |
| `managed` scope | any Delivery Note row, or linked invoice, names an Item with `sp_serial_required` | approved managed serial workflow | yes | yes |
| `DN_NOT_LINKED`, `DN_DETAIL_MISSING`, `DN_INVOICE_MISSING`, `DN_DETAIL_NOT_FOUND` | a managed row must name its own invoice row | approved I30 | yes | yes |
| `DN_CROSS_INVOICE`, `DN_ITEM_MISMATCH`, `DN_INVOICE_NOT_SUBMITTED` | the named row belongs to the invoice and matches it | approved I30 | yes | yes |
| `DN_COMPANY_MISMATCH`, `DN_WAREHOUSE_COMPANY`, `DN_TARGET_WAREHOUSE_COMPANY` | invoice, source and target are the document's company | approved I30 | yes | yes |
| `DN_SOURCE_WAREHOUSE_MISSING` | a stock row names a source warehouse | approved I30 (native ERPNext also requires it on submit) | yes | yes |
| `DN_SECOND_DEDUCTION` | a managed invoice that already moved stock is not deducted again | approved I30 | yes | yes |
| `DN_COMMITMENT_BROKEN`, `DN_COMMITMENT_TARGET_MISSING`, `DN_COMMITTED_WAREHOUSE` | the committed warehouse exists and is the source | paused Commitment (I36) | yes | no |
| virtual source refusal | a virtual warehouse cannot be delivered from | approved `warehouse.sales` (I39) | yes | yes |

The approved I30 rule is `core.integrity.delivery_note_problems`; `commitment_delivery.py`
composes it with the commitment checks for the development tree. The approved composition
registers `integrity.validate_delivery_note` then `warehouse.sales.validate_delivery_note`; the
development extension replaces the first entry with the combined
`commitment_delivery.validate_delivery_note`. Hook order and the final development list are
unchanged.

Evidence: 900 differential cases against the pre-refactor function matched exactly; the
approved function matched all 203 neutral-commitment cases; the draft unlinked managed Delivery
Note was refused by both validators with `DN_NOT_LINKED`; a non-managed physical draft was
allowed, inserted and deleted; the virtual bundle Delivery Note was refused while the physical
bundle invoice posted and was cancelled. No document or Item remains.
