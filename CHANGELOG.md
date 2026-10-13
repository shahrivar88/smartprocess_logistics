# Changelog

All notable changes to `smartprocess_logistics`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.0 — 2026-10-14

The app's records can now leave the site whole and come back whole: two buttons on
`Logistics Settings` export every serial, every assignment and every relationship they hold into
one JSON file, and import it again after the app has been removed and installed anew. The
permanent in-database archive that 0.2.0 added is removed, and that is the point of the release.

### Release notes

- **Why the archive went.** 0.2.0 kept the records in tables of its own so that they would survive
  a removal, and it worked. It was also a second mechanism claiming to be the truth about the same
  history that a site's own backup already holds, and two such mechanisms eventually disagree —
  they did, on the question of which one is the primary storage. The approved approach is the one
  an ERPNext site already uses for everything else: a **full site backup** for disaster recovery,
  and an **export** of this app's records for the case where the app itself goes away. Removing
  the archive deleted `core/archive.py`, `setup/archive_store.py`, the whole of its test file,
  a hook's worth of preservation logic, and the app's only SQL.
- **What an operator gets.** «خروجی کامل اطلاعات سریالها» on `Logistics Settings` writes one
  self-describing, digest-sealed JSON document with every field of both DocTypes and every
  relationship — invoice, invoice row and index, company, item, serial as typed, timestamps,
  authorship, and the normalisation version that produced each identity.
  «بازیابی اطلاعات سریالها» reads one back: it validates the format, the version and the digest,
  **shows what it would do before writing anything**, then places what the site can hold and
  reports what it cannot, with a reason per row.
- **What the import will not do.** It never creates or edits an Item, a Company or a Sales
  Invoice; a row naming a missing one is a conflict, not a creation. It never overwrites a stored
  row: an identity that is already live is skipped, which is what makes a second import create
  nothing. It takes each assignment's company from the live invoice, never from the file.

### Added

- `core/export.py` — the format, its versioning, its digest, the row projection and the import
  planner. Pure: no Frappe import, so every rule is testable without a site, including the one
  that keeps the format lossless.
- Three whitelisted endpoints — `export_serials`, `preview_serial_restore`, `apply_serial_restore`
  — all at Manager level in `core.authz` like every other decision about who may do what.
- `public/js/logistics_settings_transfer.js`: the two buttons, the file download, the preview
  dialog and the report. Nothing is written to the server's disk on either path.
- `tests/test_export.py`, including a test that compares the format's field list against the
  DocType definitions: a field added to a DocType and not to the export now fails a test instead
  of quietly dropping out of every export a site takes.
- `tests/test_uninstall_cleanup.py`: the `after_uninstall` behaviour that had been tested beside
  the archive keeps its coverage.

### Changed

- `after_migrate` creates nothing beyond the DocTypes, the constraints, the roles and the settings
  single. A site upgraded from 0.2.0 has no leftover tables to clean by hand: the archive's tables
  belonged to the app and went when its DocTypes were dropped.
- The README states the order before a removal — export, then `bench backup --with-files`, then
  the removal — and the three limits of restoring a backup afterwards.

### Removed

- `before_uninstall`, and with it the whole preservation mechanism: `core/archive.py`,
  `setup/archive_store.py`, the three `sp_logistics_*` tables, `snapshot_serials_now`,
  `restore_archived_serials`, the two Manager actions in `core.authz`, `tests/test_archive.py`,
  and the fake runtime's SQL-recording surface that only those tests used.

### Notes

- All four provenance columns come back as they were: `creation`, `owner`, `modified` and
  `modified_by`. Frappe overwrites every one of them on an insert — `set_user_and_timestamp`
  rewrites `modified` and `modified_by` unconditionally, and `creation` and `owner` unless one of
  `in_install`, `in_patch` or `in_migrate` is set — so the import writes them back afterwards with
  `frappe.db.set_value(..., update_modified=False)`. That is a supported API, it needs no core
  change, and `update_modified=False` is what stops it rewriting the timestamp it is restoring.
  The three framework-wide flags were deliberately **not** used: each is read elsewhere in Frappe,
  and a narrower mechanism that does one thing is easier to reason about than a switch whose other
  readers are not in this file.
