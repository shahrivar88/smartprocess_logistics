# smartprocess_logistics

Optional **warranty serial numbers** for individual `Sales Invoice Item` rows.

The app records which serial numbers were sold on a Sales Invoice item row, shows how many
units of that row still have no serial number, and reports on both. Registration is optional
per Item and is **independent of ERPNext's own stock serial numbers**: the app does not turn
on `Item.has_serial_no`, does not create or edit `Serial No` or `Serial and Batch Bundle`
records, and never posts stock or accounting entries.

## Release 0.7.1

Version 0.7.1 fixes the module sidebar of the Desk panel. Frappe 16 nests a link under the
Section Break above it only when the link carries `child: 1`, and this app's sidebar marked none
of them, so «گزارش‌ها», «خرید», «انبار» and «فروش» rendered nothing. All eight reports are now
grouped under an expandable «گزارش‌ها», the operational shortcuts and «تنظیمات سریال» stay outside
that group, and no route, title or permission changes. The database-free suite passes: 1659
tests, 2 skipped in the interpreter that carries `openpyxl`. See `CHANGELOG.md`.

## Release 0.7.0

Version 0.7.0 reconciles the phase-2 functionality onto the 0.6.0 application: the
«کالاهای خریداری‌شده و دریافت‌نشده» report, the warehouse-classification settings table with the
stable `physical` / `virtual` / `transit` codes, and a permitted default company on the three
operational reports so they load when opened. Both Logistics workspaces, every 0.6.0 report and
every existing guard are preserved. The database-free suite passes: 1653 tests, 2 skipped in the
interpreter that carries `openpyxl`. See `CHANGELOG.md` for scope and known limitations.

## Release 0.6.0

Version 0.6.0 is the GitHub publication of the approved tree at `f50e761`. The application
source is the tree deployed on `SmartProcessERP`. Compared with 0.5.1 it adds «تراز موجودی»
and the Transit/Virtual split, the authorized-stock selectors, the Purchase Invoice cancel
guard, and the per-delivery serial attribution. It ships seven Logistics reports, the Sales
Order progress panel, and the Sales Invoice warehouse and Item selectors. The Commitment work
remains deferred and excluded. See `CHANGELOG.md` for the verification record, known
limitations, and the future Persian/Jalali filtering work.

## Reports

One serial report ships, and one serial query is written and not yet wired to a Report document. Each is
**one** statement: the registered count comes from a grouped subquery joined in, not from a
query per row.

| Report | Grain | Answers | State |
| ------ | ----- | ------- | ----- |
| Sales Serial Register | one row per serial assignment | which serial was sold on which invoice, and when it was registered | `core.reports.build_register_query` only; no Report document ships, so it is reachable from a future report or a console and from nowhere else |
| Missing Sales Serials | one row per serial-required invoice item row | which rows still need serials, and how many | ships, as a Script Report with Persian columns and Persian filters |

The app ships eight Logistics reports. «تراز موجودی» is the primary operational report; the
other seven remain available:

- **«تراز موجودی»** — physical, Transit and Virtual stock are shown separately. Only physical
  and Transit count toward operational shortage, so Virtual stock never hides a shortage.
- `Warehouse Inventory`
- `Inventory and Procurement Dashboard`
- `Sales Orders Ready for Invoicing`
- `Procurement Shortage`
- `Item Weight and Volume`
- «کالاهای خریداری‌شده و دریافت‌نشده» (`Purchased Not Received`) — one row per submitted Purchase
  Invoice item line, with the physical quantity that reached a sellable warehouse beside the
  accounting receipt, so partial, returned, cancelled and transit-only receipts stay distinguishable

Every value a caller supplies becomes a named parameter. An `IN` list is expanded into one
placeholder per value rather than relying on a driver to expand a tuple, so nothing depends on
the driver's own formatting rules.

Company isolation is applied in the query itself, from the principal's own company list, and
the framework's invoice read conditions are **required** rather than optional: an argument that
defaults to no conditions is one somebody forgets, and forgetting it means a report that ignores
invoice permissions.

The missing report has no default date window. A report that silently covers ninety days answers
a question nobody asked and hides the rows outside it — and nothing else is applied by date. The
only window is one the operator types, so an empty result means no row is short, never that rows
were filtered away. An invoice older than the app is audited like any other.

What the missing report can and cannot know is set out in
[docs/integrity-invariants.md](docs/integrity-invariants.md): it lists rows whose item requires
a serial **today**, on invoices created since the app was installed. Nothing records what the
flag was when an invoice was written, and no historical snapshot has been approved.

## Status

0.3.0 — installed and running on a test site (SmartProcessERP, ERPNext v16), verified there by
hand as well as by the suite: the Desk navigation, the serial dialog, the report, the endpoints,
company isolation against two companies, and an upgrade run twice.

**0.5.1 — GitHub publication of the production-approved 0.5.0 functionality.** The application
source matches the `SmartProcessERP` deployment. This release adds version metadata and release
documentation only. It includes the invoice-first purchase workflow, the `Warehouse Type`
classification with the Material-Transfer-only movement rule, the source-warehouse Item selector,
the five Logistics reports, the Sales Order progress panel, the Sales Invoice warehouse and Item
selectors, and the Workspace «انبار و خرید» permission fix. It does **not** include the paused
Sales Invoice committed-stock UI. The approved hooks are built in `hooks_composition.py`, while
the paused commitment hooks live in `hooks_commitment.py` and are excluded from this release.
Verified on `SmartProcessERP` (Frappe v16, ERPNext 16.35.0, port 8080) by the database-free suite
and the integration proofs in `docs/test-manifest.md`. See `CHANGELOG.md` for the exact scope,
known limitations, and future Persian/Jalali filtering work. The existing `v0.5.0` tag remains
unchanged.

**0.7.1 — 2026-11-16.** The module sidebar nests its links in their Section Breaks, so the v16
Desk panel renders «گزارش‌ها» with all eight reports, «خرید», «انبار», «فروش», «سریال‌ها» and
«تنظیمات»; «کسری سریال فروش» moved into «گزارش‌ها», and `Inventory and Procurement Dashboard` and
`Procurement Shortage` became reachable from the panel. Routes, titles, permissions, both
Workspaces and every operational rule are unchanged. The database-free suite passes: 1659 tests,
2 skipped with `openpyxl`. `main` is untouched.

**0.7.0 — 2026-11-16.** Reconciliation of the phase-2 tree: the Purchased Not Received report,
warehouse classification with stable `physical` / `virtual` / `transit` codes and Persian display
labels, and default-company auto-load on «تراز موجودی», `Warehouse Inventory` and `Purchased Not
Received`; the workspace card and the sidebar report section list the new report. The database-free
suite passes: 1653 tests, 2 skipped with `openpyxl`. `main` is untouched.

**0.6.0 — 2026-11-15.** GitHub publication of the approved tree: «تراز موجودی» as the primary
operational report, Physical/Transit stock separated with Virtual excluded from availability and
shortage, the authorized-stock selectors behind their settings, the Purchase Invoice cancel
guard, per-delivery serial attribution, and the v16 Dock/apps-screen entry. Published from
`release/v0.6.0`; `main` is untouched. The database-free suite passes: 1592 tests, with 12
skipped in the system interpreter. No production deployment is part of this release.

**0.4.0 — a release candidate, not deployed.** It adds the transfer of a cancelled invoice's
serials onto its amendment, and the three restores (see below). None of it is installed on
`SmartProcessERP` and none of it has been migrated anywhere. It has been run end to end on the isolated lab instead — the eight phases of
`docs/integration-test-plan.md`, which create the two DocTypes, move eighteen serials onto an
amendment, race two processes for one row, restore both kinds of file and place the audit records
— and the run found four defects that no database-free suite could reach; all four are fixed and
each has a test that fails without its fix. See `docs/test-manifest.md`, phase 19, and the
`Unreleased` section of `CHANGELOG.md`.

0.3.0 adds the **export and import of this app's records**: two buttons on `Logistics Settings`
that write every serial, every assignment and every relationship they hold into one JSON file, and
read it back after the app has been removed and installed again. The rehearsal of that cycle runs
in an isolated stack, `deployment/lab/`; see its README for the commands a person has to run.

The permanent in-database archive that 0.2.0 added is **gone**, and that is the point of this
version. It kept this app's records in tables of its own so that they would survive a removal, and
it was defended by a hook, and it worked — but it was a second way to keep the same history that a
site's own backup and this export already keep, and two mechanisms that both claim to be the truth
eventually disagree. Removing it took the app's only SQL with it, and the two modules and the
test file that defended it.

