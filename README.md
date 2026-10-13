# smartprocess_logistics

Optional **warranty serial numbers** for individual `Sales Invoice Item` rows.

The app records which serial numbers were sold on a Sales Invoice item row, shows how many
units of that row still have no serial number, and reports on both. Registration is optional
per Item and is **independent of ERPNext's own stock serial numbers**: the app does not turn
on `Item.has_serial_no`, does not create or edit `Serial No` or `Serial and Batch Bundle`
records, and never posts stock or accounting entries.

## Reports

One report ships, and one query is written and not yet wired to a Report document. Each is
**one** statement: the registered count comes from a grouped subquery joined in, not from a
query per row.

| Report | Grain | Answers | State |
| ------ | ----- | ------- | ----- |
| Sales Serial Register | one row per serial assignment | which serial was sold on which invoice, and when it was registered | `core.reports.build_register_query` only; no Report document ships, so it is reachable from a future report or a console and from nowhere else |
| Missing Sales Serials | one row per serial-required invoice item row | which rows still need serials, and how many | ships, as a Script Report with Persian columns and Persian filters |

Every value a caller supplies becomes a named parameter. An `IN` list is expanded into one
placeholder per value rather than relying on a driver to expand a tuple, so nothing depends on
the driver's own formatting rules.

Company isolation is applied in the query itself, from the principal's own company list, and
the framework's invoice read conditions are **required** rather than optional: an argument that
defaults to no conditions is one somebody forgets, and forgetting it means a report that ignores
invoice permissions.

The missing report has no default date window. A report that silently covers ninety days answers
a question nobody asked and hides the rows outside it. When there is no installation timestamp
it refuses with a code rather than returning an empty table, which a reader would take to mean
every invoice is complete.

What the missing report can and cannot know is set out in
[docs/integrity-invariants.md](docs/integrity-invariants.md): it lists rows whose item requires
a serial **today**, on invoices created since the app was installed. Nothing records what the
flag was when an invoice was written, and no historical snapshot has been approved.

## Status

0.3.0 — installed and running on a test site (SmartProcessERP, ERPNext v16), verified there by
hand as well as by the suite: the Desk navigation, the serial dialog, the report, the endpoints,
company isolation against two companies, and an upgrade run twice.

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

The six DocType definitions, the three roles, the install-time helpers, the pure business logic,
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
while the framework matches the app's English *title*, and the three roles, which have no link to a
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
preflight that reads only the ZIP central directory, a bounded cell policy, marker grouping and
the registration plan, and comes back as a preview an operator confirms. Confirmation is a
later phase, and it re-validates against the database before writing anything.

Control and invisible characters are refused rather than deleted. Deleting them made a
separator that was never read as a separator glue two serial fragments into one serial that
looked exactly like a serial somebody had typed.

Sequential imports need the row mapping confirmed. That layout decides which invoice row a
serial belongs to by counting, so a file whose order does not match the invoice would assign
every serial to the wrong row with nothing in the file to say so. The preview shows the mapping
and the engine refuses until it is confirmed.

## Secure API layer

`smartprocess_logistics/api/` holds nine whitelisted endpoints and the gateway they drive. They
are reachable as soon as the app is installed, and reachable only through that gateway.

| Endpoint | Needs | Does |
| -------- | ----- | ---- |
| `get_invoice_serial_status` | Viewer | The rows of one invoice and how many serials each has |
| `get_serial_assignments` | Viewer | One page of the invoice's assignments |
| `set_row_serials` | Editor | Set one row's serials: add, replace and remove |
| `delete_assignments` | Manager | Remove assignments by name |
| `prepare_import_preview` | Editor | Parse a TXT or XLSX payload and return a preview |
| `confirm_import` | Editor | Apply a prepared preview, once |
| `export_serials` | Manager | Every serial and assignment in the site, as one downloadable document |
| `preview_serial_restore` | Manager | Validate an exported document and report what importing it would do |
| `apply_serial_restore` | Manager | Create the rows this site can place, in one transaction, and report the rest |

Every decision lives in `core`, which is why the authorization, the reconciliation, the replay
rules and the failure behaviour are tested without a site. An endpoint only parses arguments,
builds a principal, and wires the two together. No endpoint uses `ignore_permissions`, and none
of them commits.