- The round trip needs two commands the workspace's own safety hook refuses to the agent: site
  creation and app removal. `deployment/lab/README.md` holds them, and
  `deployment/lab/verify_isolation_cycle.py` verifies each phase on a real site — including the
  four provenance columns, compared against the export.

## 0.2.0 — 2026-10-13

Serial history now survives the app's own removal. The archive is three plain MariaDB tables
inside the site's database, written and verified by `before_uninstall` and read back by an
operator-controlled restore.

### Release notes

- **What changed, and why it had to.** 0.1.0 preserved the records by writing a JSON copy into
  the site's `private/files`. That file was not in any database backup — `remove_app` takes its
  own with `ignore_files=True`, and it takes it *before* the hook runs — and it was a raw dump of
  business rows on a volume every service in the stack can read. It is gone. In its place are
  three tables that no DocType points at, which is what keeps them out of the uninstaller's reach:
  it removes the table behind every DocType of the app being removed, and these are not DocTypes.
- **What an operator gets.** A removal still works, and it now leaves the serials, the
  assignments, the invoice row references and the company in the site's own database, joinable to
  `tabSales Invoice` with SQL and nothing else. `snapshot_serials_now()` takes a snapshot without
  a removal; `restore_archived_serials()` puts one back and is idempotent — a second run creates
  nothing. Both need a Manager.
- **What it will not do.** It never writes to `Sales Invoice`, the ledger or stock. It never
  restores an assignment whose invoice no longer exists: the row is reported as `stale` and left
  in the archive. It never resurrects a serial that was released before the snapshot.

### Added

- `core/archive.py` — the archive's schema, row projection, content digest, verification rules
  and restore planning. Pure: no Frappe import, so every rule is testable without a site.
- `setup/archive_store.py` — the one module that uses SQL. Schema creation and versioning,
  parameterized upserts, read-back verification, and a transactional restore that re-inserts
  documents through the ORM.
- `install.after_uninstall` — removes this app's desktop icon, which the framework's own cleanup
  misses because the icon is named in Persian while the match is against the app's English title,
  and the three roles, which have no link to a module. A role anything still references is left
  alone and reported. The hook never raises: by then the app is already gone.
- `install.snapshot_serials_now()` and `install.restore_archived_serials()`, both gated in
  `core.authz` at Manager level, with the restore checking each assignment's company against the
  live invoice through the same rules the endpoints use.
- `deployment/lab/` — an isolated, disposable stack for rehearsing the cycle, plus
  `verify_isolation_cycle.py`, which proves each phase on a real site and refuses to run against
  any site but the lab's.
- 71 tests: 777 in total, all database-free.

### Changed

- `before_uninstall` archives instead of copying to a file, and stops the removal only when the
  archive cannot be written **or does not verify**. A refusal from the archive is now surfaced
  with its own sentence and code rather than a generic message.
- `after_migrate` creates the archive's schema if it is absent, and refuses to touch an archive
  whose `schema_version` is newer than the code's.
- Reading a live table pages until a short page rather than to a count taken beforehand, and
  refuses a page that repeats a row — so a snapshot cannot quietly hold most of the history.
- `restore` re-points an assignment at the serial row it actually became, and takes the
  assignment's company from the live invoice, refusing a row whose archived company disagrees.

### Removed

- The JSON preservation copy: `install._write_preservation_copy`, `_read_all_rows`,
  `_verify_written_copy`, `PreservationError`, `PROTECTED_DOCTYPES` and `COPIED_SINGLES`.

### Notes

- `Logistics Import Session` is not archived: its rows are transient and carry the token a
  confirmation is claimed with.
- The archive is not a per-generation history. It holds one row per identity, and `restore`
  refuses a snapshot that is not the current one rather than restoring a subset.
- The rehearsal of the removal-and-reinstall cycle requires two commands the workspace's own
  safety hook refuses to the agent — site creation and app removal. They are written out in
  `deployment/lab/README.md` for a person to run.

## 0.1.0 — 2026-10-13

Optional warranty serial numbers for `Sales Invoice` item rows: a Desk interface, a script
report of what is still missing, and a whitelisted API that registers serials one row at a time
or from a TXT/XLSX file.