The eight DocType definitions, the four roles, the install-time helpers, the pure business logic,
a database-independent import engine, the whitelisted endpoints, one script report and the Desk
interface all exist, and everything that decides anything is covered by tests. The suite runs
with no Frappe and no database: `core/` imports none of it.

### The lifecycle, and why both hooks are registered

`installer.install_app` runs `sync_for` (which creates the tables), commits the app into
`Installed Applications`, and only then calls `after_install`. It **never** calls `after_migrate`.
So a fresh install has to create the composite unique constraints itself — a constraint declared
only in `after_migrate` would be absent on a site that has never been migrated — and a failure
inside `after_install` leaves a site that reports the app as installed with its work half done.
`after_migrate` re-asserts all three steps and is what repairs that.

`seeds.seed_settings` writes `Logistics Settings.installed_on` **last**, so an install that stops
before the end leaves the timestamp empty and is repaired by the next migrate rather than being
mistaken for a finished one. A recorded timestamp is never overwritten, and each seed table is
filled only when it is empty, so a half-written settings row is not seeded twice.

`after_uninstall` is registered, and it is the only hook on the removal path. There is
deliberately **no** `before_uninstall`: a removal is a deliberate act, and the records are
protected by acts that are equally deliberate — an export and a site backup taken first — rather
than by a hook that runs while nobody is looking. A hook there would suggest the records survive a
removal on their own, which is the belief this version removes.

What `after_uninstall` does is tidy up: it removes this app's desktop icon, whose name is Persian
while the framework matches the app's English *title*, and the four roles, which have no link to a
module anywhere. It leaves any role a user still holds alone, and it never raises — by then the app
is already gone, and an exception there would report a completed removal as a failed one.

## Exporting and importing this app's records

**Why a file.** `installer.remove_app` deletes the table behind every DocType whose module belongs
to the app being removed — `_delete_modules` collects them and `_delete_doctypes` issues one
table-dropping statement per DocType. Nothing this app keeps inside its own schema can therefore
outlive its own removal, and a second schema that tries is a second copy of the truth. So the
records travel the way an ERPNext site's records already do: a **full site backup** for disaster
recovery, and a **portable export** for the case where the app itself goes away.

**Where the buttons are.** `Logistics Settings` carries two:

| Button | Endpoints | Level |
| ------ | --------- | ----- |
| «خروجی کامل اطلاعات سریال‌ها» | `export_serials` | Manager |
| «بازیابی اطلاعات سریال‌ها» | `preview_serial_restore`, then `apply_serial_restore` | Manager |

The export becomes a download in the browser and the import reads a file the operator picked.
Neither writes to the server's disk, which is what lets this app run on a stack whose app
directory is mounted read-only.

**What the file holds.** One JSON document, self-describing and sealed with a digest over its own
contents. Every field of both DocTypes — a test compares the format's field list against the
DocType definitions, so a field added later cannot quietly stop being exported — plus the five
framework columns no DocType lists (`name`, `creation`, `modified`, `owner`, `modified_by`). The
relationships are carried as they are stored: the invoice, the invoice row name and its index,
the company, the item, and the serial as typed. Versioned twice: `format_version` for the file's
shape, and `normalization_version` for the rule set that produced the stored identities.

All four provenance columns come back as they were. Frappe rewrites every one of them on an
insert, so the import puts them back afterwards with
`frappe.db.set_value(..., update_modified=False)` — a supported API, no core change, no validation
switched off, and `update_modified=False` is what stops it from rewriting the very timestamp it is
restoring. The three framework-wide flags that would also have worked (`in_install`, `in_patch`,
`in_migrate`) are deliberately not used: each is read elsewhere in Frappe, and one narrow
mechanism is easier to trust than a switch whose other readers live in another file.

**What an import does.** It validates the format, the version and the digest, plans the whole file
against the site, and **shows the plan before writing anything**:

- a row whose identity is already live is **skipped**, which is what makes a second import a
  no-op rather than a duplication;
- a serial whose original name is free comes back under that name; a serial the site already has
  under another name is reused, and the assignment that named it is re-pointed at the row it
  actually became;
- an assignment's company is taken from the **live invoice**, never from the file, and a
  disagreement stops that row;
- a row whose Item, Company, Sales Invoice or invoice row is missing is reported with its reason
  and left alone. This app never creates or edits an Item, a Company or an invoice;
- an invoice that already carries that serial as a *different* serial row is reported as a link
  conflict rather than blessed.

Everything lands in one transaction: the import either places what it can or writes nothing, and
the report says how many rows were created, skipped and refused, with the reason for each refusal.

**Backups, and what they do not cover.** A `bench backup` holds the whole database, so it holds
this app's tables, and restoring it after a removal brings the records back — but only to the state
in that dump, and only onto a site that takes the dump's whole database with it. Three limits are
worth stating:

1. A dump taken *after* the app was removed has no serial table in it at all: the tables went with
   the app. The backup that protects the records is the one taken **before** the removal.
2. `bench backup --with-files` is what includes `private/files`; a dump without it restores the
   database and not the site's files.
3. Restoring a dump is a whole-site operation. It also reverts every other change made after that
   dump, which is why the export exists as the narrower tool: it carries this app's records alone
   and can be imported into a site that has moved on.

So the order before removing the app is: **export**, then `bench backup --with-files`, then the
removal. The export is what a person keeps somewhere else; the backup is what covers everything the
export does not.

## Pure logic

`smartprocess_logistics/core/` holds the rules the app applies, with no Frappe import, no
database, no filesystem, no clock and no network. That keeps them testable before the app is
installed and afterwards on any machine:

| Module | Decides |
| ------ | ------- |
| `normalization` | The text an operator typed, turned into the serial's identity. |
| `delimiters` | How a stored delimiter is interpreted, and how text is split. |
| `markers` | Which invoice row a `{n}` marker names. |
| `quantities` | How many labels a row needs, and how many it is short of or over. |
| `eligibility` | Which invoices the missing-serial report may cover. |
| `assignment` | The values an assignment stores, and the invariant that keeps them true. |
| `archives` | Whether a ZIP container is safe to look inside. |
| `textfile` | Whether a file is text, and which encoding produced it. |
| `spreadsheet` | Which worksheet cells may be read, and which are refused. |
| `importer` | Which invoice row each serial belongs to, and the preview to confirm. |
| `validation` | The registration plan, and the configuration checks. |
| `authz` | Who may reach which endpoint, and which companies. |
| `session` | The import session lifecycle and its legal transitions. |
| `orchestration` | The order a write runs in, and the locks it takes. |
| `reports` | The two report statements, their filters and their company restriction. |
| `defaults` | The delimiters and markers a fresh install starts with. |

Serial identity is case-folded rather than upper-cased, because `casefold` is Unicode's
caseless-matching operation. The stored identity field is read-only and visible, so an operator
can see what a serial matched on; `serial_no` keeps the text exactly as typed.

The rule set was chosen against the database's own collation rather than by preference. The
identity is stored in a `utf8mb4_unicode_ci` column and the unique index is enforced by
MariaDB, so a rule that called two serials different when the database calls them the same
would produce duplicate-key errors the operator cannot explain. `docs/integrity-invariants.md`
records every verdict that was measured, and `tests/test_collation_agreement.py` pins the
property so a later change to either side fails a test.

## Import engine

The engine reads and reports; it never writes. A file goes through a size check, an archive
preflight that reads the ZIP central directory, a bounded cell policy, marker grouping and
the registration plan, and comes back as a preview an operator confirms. Confirmation is a
later phase, and it re-validates against the database before writing anything.

The preflight reads two things besides the directory, and both are measured first. It reads the
package's own content-type map, because a `.bin` part is printer settings only if the workbook
says it is, and it reads the first eight bytes of a `.bin` that claims to be printer settings,
because the bytes are the fact the suffix was standing in for — an OLE container. Every part is
measured — its declared size, its compression ratio, its path, its name — before either read
happens, so a part that is itself a bomb is refused by the rule that bounds every other part
rather than decompressed on the way to a check. Both reads take the member they were given rather
than its name, because a ZIP may hold two members under one name and opening by name answers with
the same one however many times it is asked: a package that hides an OLE container behind a name
it also uses for a benign part is refused on one of the two.

Control and invisible characters are refused rather than deleted. Deleting them made a
separator that was never read as a separator glue two serial fragments into one serial that
looked exactly like a serial somebody had typed.

Sequential imports need the row mapping confirmed. That layout decides which invoice row a
serial belongs to by counting, so a file whose order does not match the invoice would assign
every serial to the wrong row with nothing in the file to say so. The preview shows the mapping
and the engine refuses until it is confirmed.

