# smartprocess_logistics

Optional **warranty serial numbers** for individual `Sales Invoice Item` rows.

The app records which serial numbers were sold on a Sales Invoice item row, shows how many
units of that row still have no serial number, and reports on both. Registration is optional
per Item and is **independent of ERPNext's own stock serial numbers**: the app does not turn
on `Item.has_serial_no`, does not create or edit `Serial No` or `Serial and Batch Bundle`
records, and never posts stock or accounting entries.

## Reports

Two reports, and only their query construction exists so far. Each is **one** statement: the
registered count comes from a grouped subquery joined in, not from a query per row.

| Report | Grain | Answers |
| ------ | ----- | ------- |
| Sales Serial Register | one row per serial assignment | which serial was sold on which invoice, and when it was registered |
| Missing Sales Serials | one row per serial-required invoice item row | which rows still need serials, and how many |

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

Phase 2 Step 1 — installation lifecycle prepared, app still not installed.

The six DocType definitions, the role structure, the install-time helpers, the pure business
logic, a database-independent import engine and the whitelisted endpoints all exist, and
everything that decides anything is covered by tests. The endpoints are **inactive**: the app is
not installed. `hooks.py` now registers `after_install` and `after_migrate`, both idempotent, but
until the app is mounted on a site they run for nothing. There are still no reports and no Desk
UI.

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

`before_uninstall` is implemented and **not registered**, so a `bench uninstall-app` today drops
the app's tables with no copy at all. What it will do once registered: write a JSON copy of
`Logistics Serial`, `Logistics Serial Assignment` and the `Logistics Settings` Single into the
site's `private/files`, owner-readable only, then stop the uninstall only if that copy cannot be
written or does not read back as what was prepared. It never refuses an uninstall merely because
records exist. `Logistics Import Session` is deliberately not copied: its rows are transient and
it holds the hidden `token` and `payload`. The copies are never deleted by the app — removing the
only artifact of records that no longer exist is the opposite of the point.

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
caseless-matching operation. The stored identity field is hidden, so its case never reaches a
screen; `serial_no` keeps the text exactly as typed.

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

`smartprocess_logistics/api/` holds six whitelisted endpoints and the gateway they drive. It is
**inactive**: nothing is reachable until the app is installed.

| Endpoint | Needs | Does |
| -------- | ----- | ---- |
| `get_invoice_serial_status` | Viewer | The rows of one invoice and how many serials each has |
| `get_serial_assignments` | Viewer | One page of the invoice's assignments |
| `set_row_serials` | Editor | Set one row's serials: add, replace and remove |
| `delete_assignments` | Manager | Remove assignments by name |
| `prepare_import_preview` | Editor | Parse a TXT or XLSX payload and return a preview |
| `confirm_import` | Editor | Apply a prepared preview, once |

Every decision lives in `core`, which is why the authorization, the reconciliation, the replay
rules and the failure behaviour are tested without a site. An endpoint only parses arguments,
builds a principal, and wires the two together. No endpoint uses `ignore_permissions`, and none
of them commits.

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
| `Logistics Settings` | Single | Feature switch, install timestamp, import limits, the active delimiters and item markers |
| `Logistics Serial` | Master | One row per serial number, unique per Item |
| `Logistics Serial Assignment` | Transaction | One row per serial registered on one Sales Invoice item row |
| `Logistics Import Delimiter` | Child | A delimiter that file import splits on |
| `Logistics Item Marker` | Child | An item marker pattern such as `{n}*` |
| `Logistics Import Session` | System | A one-time import preview awaiting confirmation |

Serial identity is the pair `(item_code, normalized_serial)`, enforced by a composite unique
constraint. `serial_no` keeps the text exactly as it was entered, including leading zeros,
for display; `normalized_serial` is the uppercase, digit-folded form used for identity. The
database collation is `utf8mb4_unicode_ci`, which compares without regard to case, so
uppercasing the stored form keeps the application rule and the database rule identical.

A serial may be registered on several invoices. A second constraint on
`(sales_invoice, logistics_serial)` forbids the same serial twice on one invoice, which
already covers twice on the same item row.

An assignment points at a Sales Invoice item row by that row's own name, not by its position.
If a draft row is deleted and replaced, the assignment becomes orphaned; orphaning is derived
by a join at read time and is never stored as a flag.

`Logistics Import Session` carries no DocPerm rows at all, so no Desk list or form exposes it
and its payload is reachable only by Administrator.

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
are eligible. That timestamp is written once at install and the `set_only_once` flag makes the
framework refuse to change it afterwards. Older invoices stay excluded even if they are
submitted later, while manual serial registration on them remains available.

## Requirements

- Frappe **16.x** and ERPNext **16.x**, declared as `>=16.0.0,<17.0.0`.
- Python 3.10 or newer.

The app declares one runtime dependency, `openpyxl`, because the import feature reads `.xlsx`.
Every other module uses the standard library only, and `core/` uses nothing else at all.

## Installation

Not installed anywhere yet. The app is complete enough to install and its two lifecycle hooks
are registered, but it is not mounted on any stack; that is a separate, explicitly approved
step. The standard flow applies and no site name, host, port or container path is compiled into
the application:

```bash
bench get-app <repository-url> smartprocess_logistics
bench --site <site> install-app smartprocess_logistics
```

`deployment/compose.smartprocess-logistics.yaml` is optional site wiring for one Docker
stack. The application works without it.

## Testing

Tests must never run against a production site. `IntegrationTestCase.setUpClass` calls
`make_test_records` and then commits, and the framework's test cleanup issues a final commit,
so a test run on a live site persists data. Test execution is therefore limited to a site
that has been explicitly authorized for it, and a fail-fast guard is added in Phase 2.

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

## License

MIT. See [LICENSE](LICENSE).
