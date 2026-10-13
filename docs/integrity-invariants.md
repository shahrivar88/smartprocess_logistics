# Integrity invariants

Every rule this app keeps true, where it is enforced, and what is left unenforced. The last
column is the point of the document: an invariant enforced in only one place is a claim, not a
guarantee.

All of it runs. The app is installed on a test site (SmartProcessERP, ERPNext v16), the hooks in
`hooks.py` are registered, the endpoints and the report are reachable, and every invariant below
has been exercised — most of them there as well as in the database-free suite. Where a row still
says "direct SQL" or names a hole, that hole is the one thing the invariant does not cover, and
it is written down rather than papered over.

## The invariants

| # | Invariant | Enforced by | Remaining hole |
| - | --------- | ----------- | -------------- |
| I1 | Serial identity is unique per Item: one `(item_code, normalized_serial)` pair names one serial. | `UNIQUE(item_code, normalized_serial)` on `Logistics Serial`, created by `setup.constraints` on every migrate | none |
| I2 | A serial text appears at most once on one invoice, even across two item rows. | `UNIQUE(sales_invoice, normalized_serial)` on `Logistics Serial Assignment` | none |
| I3 | `Assignment.normalized_serial` equals the identity of the serial it links to. | Derived on the server by `core.assignment.derive_assignment_values`; refused if supplied; re-derived and compared by the future controller | **direct SQL**, see below |
| I4 | A serial identity is immutable after registration. | The future `Logistics Serial` controller refuses a change to `normalized_serial`; a correction is an explicit replacement | direct SQL |
| I5 | `Assignment.item_code` equals the item of the serial it links to. | `fetch_from: logistics_serial.item_code` on the field, applied by the framework on every save | direct SQL |
| I6 | A serial number is never merged with another by a transformation nobody can see. | `core.normalization` refuses any control or invisible character instead of deleting it | none |
| I7 | The app never accepts two serials that the database would then call one. | The normalization rule set is proven to be at least as coarse as `utf8mb4_unicode_ci`; pinned by `tests/test_collation_agreement.py` | a collation change on a future site, see below |
| I8 | No accounting or stock document is created, changed or posted. | The app has no `doc_events` on any financial document and calls no posting API | none |
| I9 | An invoice is never modified by this app. | The app writes only its own DocTypes; `hooks.py` registers no hook on `Sales Invoice` | none |
| I10 | The missing-serial report covers only invoices created at or after installation. | `Sales Invoice.creation` compared to `Logistics Settings.installed_on`, which the install writes once and `setup.seeds` never rewrites | a manual `UPDATE` of the single, see below |
| I11 | A registration is removed and added, never edited in place. | The future replacement API; `Assignment` is not submittable and its controller refuses edits to the linking fields | direct SQL |
| I12 | A prepared preview is applied at most once. | The `Prepared → Applied` transition, checked while the session row is locked for update | direct SQL |
| I13 | Two confirmations cannot deadlock each other. | One global lock order, refused by `core.orchestration.check_lock_order` if a plan deviates | none |
| I14 | A write is all or nothing. | Everything rides in one transaction; nothing calls `commit` or `rollback`, so the request boundary decides | a caller that commits mid-operation, see below |
| I15 | An import adds serials; only `set_row_serials` replaces a row's whole set. | `core.orchestration` removes assignments only for the rows a caller named as replaced (`plan.only_rows`), and `core.validation` counts a replaced row's own assignments out of the duplicate and quantity checks | direct SQL, or a later endpoint that names a row it does not mean to replace |
| I16 | A stored serial records the rule set that produced its identity. | The version travels on `NormalizedSerial.version` from the normalization that ran, through `orchestration.run_rewrite`, into `Logistics Serial.normalization_version` at creation; a serial that already exists keeps the version it was created with | rows written before the field was populated carry `0`, which reads as "not recorded"; nothing rewrites them |
| I17 | A write that loses a race writes nothing and may be sent again. | One transaction per request, so the aborted side rolls back whole; the three write endpoints answer a deadlock or a lock-wait timeout with a sentence instead of the MariaDB error | a caller that retries a request whose first attempt actually committed — the session claim is what refuses that one |
| I18 | The records can leave the site whole, and come back whole. | `Logistics Settings` exports every field of both DocTypes into one versioned, digest-sealed document, and the format's field list is compared against the DocType definitions by a test | a field added to a DocType and not to the export format fails that test, and a hand-edited export is refused by its own digest |
| I19 | An import cannot duplicate a serial or an assignment, and cannot link one wrongly. | `core.export.plan` skips a row whose identity is already live, reuses a serial the site already has under another name, takes each assignment's company from the **live invoice**, and reports a missing Item, Company, invoice or invoice row as a conflict instead of creating one | direct SQL, or importing into a site while it is being changed underneath (the insert is one transaction, but the plan is computed before it) |

## Why `Assignment.normalized_serial` exists at all

A unique constraint cannot reach through a link field. `logistics_serial` points at a
`Logistics Serial` row whose identity is `(item_code, normalized_serial)`, so a constraint on
the link forbids the same **record** twice on an invoice — but not the same **text** twice when
two rows name different items, because those are two records. I2 is about the text, so the
text has to be stored on the row the constraint covers.

The copy is safe only while I3 holds, and I3 is not a constraint. It is an application
invariant with three layers, listed in `core.assignment.RUNTIME_ENFORCEMENT`:

1. The value is built by one function from the serial that was read back from the database.
2. A request that carries `normalized_serial` is refused before a document is built, so a
   caller cannot propose an identity.
3. The controller re-derives and compares on every write it allows, so a document edited
   through any framework path is caught.

Layer 3 matters because a value can be written by a path that never called layer 1. It cannot
detect a mismatch that was written by raw SQL, which is the hole below.

## Direct SQL

An `INSERT` or `UPDATE` issued directly against `tabLogistics Serial Assignment` can write a
`normalized_serial` that disagrees with the linked serial, and can change a
`Logistics Serial.normalized_serial` after registrations exist. No application code sees
either. The same is true of `Logistics Settings.installed_on`: the app writes it once and never
rewrites it, but that is an application rule, not a database one, and an `UPDATE` against
`tabSingles` would change it with nothing to notice.

This is accepted, and the reasoning is worth recording:

- Closing it needs a database trigger or a check constraint. Frappe does not manage trigger
  schema, so a trigger would be invisible to `bench migrate`, would survive an uninstall that
  drops the table it references, and would have to be recreated by hand on every site. That is
  a worse risk than the hole it closes.
- The hole requires database credentials. Anyone holding them can write anything in the
  database, including the ledger, so the marginal exposure is small.
- Detecting the damage does not need the trigger: `_constraint_columns` already reads
  `information_schema`, and a future integrity report can compare every assignment's copy
  against its serial with one join, plus compare `installed_on` against the recorded install
  time. That report is the planned answer, and it belongs in Phase 6.

What the app will **not** do is claim the invariant is enforced when it is only derived. Hence
this document.