**A file fills the columns; it does not write.** The server reads the file — only it can read an
`.xlsx` — and answers with the **values** it holds: a workbook's nonempty cells, column by column,
or a text file's whole text. Nothing has been decided about them: no row has been chosen and no
cell interpreted. The window then stages them through the same intake a paste goes through — the
same commands, the same separators, the same judgements — and the one «ذخیره سریال‌ها» writes
them. There is no confirm step and nothing
is written to the database before that save: a file import is a way of filling the columns, not a
second way of registering serials. `prepare_import_preview` reads, answers, and writes nothing at
all. The session it used to store for a second write path is gone from the app, and so is the
endpoint that applied one: in 0.3.0 `confirm_import`, the two plans it needed and the code that
stored a session were removed, because nothing had prepared a session since the window stopped
needing one. A workflow with two write paths and two states is one where an operator can save half
of what they staged without knowing it, so there is one.

The summary the window shows afterwards is a receipt, not a question: how many serials went into
the columns, how many the file held, and every serial it could not use — the server's own refusals
(a marker naming no row, a numeric cell whose leading zero Excel already lost, a duplicate inside
the file) and whatever staging refused on top of them (a duplicate of something already in a
column, or of something this invoice already holds). Removing one is removing a pending cell: the
same control the scanner's labels use.

Sequential imports need the row mapping confirmed. That layout decides which invoice row a
serial belongs to by counting, so a file whose order does not match the invoice would assign
every serial to the wrong row with nothing in the file to say so, and the engine refuses until
that mapping is confirmed.

## Secure API layer

`smartprocess_logistics/api/` holds sixteen whitelisted endpoints and the gateway they drive. They
are reachable as soon as the app is installed, and reachable only through that gateway.

| Endpoint | Needs | Does |
| -------- | ----- | ---- |
| `get_invoice_serial_status` | Viewer | The rows of one invoice and how many serials each has |
| `get_serial_assignments` | Viewer | One page of the invoice's assignments |
| `set_row_serials` | Editor | Set one row's serials: add, replace and remove |
| `add_row_serial` | Editor | Add one serial to one row, keeping whatever else the row holds |
| `add_row_serials` | Editor | Add one row's labels in one request, whole or not at all |
| `delete_assignments` | Manager | Remove assignments by name |
| `prepare_import_preview` | Editor | Parse a TXT or XLSX payload and report what it holds |
| `export_serials` | Manager | Every serial and assignment in the site, as one downloadable document |
| `preview_serial_restore` | Manager | Validate an exported document and report what importing it would do |
| `apply_serial_restore` | Manager | Create the rows this site can place, in one transaction, and report the rest |
| `get_sellable_stock` | Viewer | Sellable, sold-awaiting-dispatch and total physical stock for one item and warehouse |

Every decision lives in `core`, which is why the authorization, the reconciliation, the replay
rules and the failure behaviour are tested without a site. An endpoint only parses arguments,
builds a principal, and wires the two together. No endpoint uses `ignore_permissions`, and none
of them commits.

One more whitelisted read lives outside this table: `warehouse.item_search.items_in_stock`, the
Stock Entry Item Code selector. It is a Frappe Link query rather than a serial endpoint, so it
is gated by ordinary Frappe permissions — Warehouse read and company on the named warehouse,
Item read, and `get_match_cond("Item")` for role and Item user permissions — not by the app's
Viewer/Editor/Manager levels. In a Material Transfer it offers only Items with positive
`actual_qty` in the row's source warehouse, which ERPNext 16 does not do natively. Its statement
is built in `core/stock_search.py` and it writes nothing; the standard submit still decides
whether the movement may happen.

## Virtual warehouses

A warehouse is **virtual** when its standard `Warehouse Type` is one of `Virtual`, `مجازی` or
`Transit` (a site may override the list with `sp_virtual_warehouse_types`). Every other leaf
warehouse is physical. The stock in a virtual warehouse is ordinary ERPNext inventory: it has a
Bin, a value and Stock Ledger Entries, and it counts in company assets. It is never sellable,
deliverable or reservable.

Create any number of virtual warehouses from the standard Warehouse form, set their type, and
move stock with the standard Stock Entry **Material Transfer** in all four directions. No
custom arrival API, destination whitelist, purchase-receipt link or new field is involved.
Virtual stock may only be moved by Material Transfer or received by a standard Purchase Receipt;
an outbound Sales Invoice or Delivery Note, or a Stock Reservation Entry, that names it is
refused server-side. The virtual-warehouse guard does not narrow returns; existing managed-
workflow rules still apply. Quotation and Sales Order selectors show physical stock and Transit
stock separately; Virtual stock is excluded from their availability and warehouse choices.
Transit is orderable but is not deliverable until a Material Transfer puts it in a physical
warehouse.
Purchasing is invoice-first for every domestic and foreign stock purchase: the Purchase Invoice
bills the full amount with no stock, and every stock row of the Purchase Receipt must link a
submitted Purchase Invoice. The required order is:

1. Submit the Purchase Order (planning only; it does not create stock).
2. Submit the Purchase Invoice with `update_stock = 0` (billing only; no Stock Ledger Entry).
3. Submit the Purchase Receipt against that invoice row, in whole or in part. Receipt rows
   choose the receiving warehouse; a rejected quantity must name a Virtual warehouse.
4. Move Transit stock to a physical warehouse with a standard Material Transfer before it can
   be delivered or sold.

A Purchase Receipt without a submitted Purchase Invoice is refused by design; this is not the
standard ERPNext PO-to-receipt order. A linked stock row must use the invoice row's UOM and
conversion factor, so ERPNext's `received_qty` and `per_received` stay consistent. The receipt
may receive any part of it into a virtual or physical warehouse. Service/non-stock rows, fixed
assets and opening entries keep their standard workflow.
A Purchase Invoice Return/Debit Note may be financial-only (`update_stock = 0`); stock returns
must use a Purchase Receipt Return.

A submitted Purchase Invoice cannot be cancelled while a submitted Purchase Receipt still
depends on it. The Cancel button and the REST/API cancel path both run the app's `before_cancel`
guard, which refuses the cancel and names the blocking receipts; cancel the receipt first, then
the invoice. The guard reads the receipt row's `purchase_invoice` and `purchase_invoice_item`
links and, on a site that added one, a receipt-header link; a receipt of another company neither
blocks nor is named. A draft or cancelled receipt never blocks. The guard locks the invoice row
before it reads receipts, and the receipt validator locks the same row before it reads the
invoice, so a concurrent receipt submit and invoice cancel cannot both pass. ERPNext's own
back-link check remains the backstop when the guard is bypassed.

`set_row_serials` and `add_row_serials` differ in what they do to a row that already holds
serials, and the difference is deliberate. `set_row_serials` names one row and **replaces** it:
the set it sends is the set the row ends with. `add_row_serials` adds one column's labels and
**keeps** whatever the row already had, which is what the window needs: a paste or an import that
completes a partly registered row must not drop the labels another operator registered there. An
operator who wants a serial gone asks for it, with `delete_assignments` or with a shorter list.

`add_row_serial` adds, for one row and one serial, and it exists because of the scan station in
`public/js/sales_invoice_serial_button.js`. A barcode scanner sends one label per keystroke run,
several to a second, and two operators may be scanning into the same invoice. An endpoint that
replaced the row's set would have each of them send the set it believes in, and the later one
would drop the earlier one's labels — so the station adds instead, and reads the row's counts
back from the answer rather than counting for itself.

`add_row_serials` is that same act for a column's worth of labels, and the station's save uses it:
one request per column instead of one per label. It is not a different rule — `build_append_plan`
takes a tuple of labels and hands them to the same wall of checks as a single one, in the order
they were scanned — and it is not `set_row_serials`: it names no row to replace, so a batch can
only add. What a batch buys is one answer for one column: **accepted whole or refused whole**, so
a column cannot end up holding the first half of what an operator asked for while a failure is
reported about the second half. A refusal names the serials it is about, because with a batch the
sentence has to say *which* label to remove.

**An operator needs to be able to read the invoice.** Every write and every read of the station
passes `gateway.assert_can_read_invoice`, which asks the framework — deliberately, because a
serial belongs to an invoice its operator has to be allowed to see. On a current ERPNext, that
means a role that grants read on `Sales Invoice` (`Accounts User` or `Accounts Manager`, measured;
`Sales User` alone does not) in addition to a Logistics role.

**A missing invoice and a forbidden one give the same answer.** `core.authz.INVOICE_ENDPOINTS` is
every endpoint that is about one invoice — named by the caller, or by the session being confirmed —
and all of them check it whether or not one was found. An endpoint that checked only the invoices
it had found answered `فاکتور یافت نشد` for a name that does not exist and `به این شرکت دسترسی
ندارید` for one that does, which is two answers where the design promises one; a caller who can
tell them apart has a way to work out which invoice names are real. The write endpoints were the
ones leaking it, because they handed the invoice to `core.authz` only when the lookup had found
something.