### Release notes

- **What an operator gets.** A Persian sidebar («سریال لجستیک») with one workspace, a report of
  rows still short of serials, and a **سریال‌ها** button on every Sales Invoice that opens a
  dialog: per-row entry with the row's current serials already in the box, TXT/XLSX upload with a
  preview to confirm, and a details view with removal for a Manager.
- **What it will not do.** It never writes to `Sales Invoice`, the ledger or stock; it never
  bypasses permissions; it registers nothing without a preview that a person confirmed; and it
  has no scheduled job except the one that removes its own expired previews.
- **Three roles.** Viewer reads, Editor registers, Manager also removes. Company isolation is
  enforced on the stored invoice's company, server-side, and measured live against two companies.
- **Known limitation, by decision.** Preview confirmation is mandatory and there is no setting to
  skip it; the field that pretended otherwise was removed in 0.1.0 rather than implemented.
- **Known limitation, by environment.** The app is mounted read-only. With `developer_mode` on, a
  Desk-side save of a standard app document fails with `Errno 30`; see the deployment notes in
  the README.

### Added

- Package layout for a standalone Frappe v16 application: `pyproject.toml`, `hooks.py`,
  `modules.txt`, `patches.txt`, the module package and the `tests` package.
- Repository and licensing files: `LICENSE`, `.editorconfig`, `.gitignore`,
  `.pre-commit-config.yaml`.
- `deployment/compose.smartprocess-logistics.yaml`, a site-independent Compose override that
  mounts the app into the existing stack.
- `docs/test-manifest.md`, the log of every record created while testing.

- Six DocType definitions: `Logistics Settings`, `Logistics Serial`,
  `Logistics Serial Assignment`, `Logistics Import Delimiter`, `Logistics Item Marker` and
  `Logistics Import Session`, with Persian labels and field descriptions.
- The three roles `Logistics Serial Viewer`, `Logistics Serial Editor` and
  `Logistics Serial Manager`, and the full DocPerm matrix for each DocType.
- `setup/roles.py`, `setup/constraints.py` and `setup/seeds.py`, plus `install.py` wiring
  `after_install`, `after_migrate` and `before_uninstall`. All four are idempotent; the first
  three are registered at the end of this release.
- Composite unique constraints on `(item_code, normalized_serial)` and
  `(sales_invoice, normalized_serial)`, applied through `frappe.db.add_unique`, and read back
  afterwards: `add_unique` looks its name up without a schema filter, so on a server hosting a
  second site that runs this app it can return without altering anything, and only a look-back
  can tell.
- `tests/schema_checks.py`, a pure validator for the DocType definitions that runs without a
  site, and `tests/test_doctype_schemas.py` to run it under Frappe.
- `core/`, the app's rules with no Frappe import, no database and no clock: versioned serial
  normalization, delimiter interpretation and splitting, marker validation and matching,
  requirement and shortage arithmetic, report eligibility, and the registration plan.
- `core/defaults.py`, the delimiters and markers a fresh install starts with, kept where the
  core tests can check them.
- `tests/base.py`, the one place that falls back to `unittest.TestCase` so the database-free
  tests run on a machine with no bench.
- The first 340 database-free tests, across normalization, delimiters, markers, quantities,
  eligibility, the registration plan, the configuration checks, the DocType definitions, the
  derived identity, the archive preflight, text decoding, the worksheet policy and the import
  engine. The release's own total is 706, measured at the end of this section.
- `Logistics Serial Assignment.normalized_serial`, a server-derived copy of the referenced
  serial's identity, so that `UNIQUE(sales_invoice, normalized_serial)` can forbid the same
  serial twice on one invoice even across two rows naming different items. The previous
  constraint on `(sales_invoice, normalized_serial)` could not: a link to a serial record
  identifies the record, not the text.
- `core/assignment.py`: the derived values, the refusal of a client-supplied identity, and the
  recorded runtime contract the assignment controller has to satisfy.
- `docs/integrity-invariants.md`: every rule the app keeps true, where it is enforced, and the
  limitations that remain, including what direct SQL can still bypass.