`set_row_serials` and `confirm_import` differ in what they do to a row that already holds
serials, and the difference is deliberate. `set_row_serials` names one row and **replaces** it:
the set it sends is the set the row ends with. `confirm_import` names no row and **adds**: a
file that completes a partly registered row keeps what the row already had. An operator who
wants a serial gone asks for it, with `delete_assignments` or with a shorter list.

A write that loses a race is refused with a sentence rather than a MariaDB error. Two requests
touching one invoice, session or serial at the same instant leave MariaDB to abort one of them
with `QueryDeadlockError` or `QueryTimeoutError`; nothing of the aborted request is written, and
`core.orchestration.is_retryable` answers that sending it again is the remedy, which is what the
three write endpoints tell the operator.

Serials typed into the dialog's box are split by `core.delimiters.split_typed_list`, which
accepts the configured delimiters **plus** a comma, a semicolon, an Arabic comma, a line break
and a tab. Serials read from a file are split by the configured delimiters alone, so a line
`A,B` in a file is one serial named `A,B` unless a comma is configured. The difference is
deliberate and stated once, in `core`, because a list typed by hand is separated with a comma by
habit whether or not the configuration says so.

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

## Approved scope

- Serial registration belongs to `Sales Invoice Item` rows.
- Registration is allowed on existing invoices, including older ones, subject to permissions.
- A serial number is unique per Item identity and may be reused on different invoices.
- The same Item and serial number may not appear twice within one invoice.
- The missing-serial report covers only invoices whose `Sales Invoice.creation` is on or
  after the recorded application installation timestamp. Invoices created before that time
  stay excluded, even when they are submitted later.
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

## Missing-serial report eligibility

Only invoices whose `Sales Invoice.creation` is on or after `Logistics Settings.installed_on`
are eligible. That timestamp is written once, by the install, and the app never rewrites it:
`seed_settings` returns early as soon as it is set, so neither a re-install nor a migrate can
move it. Among this app's own roles only `Logistics Serial Manager` can write the Single at all.
Older invoices stay excluded even if they are submitted later, while manual serial registration
on them remains available.

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
```

`deployment/compose.smartprocess-logistics.yaml` is optional site wiring for one Docker stack.
The application works without it.

### What the install does

Four idempotent steps, in `install._ensure_app_state`, and both `after_install` and `after_migrate`
run them: create the three roles, apply the two composite unique constraints, bring
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
the DocPerm rows, the three roles, the settings values and the record counts identical. A field
removed from a definition is dropped by the same sync; the single value it left behind in
`tabSingles` has to be removed by hand, which is what 0.1.0 did for
`require_preview_confirmation`.

A migrate creates nothing of its own beyond the DocTypes, the constraints, the three roles and the
settings single. An earlier version also carried a schema of its own for a permanent archive; that
is gone, and a site upgraded from it has nothing to clean up by hand — those tables belonged to the
app and went when the app's DocTypes were dropped. What still has to be removed by hand is the one
value 0.1.0 left behind, described above.
that raises `SCHEMA_VERSION` — is written out in `deployment/lab/README.md`.

### Uninstall

```bash
bench --site <site> uninstall-app smartprocess_logistics
```

This drops the six DocTypes and the records in them — serial numbers and the assignments that
link them to invoice rows, the settings single, and the two configuration child tables — and
removes the app's sidebar and desktop icon through
`frappe.utils.install.delete_desktop_icon_and_sidebar`.

There is no preservation hook, and that is deliberate: the records in those tables go with them.
What must be taken **before** the removal is an export and a backup — «خروجی کامل اطلاعات
سریال‌ها» on `Logistics Settings`, then `bench backup --with-files` — and the order matters,
because a dump taken afterwards has no serial table in it at all.

`install.after_uninstall` runs afterwards and removes the app's own desktop icon — the framework
misses it because the icon is named in Persian and the match is against the app's English title —
and the three roles, leaving any role something still refers to in place and reported.

A reinstall brings the DocTypes back empty; «بازیابی اطلاعات سریال‌ها» fills them from the export,
and a second import creates nothing. See
[Exporting and importing this app's records](#exporting-and-importing-this-apps-records).

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

That command skips the three XLSX-reader tests, because the reader needs `openpyxl` and the
system interpreter does not have it. Run the same command with the isolated environment's
interpreter to execute them; between the two runs every test runs:

```bash
.venv/Scripts/python.exe -m unittest discover -s smartprocess_logistics/tests -t .
```

## License

MIT. See [LICENSE](LICENSE).