**A cancelled invoice may be read and emptied, and may not be added to.** `core.authz` refuses
every registration endpoint for an invoice whose `docstatus` is 2, and allows every read and the
removal of what it already holds — a registration added to an invoice that was later cancelled is
exactly the mistake somebody has to be able to see and undo. The state is read from the stored
document, never from the request, and it is re-read **under the invoice lock** by
`core.orchestration.run_rewrite`: the endpoint's decision was about the invoice as it was a moment
ago, and an invoice cancelled while the request waited must not receive a registration. The
window has the same rule, which is where it used to live alone — a rule that lives only in the
window is one an API call does not have.

A write that loses a race is refused with a sentence rather than a MariaDB error. Two requests
touching one invoice, session or serial at the same instant leave MariaDB to abort one of them
with `QueryDeadlockError` or `QueryTimeoutError`; nothing of the aborted request is written, and
`core.orchestration.is_retryable` answers that sending it again is the remedy, which is what the
three write endpoints tell the operator.

Serials sent to `set_row_serials` as text are split by `core.delimiters.split_typed_list`, which
accepts the configured delimiters **plus** a comma, a semicolon, an Arabic comma, a line break
and a tab. Serials read from a file are split by the configured delimiters alone, so a line
`A,B` in a file is one serial named `A,B` unless a comma is configured. The difference is
deliberate and stated once, in `core`, because a list typed by hand is separated with a comma by
habit whether or not the configuration says so.

That difference is the **server API's**, and it is the only one left: in the window a file and a
paste are split by the same served list, because a file's values are staged by the same `intake` a
paste goes through. So a cell holding `A,B` is two serials in the window whichever way the operator
brought it, and it is one serial named `A,B` only for a caller posting text to `set_row_serials`.

**Three ways in, one set of rules, one place to press.** A scanned label, a pasted list and an
imported file all end at the same `intake` in
`public/js/sales_invoice_serial_button.js`, which splits what a list is, reads what a command is,
judges each serial on its own, refuses the duplicates this invoice already has, and stages the
rest in the columns the operator is working in. Nothing is written by any of them: the one
«ذخیره سریال‌ها» writes, through `add_row_serials`, and it writes what the columns held at the
moment it was pressed. The server repeats every one of those judgements — `core.validation` is
where the quantity limit, the duplicates and the identity of a serial are finally decided — so
the window is a convenience and not an authority.

Two commands, recognized only as a whole token: the site's own next-column command, and `*N` or
`#N` — the same command written two ways — which select the invoice row the columns show as `#N`.
`N` is any positive whole number, however long. A serial that merely contains either is a serial:
`AB*1C` is a label, and the separators are what ends a token. A number this invoice has no row for
is answered with a sentence and changes nothing — the values after it stay where they were, rather
than being filed somewhere nobody asked for.

The separators a pasted list is split by are served with the invoice, interpreted by
`core.delimiters.served_delimiters` — the site's configured delimiters plus the ones a typed list
uses — so the window splits on the characters this site splits on rather than on a second opinion
about what a separator is.

**Everything aimed at a column begins in that column.** A scanned label, a typed label and a pasted
list all start in the input they were given to, because clicking that input is how the operator says
which product the serials belong to; a command in the text moves them from there, and may move them
again. A **file** is the one input that has no column of its own — the picker is in the window's
footer — so a file's values begin at invoice item row 1 and are moved by their own commands. The
difference shows only in a value no command names: with the operator's own workbook, the one cell
of the column that carries no command is staged in the row the file or the paste began in, which is
row 1 for the file and the clicked row for a paste.

**Text is decoded before it is split, and that is what keeps a table a table.** Everything that
arrives as text — a paste, a `.txt`, a cell of a workbook — goes through one step,
`smartprocess_logistics.decode_intake_values`, before the intake reads anything. Two shapes come
out of it:

- A **table**, which is what a copied range of cells is: rows are lines, columns are the tabs
  between them, and the values are read **column by column, top to bottom** — the same order a
  workbook is read in. An operator writes the command for a column at the head of that column, so
  `*1` over five serials and `*2` over ten belong to different invoice rows. Read the other way —
  across, row by row — the first line's commands are all consumed before a single serial arrives
  and every serial lands in the row the **last** command named, which is the defect this step
  exists to prevent.
- A **list**, which has no shape to lose: it is returned whole and the site's separators take it
  apart, so a scanned label, a typed line, a column of cells and a comma-separated list all keep
  the reading they have always had, and one command may switch the row as many times as it likes.

A shape has to **span rows** to be a shape: two or more lines carrying a tab. One line with a tab
in it is a list whose separator happens to be a tab — and for a single line the two readings agree,
so nothing is decided by the guess. Newlines alone are never a table; a column of cells copied out
of a spreadsheet is one value per line.

**A workbook is read column by column, top to bottom.** `A1, A2, A3 … B1, B2, B3 …`, and only the
cells that hold something: blank cells are not values, not counted, and never become a serial, and
a worksheet somebody formatted to the bottom of the sheet is read for what is in it rather than
refused for the size of its formatting. Each nonempty cell is one value, and a cell holding several
separator-separated tokens is split like any other list — which is what makes the first row of a
column carry the command for the serials beneath it, exactly as an operator arranged it. A cell in
a workbook and the same cell pasted into a box are therefore the same value read the same way, and
the values a workbook returns are exactly the sequence the clipboard copy of the same range
decodes to.

An identity that already exists in the catalogue is not a duplicate: reusing a serial this site
knows, on an invoice it is not already on, is a reuse, and the answer says so (`serials_reused`)
rather than refusing it.

## The scan station

**«سریال‌ها» on a Sales Invoice is one button, and one left-click opens the station.** It used to
be a dropdown holding «ثبت سریال» beside a read-only «جزئیات سریال‌ها» entry, which made the window
the operator always wants sit behind a second click; the details window itself is unchanged and is
simply no longer on the toolbar. The button is drawn by the form's own `refresh` — so it comes back
after a reload or a navigation — and it appears only where registration is possible: not on a
cancelled invoice, not for a user without the Editor role, not when the settings switch is off, and
not for a company the acting user's permissions do not reach.

The station opens with one column per **serial row** — the invoice item rows whose item is marked
`sp_serial_required`, and no others: a row that cannot take serials has no column, no place in the
navigation, and no way for a paste, an import or a command to reach it. Each column carries the
model on one line of its own — clipped at its **beginning** when it does not fit rather than
wrapped, with the whole name in the tooltip, and the row number underneath it and quieter than
it — then the counts, an input that stands ready for a barcode scanner, and one cell per serial:
solid for a serial the invoice already holds, **dashed for one
that has only been scanned**. A column turns green with a tick when the server reports it complete,
and it can be emptied as a whole from the control at its foot, under the serials it removes, after
an explicit confirmation naming the row.

- **A scan is kept, not written.** The window holds the labels an operator scans, in the order they
  scanned them, and writes nothing until they press **«ذخیره سریالها»**. This is what makes an
  over-quantity scan as ordinary as any other: nothing is refused, nothing interrupts, and the
  scanner keeps its rhythm. The save sends a column's labels in one request to `add_row_serials` —
  the same rule, the same authorization, the same server-side limit, applied to the batch instead
  of to each label — so the invoice is written by the same rules whether a label arrived one at a
  time or twenty at once. Scanning far past the quantity is therefore possible on screen and
  impossible in the database.
- **The column heading is one line, and it is the model rather than the code.** `Item.item_name`,
  truncated at its **beginning** when it does not fit — the end of a model name is the part that
  tells two of them apart — with the whole name in the tooltip. A header that grew to fit a long
  name would push its own counts and input down the column and lose the operator's place, so it
  does not grow: it clips, and the clip is at the side that keeps the lenses.
- **A paste lands in the columns, and a pasted table keeps its shape.** A paste is decoded first
  (see above): a **table** — two or more lines with tabs, which is what copying a range of cells
  gives — is read column by column like a workbook, so a command written at the head of a column
  rules that column and no other; a **list** — one line, or lines without tabs — is read
  sequentially, and a command in it moves the cursor for everything after it and may move it again.
  Each token is judged on its own, so a label already staged, a label this invoice already holds or
  a value that is not a serial at all is refused with a sentence while the rest of the list is
  staged. A paste of **one** value is left to the browser: that is a serial somebody copied and is
  about to read before pressing Enter, and taking it over would surprise them. A paste writes
  nothing, so pasting the same table twice stages nothing the second time — every serial in it is
  refused as a duplicate of one this invoice already holds unsaved. The same column redraws once —
  staging touches no DOM — so a list of two hundred costs one redraw per column that changed rather
  than two hundred of a growing column.