- `core/archives.py`, `core/textfile.py`, `core/spreadsheet.py` and `core/importer.py`: a
  database-free import engine. Size limits, a ZIP preflight that reads only the central
  directory, refusals for macros, external links, embedded objects and unsafe paths, a bounded
  worksheet cell policy, three assignment layouts, marker grouping, and a structured preview.
- `openpyxl>=3.1,<4` declared, because the app now reads `.xlsx`. It is the only dependency
  outside the standard library.
- `core/authz.py`: the endpoint table, the three levels, and the company and invoice checks,
  with the company read off the stored invoice rather than from the request.
- `core/session.py`: the import session lifecycle, `Prepared → Claimed → Applied`, with
  `Failed` and `Expired`, and every reason a claim is refused.
- `core/orchestration.py`: the rewrite and confirmation plans, the one global lock order, and
  the retry rules. Runs against a gateway, so the whole plan is tested against a fake.
- `api/endpoints.py` and `api/gateway.py`: six whitelisted endpoints and the one class that
  touches the ORM. Every read and write an endpoint needs is a method on that class, so the one
  layer that could bypass a permission check is one file wide.
- `tests/test_authz.py`, `tests/test_session.py` and `tests/test_orchestration.py`: 455 tests at
  that point, including authorization, replay, rollback and adversarial-input cases against a
  fake gateway.
- Resource limits now also apply to the real read: `sharedString` pool size is measured and
  capped during the archive preflight, and the declared uncompressed size is checked inside
  `read_workbook` rather than only by a separate caller.
- A sequential import needs its row mapping confirmed. The layout decides the row by counting,
  so the engine now refuses until the operator confirms the mapping the preview shows.
- `docs/integrity-invariants.md` records the concurrency invariants and states plainly which
  equivalences are measured and which are only expected.
- `core/reports.py`: the two report statements, with named parameters, an explicitly expanded
  company list, a required invoice-permission condition, enforced paging, no default date
  window, and an explicit refusal when there is no installation timestamp.
- The Item requirement flag's effect on the missing report, and the historical semantics that
  remain unresolved, are written down in `docs/integrity-invariants.md` rather than left for
  somebody to rediscover. No snapshot was invented.
- `tests/test_reports.py`: 66 focused tests over the query construction, including that no
  filter value reaches the statement, that a `LIKE` search escapes what the operator typed, and
  that both reports order by a total key so paging cannot repeat or skip a row.

### Added (Desk interface and operations)

- The Desk interface: a public `Workspace` and `Workspace Sidebar` named «سریال لجستیک», a
  `Desktop Icon` that opens the sidebar, and a `Logistics` card set linking the report, the
  three DocTypes and the settings single.
- `tests/test_desk_artifacts.py`: the shipped Desk files read as files, before anything is
  installed — that exactly one sidebar carries the links and that it is titled in Persian, that
  the module-named guard exists with no items, that the icon opens the sidebar that carries them,
  that a workspace's card breaks match the cards its content names, that the report's folder and
  files are named after the report, and that the two hook paths name files that exist.
- `core.delimiters.split_typed_list`: the one rule a typed list has that a file does not. A list
  typed into the dialog is separated with a comma or a semicolon by habit, whether or not either
  is a configured delimiter; the same text in a file is one serial. `set_row_serials` now takes
  the text as typed and splits it there, so the rule sits in `core`, where a test reaches it,
  instead of in the dialog's JavaScript, where it could not.
- `install.purge_stale_sessions`, run by the app's only scheduled job (`daily`): it expires the
  previews that expired on their own and removes the ones that finished more than
  `core.session.RETENTION_DAYS` ago, in batches bounded by `core.session.PURGE_BATCH` and oldest
  first. `Logistics Import Session` is transient by design, so without it the rows and the
  payloads they carry would accumulate for ever. The expiry step carries the two open states in
  its own `UPDATE`, so a session that was applied while the sweep was reading the table cannot be
  relabelled; the removal step is a permanent delete, because keeping a restorable copy of a
  payload in the Recycle Bin would preserve exactly what the sweep exists to remove.
