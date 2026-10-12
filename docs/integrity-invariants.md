# Integrity invariants

Every rule this app keeps true, where it is enforced, and what is left unenforced. The last
column is the point of the document: an invariant enforced in only one place is a claim, not a
guarantee.

Nothing here runs yet. The app is not installed, `hooks.py` registers no hooks, and the
runtime APIs this document refers to do not exist. This is the contract they have to satisfy.

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
| I10 | The missing-serial report covers only invoices created at or after installation. | `Sales Invoice.creation` compared to `Logistics Settings.installed_on`, which `set_only_once` freezes | a manual `UPDATE` of the single, see below |
| I11 | A registration is removed and added, never edited in place. | The future replacement API; `Assignment` is not submittable and its controller refuses edits to the linking fields | direct SQL |
| I12 | A prepared preview is applied at most once. | The `Prepared → Applied` transition, checked while the session row is locked for update | direct SQL |
| I13 | Two confirmations cannot deadlock each other. | One global lock order, refused by `core.orchestration.check_lock_order` if a plan deviates | none |
| I14 | A write is all or nothing. | Everything rides in one transaction; nothing calls `commit` or `rollback`, so the request boundary decides | a caller that commits mid-operation, see below |

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
either. The same is true of `Logistics Settings.installed_on`, which `set_only_once` protects
against the framework but not against `UPDATE tabSingles`.

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

## Collation

The tables are `utf8mb4_unicode_ci`, so the database's own unique constraints compare without
regard to case **and without regard to accents**. Every verdict below was measured on this
MariaDB build, not assumed:

| Pair | Database | Rule set 2 |
| ---- | -------- | ---------- |
| `a` / `A` | same | same |
| `é` / `e` | same | same |
| `ǻ` / `a` | same | same |
| `é` / `è` | same | same |
| `İ` / `i` | same | same |
| `ß` / `ss`, `ß` / `SS` | same | same |
| `ς` / `σ` | same | same |
| `Ⅳ` / `IV`, `①` / `1`, `ﬁ` / `fi`, `０` / `0`, `½` / `1⁄2` | same | same |
| `ﻻ` / `لا` | same | same |
| `۱` / `1` | same | same |
| `AB` / `AB ` (trailing space) | same | same |
| `Æ` / `AE` | **different** | different |
| `क` / `कि` | **different** | different |
| `ي` / `ی` | **different** | **same** (deliberate, see below) |

The property that must hold is one-directional: **if the application calls two serials equal,
the database must call them equal too.** The opposite direction — the database calling two
serials equal while the app calls them different — is the dangerous one, because the app then
accepts both and the database rejects the second with a duplicate-key error the operator
cannot explain.

Rule set 1 (`NFKC` plus `casefold`) broke that property on four of the pairs above: it called
`é` and `e`, `ǻ` and `a`, `İ` and `i`, and `é` and `è` different while the database called each
pair the same. Rule set 2 drops combining marks, which fixes all four. The change is recorded
as `NORMALIZATION_VERSION = 2`.

`ي` and `ی` are the one place the app is deliberately **stricter** than the database. The two
letters are visually identical and come from different keyboard layouts, so treating them as
one serial is what an operator expects; being stricter can only refuse a serial, never accept
one the database would reject. On the other direction there is no case left.

Two things a future site could change:

- A site created with a different collation would move the boundary. The rule set is
  conservative enough to stay correct under a *more* coarse collation and could become unsafe
  under a *less* coarse one. Detecting it needs no new code: the comparison in
  `tests/test_collation_agreement.py` is a table, and re-measuring it on a new site is a
  one-query change.
- `set_only_once` protects `installed_on` against the framework, not against `UPDATE`.

## Concurrency, and what a mock can and cannot show

The transaction plan lives in `core.orchestration` and runs against a gateway, so its tests use
a fake and check the **sequence**: which calls are made, in which order, and which are skipped
when a check refuses.

A fake cannot show that a real `SELECT ... FOR UPDATE` blocks a second request, that MariaDB
rolls the whole transaction back, or that the composite index rejects a duplicate. Those are
database guarantees, they are listed in `tests/test_orchestration.py` as `NEEDS_A_DATABASE`, and
they stay unverified until the app runs on a site. The tests that exist are marked as what they
are: proof of the plan, not proof of the database.