- **A scanner that ends no label is still understood, and never split.** A wedge can be configured
  with no Enter or Tab, and its keystrokes arrive as one unbroken run of `input` events: the second
  label used to be appended to the first. The window times the run — a burst that reaches
  `WEDGE_MIN_LENGTH` characters within `WEDGE_BURST_MS` and then stops for `WEDGE_SETTLE_MS` is
  filed as one label — so typing at human speed is never filed behind the operator's back and never
  split, while two scans a scanner produced are two labels. This is a rule about the shape of the
  input, not a delay in place of one: nothing sleeps waiting for a scanner, and a label the scanner
  *did* end is taken the moment it is ended. Its limit is the limit of the idea: a wedge in
  continuous mode that ends nothing **and** starts its next label with no pause at all produces one
  unbroken run of keystrokes with no gap in it to split at, and that is a scanner to configure with
  a suffix.
- **The excess is refused by the window before the server sees it.** Everything scanned past the
  invoice's quantity is red, the column says how many, and pressing save with any row over its
  quantity writes **nothing at all** — the whole batch is held back and the message names the item
  and its excess, so the operator fixes one row instead of repairing a half-written invoice. The
  server's own limit is untouched behind it, and a save that is partly refused says so, column by
  column — one line per refused column, naming its product and every label it covers — and never
  reports a completion it did not achieve.
- **The counters are the server's, plus what is in hand.** `ثبتشده` is what the invoice holds,
  `ذخیرهنشده` is what is waiting on screen, and the remaining and excess counts are worked out over
  the two together, so the operator sees both what they have scanned and what has actually been
  saved. Nothing here decides anything the server decides again.
- **One state per row: gray, green, blue, red.** Every card is drawn from one function —
  `core.eligibility.row_state`, and the window's own copy of it — from four numbers: how many labels
  the row needs, how many the invoice allows, how many the database holds, and how many the operator
  has in hand. **Gray** is short of what it needs. **Green** is exactly what it needs with some of it
  not yet written: the operator's own work, and the reason the state exists as its own colour rather
  than as a shade of done. **Blue** is exactly what it needs with nothing in hand — the answer the
  server gave back, and the only state that shows the tick or rings the chime, because only that one
  is the invoice's. **Red** is more than the invoice allows, and it outranks the rest: a row that has
  to be fixed is not also nearly finished. Saving a green row turns it blue from the server's own
  answer, never from an assumption; scanning into a saved row, or removing one of the labels in
  hand, recomputes the state on the spot.
- **Reading a file says so while it is being read.** The bytes are read by the browser, sent, and
  parsed on the server, and that is long enough for an operator to press the button again. While the
  request is out, «لطفاً منتظر بمانید؛ فایل در حال پردازش است…» stands where the file's result will
  appear, the picker and the button are disabled, and a second press asks for nothing — then the
  receipt replaces the notice and the controls come back, from a `finally`, whether the read
  succeeded or failed. A refusal answers in the same place with the server's own Persian sentence
  and a line saying the read is over, so the next press tries again rather than being swallowed by a
  notice that never left.
- **The busy state is the station's own.** It does not use `frappe.dom.freeze`. Desk's overlay is
  counter-based (`frappe/public/js/frappe/dom.js`): `unfreeze()` returns early when the counter is
  zero, so one unpaired `freeze()` — and the station had three ways to leave one — makes every
  later `unfreeze()` a silent no-op and pins the grey layer over the page until a reload. The
  station shows its progress in its own toolbar, disables only the column it is writing to, and
  releases both from a `finally` that runs even when the work throws before it returns a promise.
- **The chime.** A short, quiet two-note tone from the Web Audio API — no asset, no library —
  plays when a column *becomes* complete, judged from the server's own answer. It never plays when
  the window opens on finished work, and a column emptied and filled again rings again. The switch
  beside it is remembered per user in the browser's storage, and a browser that refuses audio
  leaves the interface untouched.
- **The focus.** A scan empties the input and puts the cursor back in it — so twenty labels in a
  row need no click, and none of them waits for a server answer, because none of them is a write.
  A label the column already holds is answered in the warning line and not added twice; a label the
  invoice already carries is refused when the save reaches the server, and stays on screen until
  the operator removes it.
- **The same model in two invoice rows** is two columns, because a column is an invoice *row*.
  Every serial keeps its own row key, so the two never share a cell.
- **The next-column command.** A scanner can be programmed to send one value that moves the cursor
  to the next column instead of registering: `Logistics Settings` → *کاراکتر ردیف بعدی*, `**` by
  default. The match is the **whole** scanned value — a serial that merely contains `**` is a
  serial — and the command is never sent to the server, never queued and never stored. Past the
  last column the cursor returns to the first, and a column is skipped only because it is not next:
  a complete column is still reachable, because the operator chooses where to go. The value is
  served with the rest of the window's data, already checked against the site's own separators and
  serial length, so a site whose setting cannot be matched navigates on nothing rather than on the
  wrong thing.

Company isolation reads the company **off the stored invoice**, never from the request, so a
caller cannot reach another company's invoice by naming it. A missing invoice and a forbidden
one produce the same answer, so a caller cannot probe for names.

A write is one transaction. The claim, the reconciliation, the deletes and the inserts all ride
in it, and the request boundary ends it — which is what makes a failure roll the claim back and
leave the session usable, and what makes a second request for the same session block and then
see that the first one applied it.

What the mocked tests prove is the **plan**: which calls happen, in which order, and which are
skipped when a check refuses. What they cannot prove is the database's own behaviour — that a
row lock blocks a second request, that MariaDB rolls back, that the unique index rejects a
duplicate. Those are listed as `NEEDS_A_DATABASE` and stay unverified until the app runs.

## Moving serials onto the amendment

ERPNext amends a Sales Invoice by cancelling it and writing a new one with **new** child-row names.
The cancelled invoice keeps the registrations a warehouse scanned onto it, and the amendment reads
as a shortage until the same serials are registered on it — so an amended invoice carries a second
button, «انتقال سریال», which moves them.

What it does, in the order an operator meets it:

1. **A preview, and a confirmation.** The window asks `preview_serial_transfer` what would happen
   to **every** serial the cancelled invoice holds and draws them in the invoice's own order — not
   grouped by outcome, because a person checking eighteen serials against the paper in front of
   them is reading one list. Each row says whether it would move, needs an answer, or cannot move
   and why. Nothing is decided in the browser: a tick, an untick and an acknowledgment each ask the
   server again, and what the operator confirms is the answer that came back.
2. **Only what the amendment can take.** A row may never hold more serials than its invoiced
   quantity (I20), so an amendment that invoices less than its predecessor moves what fits and
   reports the rest with a reason. A serial the amendment already holds is refused rather than
   duplicated. A serial that is also on another **live** invoice is not an error — this app has
   never had a site-wide uniqueness rule over serial text — so the operator is asked to acknowledge
   it, and the acknowledgment is what the record keeps.
3. **The cancelled invoice is never written to.** Its registrations are read, named in the record,
   and left exactly as they were. Every serial it held ends up placed on the amendment, refused
   with a reason, or left because the operator did not select it — and the record says which.
4. **A submitted record of the attempt.** `Logistics Serial Transfer` carries one child row per
   serial a decision was made about. Pressing the button twice writes two records, because each
   press is an attempt, and an attempt that moved nothing is still worth recording: what it says is
   which serials could not go and why.

The apply revalidates everything under the amendment's own lock and **re-plans against the
database**: the preview's token proves only which selection was confirmed, never that the site
still looks the same. Two operators pressing at the same instant produce one transfer and one
refusal reading «درخواست دیگری هم‌زمان…», and the loser writes nothing at all.

### The three restores

`Logistics Settings` reads an exported file back in three ways, and they are disjoint by design
(`core.export.MODES`):

| Button | Places | Refuses |
| ------ | ------ | ------- |
| «بازیابی سریال‌ها» | serials and assignments on **live** invoices | a row on a cancelled invoice, naming the historical path instead |
| «بازآوری تاریخچهٔ فاکتورهای لغو‌شده» | assignments on **cancelled** invoices | a row on a live invoice, as `NOT_HISTORY` |
| «بازآوری سابقه‌های انتقال» | the audit records themselves, with their rows | a name this site already uses, a target invoice that is cancelled now, a serial this site does not have, and a file that disagrees with itself |