- Each row of `get_invoice_serial_status` now carries the serials registered on it, as the text
  that was typed, and `get_serial_assignments` returns that text alongside the identity it is
  folded into. The dialog arrives with the row's serials already in its box, so correcting one of
  them is an edit rather than a retype of the row's whole set — and emptying a row that has
  serials asks first, because the endpoint replaces a row's whole set by design.
- `tests/test_session.py` and `tests/test_lifecycle.py`: the cleanup rules and the sweep, the
  latter against a gateway that can be told to lose a race.
- `public/js/sales_invoice_serial_button.js`, attached to `Sales Invoice` through the
  `doctype_js` hook, with a form button that opens the serial dialog: per-row registration,
  TXT and XLSX upload through `prepare_import_preview`, confirmation through `confirm_import`,
  and the invoice's registered serials with a Manager-only removal.
- `public/css/logistics.css`, loaded through `app_include_css`, styled from Desk's own custom
  properties so it follows the desk theme and reads correctly in both directions.
- `Missing Sales Serials` as a Script Report over `core.reports.build_missing_query`, with
  Persian labels and a `shortage` column from `core.quantities.row_coverage`.
- `Item.sp_serial_required` as a Custom Field, shipped as `custom/item.json` and synced by
  `sync_customizations` rather than by a core edit.
- `core.limits` and `core.units`: the four import limits in `Logistics Settings` are finally
  read, and byte sizes are described in Persian.

### Fixed

- **An Editor could not import from a file at all.** `Logistics Import Session` carried no
  DocPerm rows, so `document.insert` refused the preview with a bare `PermissionError` — an empty
  message in a dialog — for every user who was not an Administrator. The whole path had only ever
  been exercised as Administrator. The DocType now grants `create` — and only `create` — to the
  Editor and Manager roles: the app's rule is that a missing permission is a design problem, not
  something to work around with `ignore_permissions`, and a session row is a preview cache rather
  than an authorization. Nothing in the app may *read* the table as a user, because a row carries
  the token a confirmation is claimed with and the payload the preview accepted; a session
  belonging to somebody else is refused by `authz.require_session_owner`, and the registration it
  carries is re-validated against the database before anything is written. Viewers and
  administrators keep `read`/`delete` to System Manager only, and the sidebar link to the preview
  list is hidden from everyone else by the framework's own item filter.
- **A stored serial records the rule set that produced its identity.**
  `Logistics Serial.normalization_version` was never written, so every serial in the table read
  `0` — the field exists to say which rules built an identity, and it said nothing. The version
  is now carried on the normalized value itself (`NormalizedSerial.version`) through
  `find_or_create_serials` and stored with the serial, so a later rule change cannot relabel an
  older serial as new. Rows written before this change keep `0`, which is read as "not
  recorded"; no identity was rewritten to make them read otherwise.
- **`Logistics Settings.normalization_version` no longer contradicts the code.** The field had
  no reader and had kept the value `1` from the day the site was installed while
  `core.normalization.NORMALIZATION_VERSION` had moved to `2`, so the settings screen described
  rules the app no longer applied. `setup.seeds.sync_normalization_version` brings it up to date
  on every install and migrate, the shipped default is now the same number, and
  `tests/schema_checks.py` refuses a definition where the two disagree. It is a statement about
  the code, not about stored data, so it changes no identity.
- **A write that loses a race says so in words.** Two requests touching one invoice, session or
  serial at the same instant leave MariaDB to choose one to continue and abort the other, and
  the abort surfaced as `QueryDeadlockError (1020) Record has changed since last read in table
  ...` — a table name and an error number, in a dialog. `core.orchestration.is_retryable` had
  already answered "send it again" for this class and nothing carried that to the operator; the
  three write endpoints now answer with a sentence that says nothing was written and the request
  can be sent again. The abort rolls back whole — measured with two processes on one session and
  with two processes replacing one row — so retrying is the whole remedy.
- **Frappe no longer adds a second, English sidebar beside the Persian one.** A second
  `Workspace Sidebar` named exactly as the module is shipped with no items: the framework
  generates its own sidebar for any module that has none with that name, and the generated one
  lists the module's DocTypes in English. With no real item it is skipped again by
  `get_sidebar_items`, so it is a guard rather than an entry, and the panel shows one sidebar
  titled «سریال لجستیک».