Lock order, which is what stops two confirmations deadlocking:

	logistics_import_session -> sales_invoice -> logistics_serial -> logistics_serial_assignment

Retry rules: a validation failure is deterministic and not worth retrying; a lock wait timeout
or a connection loss rolled the transaction back whole and is worth retrying; an applied session
is finished and needs a new preview.

## Tested equivalence, and guaranteed equivalence

The collation table below was **measured**. No finite table can cover Unicode, so what is
claimed in general is narrower than what the table shows: the rule set is a *construction* —
`NFKD`, drop `Mn` and `Me`, `NFC`, fold digits, fold case — chosen to sit at least as coarse as
the collation's own construction, and the table is the evidence for that claim rather than a
proof of it.

For the pairs measured, the property holds. For a pair that was never measured, the property is
expected and **not established**. Re-measuring on another site means running the same query,
which is why the table is data and not an assertion hidden in a helper.

The collisions the rule set creates on purpose, each of which the collation creates too: width
(`ＡＢ１２` = `AB12`), case, digit script, accents (`é`, `è` = `e`), compatibility forms
(`①`, `Ⅳ`, `ﬁ` = `1`, `IV`, `fi`), and trailing space. One collision the app creates and the
database does not is the Arabic/Persian letter pairs `ي` = `ی` and `ك` = `ک`; there the app is
stricter, which can only refuse a serial, never accept one the database would reject.

## What the Item requirement flag does to the missing report

`Item.sp_serial_required` is the only thing that decides whether an invoice row belongs in the
Missing Sales Serials report, and it is read **as it is now**. That has consequences an operator
can see, and they are worth stating before somebody reports them as bugs:

- Turning the flag **off** on an item removes its invoices from the report, including invoices
  that were created while the flag was on. Work that was genuinely outstanding disappears.
- Turning the flag **on** adds invoices, including ones that were created before the item ever
  needed a serial. Work appears that nobody was expecting.
- The report is bounded by `Sales Invoice.creation >= Logistics Settings.installed_on`, so an
  invoice from before the install is never included whatever the flag says. An invoice created
  before the install and submitted afterwards stays out as well, because creation is the marker
  and the submission date is not stored.

### Unresolved historical semantics

**Nothing records what the flag was when a given invoice was written.** The app therefore
cannot answer "did this row require a serial *at the time*", and it does not pretend to. Two
approaches were considered and neither is approved:

1. A snapshot field on the Sales Invoice item row, written while the invoice is a draft. It
   would answer the question exactly for invoices written after the app was installed, and it
   would leave every invoice from before then unanswerable anyway. It also writes a field on a
   financial document, which this app has kept itself from doing.
2. An app-owned history of the flag's changes, resolved per row. It answers the question for
   every invoice, old ones included, without touching a financial document, at the cost of a
   table and a hook on `Item`.

Until one of them is approved, the honest description of the report is: **it lists rows whose
item requires a serial today, on invoices created since the app was installed.** The word
"today" is the limitation, and `ReportError(REPORT_NOT_INSTALLED)` is the honest answer when
even the boundary is unknown.

A third consequence, unrelated to the flag: an assignment whose invoice item row was deleted is
orphaned, matches no row, and counts towards nothing. The Sales Serial Register shows it with
`is_orphaned` set, so it is visible; the missing report simply does not see it.

## What is not enforced, on purpose

- **Orphaned assignments.** A Sales Invoice item row deleted while the invoice is a draft
  takes its row name with it, so the assignment that named it is orphaned. Orphaning is a
  consequence of the invoice being edited, which this app is not allowed to prevent. It is
  derived by a join at read time, never stored, so it cannot go stale.
- **Excess registrations.** Registering more labels than a row's quantity is a business
  decision the site makes through `allow_over_quantity`. The app reports the number and refuses
  the write only when the setting says to.
- **Serial reuse across invoices.** Permitted, and the point of separating serial identity from
  the assignments that reference it.