The historical restore is the only path in this app that may add to a cancelled invoice — I22 has
exactly one exception, and this is it — and it asks for the `Logistics Serial Archivist` role **on
top of** a Manager level, so a site with several Managers decides which of them may do it. The
records restore marks what it places: `restored_from`, `restored_on` and `restored_by` are written
by the restore and by nothing else, so a restored record cannot claim the act happened here, while
`owner` and `creation` still name whoever performed it.

## Desk navigation

The app ships one visible sidebar, titled «سریال لجستیک», plus a second `Workspace Sidebar` named
exactly as the module **with no items**. That second file is not decoration and is not an entry:
`boot.get_sidebar_items` asks `auto_generate_sidebar_from_module` for a sidebar for every module
that has none named after it, and the generated one lists the module's DocTypes under their
English names — which is how a second entry appeared beside the Persian one. A sidebar with no
real item is skipped later in the same function, so the module-named file suppresses the
generated one and shows nothing itself. `tests/test_desk_artifacts.py` pins both halves so the
pair cannot be edited apart.

The panel header shows that sidebar's title, and, underneath it, the app's own name, which the
framework reads from the app data rather than from any field this app owns.

The v16 app entry is `dock/smartprocess_logistics/`, the standard Dock record, together with
`add_to_apps_screen` in `hooks.py`. `desktop_icon/لجستیک.json` remains as a compatibility entry
for sites that still render the legacy icon grid, and links the «انبار و خرید» Workspace
Sidebar. The `SmartProcess Logistics` Sidebar shown with that workspace is also part of the app.

The module panel v16 actually renders comes from
`smartprocess_logistics/sidebar/smartprocess_logistics/smartprocess_logistics.json` — a standard
`Sidebar` named exactly as the module, which `boot.get_module_sidebars` resolves and ships in the
boot payload. Its rows are flat in the database; the browser nests them, following each link's
`child` flag (`sidebar.js nest_section_items`), and a Section Break with no nested link returns
before drawing anything (`sidebar_item.js TypeSectionBreak.make`). So **a link below a Section
Break must carry `child: 1`** — at `child: 0` it disappears from the panel with no error, which is
what hid «گزارش‌ها», «خرید», «انبار» and «فروش» between 0.7.0 and 0.7.1. All eight Logistics
reports now sit under the collapsible «گزارش‌ها», and «انبار و خرید», «سریال لجستیک» and
«تنظیمات سریال» stay outside it. `TestModuleSidebar` in `tests/test_desk_artifacts.py` holds both
rules.

## Approved scope

- Serial registration belongs to `Sales Invoice Item` rows.
- Registration is allowed on existing invoices, including older ones, subject to permissions.
- A serial number is unique per Item identity and may be reused on different invoices.
- The same Item and serial number may not appear twice within one invoice.
- The missing-serial report covers every invoice the site holds, whatever its age. What makes
  a row a shortage is its **item's** `sp_serial_required` flag, read when the report runs: a
  marked item needs one label per unit, an unmarked one needs none and can never be short.
- Cancelled invoices are excluded from the missing-serial report by default.
- Permissions use three levels — Viewer, Editor and Manager. Record ownership is not the
  primary restriction.
- The app must not change accounting or stock ledger behaviour.

The per-row historical requirement snapshot is **not approved yet**. It is not implemented,
and no `Item` hook is registered for it.

## Data model

| DocType | Kind | Holds |
| ------- | ---- | ----- |
| `Logistics Settings` | Single | Feature switch, install timestamp, import limits, the active delimiters and item markers, and the normalization rule set in force (which follows the code rather than the install date) |
| `Logistics Serial` | Master | One row per serial number, unique per Item |
| `Logistics Serial Assignment` | Transaction | One row per serial registered on one Sales Invoice item row |
| `Logistics Import Delimiter` | Child | A delimiter that file import splits on |
| `Logistics Item Marker` | Child | An item marker pattern such as `{n}*` |
| `Logistics Import Session` | System | A one-time import preview awaiting confirmation |
| `Logistics Serial Transfer` | Submittable record | One row per transfer attempt: the two invoices, the company, the five counts, the token of the selection that was confirmed, and — when the record arrived from another site — where and when that happened |
| `Logistics Serial Transfer Item` | Child | One row per serial a transfer decided about: the source registration, the target row, `Moved` or `Refused`, and the reason |

Serial identity is the pair `(item_code, normalized_serial)`, enforced by a composite unique
constraint. `serial_no` keeps the text exactly as it was entered, including leading zeros, for
display; `normalized_serial` is the case-folded, digit-folded form used for identity, and
`core.normalization` is written to be at least as coarse as the `utf8mb4_unicode_ci` collation
the column uses — which is what keeps the application rule and the database rule identical. A
third column on the serial, `normalization_version`, records which rule set produced that
identity.

A serial may be registered on several invoices. A second constraint on
`(sales_invoice, normalized_serial)` — the copy of the identity the assignment carries, because
a unique constraint cannot reach through a link field — forbids the same serial twice on one
invoice, which already covers twice on the same item row.

An assignment points at a Sales Invoice item row by that row's own name, not by its position.
If a draft row is deleted and replaced, the assignment becomes orphaned; orphaning is derived
by a join at read time and is never stored as a flag.

`Logistics Import Session` grants `create` to the Editor and Manager roles and `read` and
`delete` only to System Manager. Preparing a preview inserts a row, so `create` is what makes
file import possible for an operator at all; nothing in the app may *read* the table as a user,
because a row carries the token a confirmation is claimed with and the payload the preview
accepted. A user cannot confirm somebody else's session — `authz.require_session_owner` decides
that — and a session forged by hand still has its registration validated against the database
before anything is written.

The transfer record is the one document here that is **inserted as submitted** rather than
inserted and then submitted, and the reason is the framework's order of checks: `Document._save`
asks for the `write` permission before it reads an explicit `docstatus`, and no role has `write`
on this DocType — so a record is written by asking for `create` and `submit` and nothing else.
What protects it afterwards is the framework rather than this app: a submitted document cannot be
changed (`_validate_update_after_submit` compares every field with the stored copy) and cannot be
deleted by anyone, the Administrator included. Its five count fields must agree with its own rows,
and `core.export.check_transfer_records` refuses an archive whose counts disagree, whose child
names a record the file does not carry, or whose record is not submitted.

## Permissions

Three roles, created by the app and assigned by an administrator:

| Role | Logistics Serial | Assignment | Settings | Reports |
| ---- | ---------------- | ---------- | -------- | ------- |
| `Logistics Serial Viewer` | read | read | read | read, export |
| `Logistics Serial Editor` | read, create, write, delete | read, create, write, delete | read | read, export |
| `Logistics Serial Manager` | read, create, write, delete | read, create, write, delete | read, write | read, export |

No DocPerm carries `if_owner`, so an Editor may correct a registration somebody else made.
None of these roles is granted anything on `Sales Invoice`, `Item` or any other ERPNext
DocType. Company isolation comes from the `company` link on the app's own DocTypes, which the
framework's User Permission rules apply automatically on every permission-aware query; the
whitelisted APIs added later enforce the same rule explicitly, together with read access to
the invoice itself.

## What makes a shortage, and which rows a serial goes on

One rule, stated in `core/eligibility.is_eligible` and applied in three places:

| `Item.sp_serial_required` | registration | a shortage | in the report | in the scan window |
| --- | --- | --- | --- | --- |
| `1` | required, one label per unit | possible | yes, while short | a row: drawn, navigable, and the only kind a label may be written to |
| `0` | neither offered nor refused | never | never | no row at all — and a command naming it is answered, not obeyed |

The report's statement carries `i.sp_serial_required = 1`, the scan window serves only rows the
predicate returns true for, and `core.validation` refuses a registration anywhere else with
`ROW_NOT_ELIGIBLE`. A row that is not marked is **not a serial row**, so it cannot be worked in
from this window — which is also what makes the report and the station agree about the same
invoice instead of one calling a row short in a place the other does not draw.

**Hiding a row is not a way of losing what is on it.** A row that stops being marked keeps every
registration it already had: nothing deletes, rewrites or hides them. They stay in
`Logistics Serial Assignment`, they are counted in the payload (`ineligible_rows`,
`ineligible_registered`) so the window can say the invoice has such a row, they are listed by
`get_serial_assignments` like any other, they are in the Desk list, and `delete_assignments`
still removes them — the refusal is about **adding** a label, and a removal does not go through
the registration path at all. The read-only window `open_details_dialog` still exists in the app
for exactly this: it lists every registration of an invoice whatever its row's item is marked as.

Neither the report nor the window reads `Logistics Settings.installed_on`: an invoice created
before the app was installed is audited like any other, and stays audited across an upgrade, a
reinstall, a restore, a move to another server, or a change to that timestamp. The timestamp
keeps the one job it ever really had, in `setup/seeds.py`: recording that the app's own seeding
finished, so a half-finished install is completed and a finished one is left alone.