- **An import no longer deletes the serials a row already holds.** `run_rewrite` removed every
  assignment of each row it filled, including rows an import had only added to, so uploading a
  second file to complete a partly registered row discarded the first file's registrations.
  Deletion now happens only for the rows a caller names as replaced, which is the one row
  `set_row_serials` names; `confirm_import` names none. Recorded as invariant I15.
- **A row's own serials no longer block its correction.** `core.validation` counted a replaced
  row's existing assignments as still present, so re-sending a serial the row already had was
  refused as "already registered on this invoice", and a shorter list was refused as an excess
  instead of the shortage it is. `plan_registration` now takes `replaced_rows` and counts those
  assignments out of the duplicate and quantity checks.
- **A permission refusal on an invoice is no longer an empty dialog.**
  `has_permission(..., throw=True)` raises a bare `PermissionError`, which reached the Desk as a
  blank message; the same check now raises through `frappe.throw` with a sentence.
- **The dialog's read calls use the verb the server declares.** The two read endpoints are
  whitelisted `methods=["GET"]` and the script posted to both, so every open of the dialog was
  refused with `403` before the endpoint ran — measured in a browser, not by calling the
  functions. The verbs are now declared once, in one map, instead of at each call site.
- **`reaches_company` no longer throws on a framework that answers synchronously.**
  `frappe.defaults.get_user_permissions()` returns the object itself on this version rather than
  a promise; calling `.then` on it threw inside the form's `refresh` handler, and the buttons
  were never added. `Promise.resolve` now takes either shape.
- **The file picker no longer offers `.csv`.** The shipped delimiters are the line breaks, tab
  and space, with no comma, so a comma-separated file would have been read as one serial per
  line with the commas inside it. The engine still reads any text file the operator chooses.

### Removed

- **`Logistics Settings.require_preview_confirmation`.** The field claimed to control whether an
  import needs a preview and a confirmation, and nothing read it: the flow always required both,
  and the value shipped as `1`. Implementing the other half would have meant a second write path
  that stores what nobody looked at, so the field is gone and the rule it described is now stated
  where an administrator reads about sessions — confirmation is mandatory, and there is no
  setting that turns it off. Sites upgrading from a build that had the field: the column is a
  Single, so the migration drops it and the orphan value in `tabSingles` is removed with it.

### Changed

- **Normalization rule set 2.** `NORMALIZATION_VERSION` is now 2, and two behaviours changed:
  - Control and invisible characters are **refused** instead of deleted. Deleting them let a
    separator glue two serial fragments into one serial that looked exactly like a serial
    somebody had typed. The refusal names the character.
  - Combining marks are dropped, after `NFKD` and before `NFC`. Rule set 1 kept them, so it
    called `é` different from `e` and `İ` different from `i` while the `utf8mb4_unicode_ci`
    collation called each pair equal. The app would have accepted two serials and the unique
    index would then have rejected the second with a duplicate-key error. The rule set is now
    proven to be at least as coarse as the collation, over a table of verdicts measured on the
    target MariaDB build.
- The `normalized_serial` field description on `Logistics Serial` now says case-folded rather
  than upper-cased, which is what the implementation has always done.
- `ensure_unique_constraints` verifies the columns an existing constraint covers, and stops the
  migration when a constraint name is taken by a different rule instead of silently accepting
  it.

### Notes

- `after_install` and `after_migrate` are registered and both call one idempotent
  `install.ensure_app_state`. `after_install` is what a fresh install runs and `after_migrate`
  is what a migrate runs; `installer.install_app` never calls the second, which is why both
  exist. No `doc_events` and no `scheduler_events`.
- The installation timestamp is written once, by the install, and never rewritten:
  `setup.seeds.seed_settings` returns early as soon as it is set.
- `before_uninstall` is **registered**, after the removal it guards was rehearsed on a disposable
  site. It writes a verified preservation copy of the app's records into `private/files` and
  blocks only when that copy cannot be written; it does not refuse a removal merely because
  records exist.
- The app declares one runtime dependency, `openpyxl`, for the XLSX reader. `core/` uses the
  standard library only, which is what keeps its tests runnable without a bench.