The flag is read as it is **now**, not as it was when the invoice was written, so marking an
item adds its rows to the report and unmarking it removes them. That is the rule, not an
accident: an item nobody marks as needing a serial cannot be short of one.

## Requirements

- Frappe **16.x** and ERPNext **16.x**, declared as `>=16.0.0,<17.0.0`.
- Python 3.10 or newer.

The app declares one runtime dependency, `openpyxl`, because the import feature reads `.xlsx`.
Every other module uses the standard library only, and `core/` uses nothing else at all — which
is what makes the core suite runnable without a bench.

## Installation

Frappe v16 and ERPNext v16, and `openpyxl` for the XLSX reader — nothing else. No site name,
host, port or container path is compiled into the application.

```bash
bench get-app <repository-url> smartprocess_logistics
bench --site <site> install-app smartprocess_logistics
bench build --app smartprocess_logistics
```

**`bench build` is required on a normal bench, and there is nothing to configure.** The app ships
its stylesheet as a plain asset path (`app_include_css = /assets/smartprocess_logistics/css/…`)
rather than as a bundle, so Frappe resolves it through `sites/assets/<app>`, the symlink
`bench build` creates. On a bench that has never built the app, the scan window opens **unstyled**
and the log records a 404 for that one URL; the form script itself is unaffected, because Frappe
inlines `doctype_js` into the DocType's own metadata rather than serving it as an asset (verified
in v16: `desk/form/meta.py::_add_code`). A bench that installs the app and never builds it is a
working app with an unstyled window, not a broken one — and one command fixes it.

**The Persian module label needs that build, or one command.** Frappe v16 reads translations from a
compiled catalogue (`sites/assets/locale/fa/LC_MESSAGES/smartprocess_logistics.mo`) and never from
`translations/*.csv`, so the app ships `locale/fa.po`, and `bench build --app smartprocess_logistics`
compiles it. On a stack that mounts `public/` instead of building, compile the catalogue directly:

```bash
bench --site <site> compile-translations --app smartprocess_logistics --locale fa
```

Either way the `.mo` lands in `sites/assets`, inside the site's own volume, so it survives a
container recreate. A site without it shows the English title; nothing else depends on it.

`deployment/compose.smartprocess-logistics.yaml` is optional site wiring for one Docker stack: it
mounts `public/` over `assets/smartprocess_logistics`, which is why a stack wired that way needs no
build step. The application works without it.

### What the install does

Four idempotent steps, in `install._ensure_app_state`, and both `after_install` and `after_migrate`
run them: create the four roles, apply the two composite unique constraints, bring
`Logistics Settings.normalization_version` level with the code, and seed the delimiters, the item
markers and `installed_on`. `installed_on` is written last and only once, so a failure part way
leaves the timestamp empty and the next attempt repairs the site instead of finding a settings row
that claims to be complete.

Both hooks exist because Frappe v16 calls them at different times: `installer.install_app` runs
`after_install` and **never** runs `after_migrate`, so a constraint declared only in the second
would be missing on a site that has never been migrated.

### Upgrade

```bash
bench --site <site> migrate
```

The DocType sync and both lifecycle hooks are idempotent, and that was measured rather than
assumed: on a site running this app, running the whole upgrade path twice left the field counts,
the DocPerm rows, the four roles, the settings values and the record counts identical. A field
removed from a definition is dropped by the same sync; the single value it left behind in
`tabSingles` has to be removed by hand, which is what 0.1.0 did for
`require_preview_confirmation`.

A migrate creates nothing of its own beyond the DocTypes, the constraints, the four roles and the
settings single. An earlier version also carried a schema of its own for a permanent archive; that
is gone, and a site upgraded from it has nothing to clean up by hand — those tables belonged to the
app and went when the app's DocTypes were dropped. What still has to be removed by hand is the one
value 0.1.0 left behind, described above.

**Upgrading to 0.3.0 from 0.2.x removes an endpoint and changes nothing in the database.** The
`Logistics Import Session` DocType and its rows are untouched, and the daily sweep keeps expiring
them; what is gone is the code that wrote and applied one. A site that has rows in that table keeps
them, and they are read-only for this app.

### Uninstall

```bash
bench --site <site> uninstall-app smartprocess_logistics
```

This drops the eight DocTypes and the records in them — serial numbers and the assignments that
link them to invoice rows, the transfer records and their rows, the settings single, and the two
configuration child tables — and removes the app's sidebar and desktop icon through
`frappe.utils.install.delete_desktop_icon_and_sidebar`.

There is no preservation hook, and that is deliberate: the records in those tables go with them.
What must be taken **before** the removal is an export and a backup — «خروجی کامل اطلاعات
سریال‌ها» on `Logistics Settings`, then `bench backup --with-files` — and the order matters,
because a dump taken afterwards has no serial table in it at all.

`install.after_uninstall` runs afterwards and removes the app's own desktop icon — the framework
misses it because the icon is named in Persian and the match is against the app's English title —
and the four roles, leaving any role something still refers to in place and reported.

A reinstall brings the DocTypes back empty; the export fills them back through the three restores
below, and a second import creates nothing. See
[Exporting and importing this app's records](#exporting-and-importing-this-apps-records) for the
file, and [docs/recovery-test-plan.md](docs/recovery-test-plan.md) for the order of a rehearsal —
what to take first, which steps are a person's, and what each one has to prove.

Two things stand between a removal and a completed removal on a stack like this one, and both are
wider than the app:

- **The environment's own guard.** A protective hook in this workspace refuses a site-creation or
  an app-removal command to the agent. That is deliberate: those two commands are run by a person,
  and they are written out in `deployment/lab/README.md`. The rehearsal of the whole cycle is
  prepared there, in a stack of its own that shares nothing with this one.
- **`developer_mode` with a read-only mount.** The framework deletes the app's desktop icon and
  sidebar during removal, and in developer mode each of those tries to delete its JSON out of the
  app directory — which the read-only mount refuses with `Errno 30`. On such a site, either make
  the mount writable for the removal or turn `developer_mode` off for that site. The export and the
  import do not touch the filesystem at all: the browser writes the download and reads the file the
  operator picks, so nothing in this app needs a writable app directory.

### Deploying a change to a form script

A change to a `doctype_js` file is **not** served from `/assets/…`. Frappe inlines that file into
the DocType's form meta as `__js` (`frappe/desk/form/meta.py:111`), and that meta is cached in
redis under `doctype_form_meta::<DocType>` unless the site runs with `developer_mode`
(`desk/form/meta.py:33-42`). So after editing one:

1. `bench --site <site> migrate` — its own flow calls `frappe.clear_cache()` (`frappe/migrate.py:88`),
   which is what normally clears the meta.
2. Reload the Desk page. The browser executes the meta it fetched, so an open form keeps the old
   script until the page is reloaded.

Editing the file and reloading the browser alone is not a deployment: the running backend keeps
the Python it loaded at start unless it is restarted (its serve command runs with `--noreload`),
and the cached meta keeps the JavaScript it was built from. Both were the cause of a TypeError a
real browser test hit, and this note is what that cost.

### Deployment notes

- **The app is mounted read-only, on purpose.** Nothing in the install writes into the app
  directory: `sync_for` reads the DocType JSONs and inserts them into the database, and the three
  export paths that would write back into an app folder are suppressed while
  `frappe.flags.in_import` is set. The consequence, measured on a site with `developer_mode` on:
  saving a **standard** document this app ships — a workspace, a workspace sidebar, a desktop icon
  — makes the framework export the JSON back into the app directory and fails with
  `Errno 30 Read-only file system`. A workspace or icon an administrator created themselves
  (`standard: 0`) saves normally, because nothing is exported. A writable mount belongs to a
  development setup; production keeps the mount read-only and treats the app's own navigation as
  shipped, not edited.
- **The session sweep needs the scheduler.** `hooks.py` registers one `daily` event, which removes
  expired import previews. If the `scheduler` service is not running, the rows accumulate and
  nothing else changes: an expired preview is refused by the claim, so correctness does not depend
  on the sweep, only the size of one table does.

### Verifying the install

Three checks say whether the app is installed and working, in the order a person would ask them:

```bash
bench --site <site> list-apps
```
shows `smartprocess_logistics`; then, on a Sales Invoice whose item is marked `sp_serial_required`,
the toolbar shows one button, «سریال‌ها», and a single left-click opens the scan window. Finally,
the report «سریال‌های فروش ناقص» (Missing Sales Serials) lists the invoices that are short.

A site whose window opens **unstyled** is a site where `bench build --app smartprocess_logistics`
has not been run; see [Installation](#installation).

## Known limitations

- **A row whose item is not marked `sp_serial_required` is not offered.** It has no column, no
  place in the row navigation, and a registration on it is refused, with a sentence naming the row
  and its product. Registrations it already carries are neither deleted nor rewritten — they stay
  in `Logistics Serial Assignment`, count in the payload's `ineligible_rows`, are listed by
  `get_serial_assignments` and in the Desk list, and can still be removed. What the window cannot do
  is show them; a site that has such rows reaches them through the Desk list.
- **Amending a submitted invoice leaves its registrations on the cancelled original.** ERPNext
  amends by cancelling the invoice and creating a new one with new child-row names, so the new
  invoice reads as a shortage until the serials are registered on it. Nothing is lost — the old
  assignments stay with the old invoice, which is history — and nothing is moved automatically.
- **Moving serials is offered, not automatic.** `«انتقال سریال»` moves what an operator confirms
  and nothing else, and an amendment that invoices less than its predecessor leaves the rest on the
  cancelled invoice with a reason. There is no background job and no automatic transfer on submit.
- **A caller restricted to one company learns whether a serial name exists on the site.** Export
  and restore read serial *identities* only through the caller's own companies, with one exception
  (I28): the names the uploaded file mentions, which cannot be scoped at all — a `Logistics Serial`
  name is unique over the whole site and the DocType has no company column — and which the planner
  needs so it does not insert a master whose name is taken. What is disclosed is one bit per name
  in a file the caller already holds, and nothing about the company, item or text behind it.
- **A record name held by another company rolls the whole restore back.** The pre-flight can only
  see the record names of the caller's own companies, because a record of another company is not a
  name this caller may be told about; a collision outside that set reaches the insert and is refused
  with the sentence that the name is taken. Nothing is overwritten, and nothing is placed — the
  file has to be reconciled by hand.
- **The record tables grow with the work, not with the site.** The missing-serial report's grouped
  subquery aggregates the assignment table, and on a site with a large one it is the query to
  watch. There is no index on `(sales_invoice, sales_invoice_item)` yet.
- **Restoring is Manager-only and additive.** It creates the rows this site can place and reports
  the rest; it never overwrites, never edits a document, and never repairs a conflict.
- **The window is one invoice.** A paste, a file or a scan session that covers several invoices
  in one go is not supported, and the counts it shows are that invoice's.
- **A serial that a spreadsheet displays in scientific notation is copied that way.** A numeric
  cell holding fourteen digits may reach the clipboard as `6.95543E+13`; the app stores the text it
  is given, faithfully. Store serial columns as **text** in the workbook.
- **The custom homepage app is no longer installed.** `smartprocee_erpnext_homepage` imported
  `get_sidebar_items` from `frappe.boot`, which Frappe v16 no longer exports, so `get_bootinfo`
  failed for non-Administrator users. It was uninstalled from `SmartProcessERP`; the standard
  v16 Dock/apps-screen entry does not depend on it, and the legacy Desktop Icon ships alongside
  it as a compatibility entry only.
- **Purchase serial tracking is not implemented.** Serial capture and movement remain
  Sales-Invoice-side only; Purchase Receipt, warehouse transfer, sales and warranty are not yet
  connected into one purchase-serial flow.
- **The cancel guard has three edges.** A cancel that sets `doc.flags.ignore_links` bypasses it,
  because that is the flag ERPNext's own back-link check honours and a documented data repair
  needs it; a cross-company `purchase_invoice_item` Data link is not a Link and no standard check
  sees it, so it is ignored by design; and the guard names up to ten blocking receipts to the
  user attempting the cancel, who can already read them. The `purchase_invoice` column of
  `Purchase Receipt Item` is not indexed, so the lookup costs a scan on a large receipt table.
- **The Purchase Receipt weight/volume panel is verified server-side, not visually.** Its
  endpoint and calculations are tested, but the Desk panel has not been accepted in a real
  browser.
- **`sp_virtual_stock_covers_procurement` is retained for compatibility.** The calculation no
  longer reads it and Virtual stock never covers operational shortage; the field remains until a
  separate removal is approved.
- **Purchase Invoice GL issues are outside this app.** Zero-value journal rows and unbalanced
  Journal Entry attempts belong to the Accounting project; this app does not change ledger
  behaviour.

## Troubleshooting

| What you see | What it is | What to do |
| ------------ | ---------- | ---------- |
| The window is unstyled; the log shows a 404 for `/assets/smartprocess_logistics/css/…` | `bench build` has not run for this app | `bench build --app smartprocess_logistics` |
| The toolbar has no button | The invoice is cancelled, the item is not marked, the settings switch is off, or the acting user is not an Editor for this company | Check `docstatus`, the Item's `sp_serial_required`, «فعال‌سازی ثبت سریال» on `Logistics Settings`, and the user's roles and company permissions |
| A command earns «ردیف N … برای ثبت سریال فعال نیست» and nothing is staged | The row exists but its product is not marked for serials | Mark the Item, or aim the serials at a row that is |
| «ذخیره انجام نشد: تعداد سریال‌های اسکن‌شده … بیشتر است» | A column holds more labels than the invoice's quantity, so the whole save is refused | Remove the red labels, then save again |
| A serial is refused as a label | It is already on this invoice — as a saved registration or as an unsaved scan — or it is empty, too long, or carries an invisible character | The refusal names the serial and why |
| «این فاکتور بیش از 5000 سریال ثبت‌شده دارد …» | The status read is bounded on purpose: every count it shows is a count of what it read | Use the Desk list or `get_serial_assignments` for an invoice that large |
| A removal refuses «این قید ساخته نشد» | Another schema on the same MariaDB server holds a unique constraint with the same name | See the release notes: the guard refuses rather than claim a rule it does not enforce |

## Testing

Tests must never run against a production site. `IntegrationTestCase.setUpClass` calls
`make_test_records` and then commits, and the framework's test cleanup issues a final commit, so a
test run on a live site persists data. This app ships no `IntegrationTestCase`, so nothing here
commits: the whole suite runs without a site. A test that ever does need a bench belongs on a site
explicitly authorized for it, and on no other.

Under Frappe:

```bash
bench --site <site> run-tests --app smartprocess_logistics
```

The `core` tests do not need Frappe, because `core` does not import it. That is what makes
them runnable on a machine with no bench, and it is how they are checked before the app is
installed anywhere:

```bash
python -m unittest discover -s smartprocess_logistics/tests -t .
```

That command skips **twelve** tests: eleven because the reader needs `openpyxl` and the system
interpreter does not have it, and one because the development commitment composition is not part
of this build. Run the same command with an interpreter that has `openpyxl` — the repository's
isolated environment, or any environment where the app's own dependency is installed — to
execute them:

```bash
.venv/Scripts/python.exe -m unittest discover -s smartprocess_logistics/tests -t .
```

The current suite result is **1508 tests, OK**: 12 skipped with the system interpreter, and 2
skipped with the isolated environment (the commitment composition, and the missing-library path
that is unreachable when `openpyxl` is installed).

Between the two runs every test runs, with one exception: `TestTheOperatorsWorkbook` reads the
operator's own `~/Desktop/01.xlsx` and skips when it is not there. It is an **optional** check on
that machine's file — the values it asserts are that operator's serials, so they are not in this
repository — and it asserts the shape and the reading order rather than the serials. What every
checkout has, and what the exact values are pinned by, is the synthetic workbook in
`smartprocess_logistics/tests/fixtures/synthetic-workbook.xlsx`, whose column-by-column reading is
asserted by `TestTheSyntheticWorkbook`, by the node checks below, and by the browser harness.

The window's own rules are tested in JavaScript, and neither test needs a site:

```bash
node deployment/lab/verify_scan_logic.js
```

It loads the real `public/js/sales_invoice_serial_button.js` with a stand-in for `frappe` and runs
its pure functions — the command grammar, the splitter, the decoder that reads a table column by
column, the four states a row can be in, the refusal of one serial and the identity two serials are
compared by. The synthetic workbook is in it column for column, together with the clipboard copy of
the same range, and the decoded clipboard is asserted to be exactly the sequence the workbook
reads.

The window as a whole — the DOM, the staging, the save — is driven in a browser by
`deployment/lab/preview_scan_header.html`. It loads the real `public/js` and `public/css` and runs
against an in-memory server of its own, so it needs no site either: serve the repository root with
any static server and open the page with `?base=./../../smartprocess_logistics/public`. Left alone
it reads the station's two files from the lab (`http://localhost:8095`), which mounts that same
directory; `?lab=` points it at another site serving the same mount instead.

## License

MIT. See [LICENSE](LICENSE).
