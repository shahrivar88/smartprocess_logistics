# Data integrity — the lifecycle of a document

## docstatus is a meaning, not a flag

```
0  draft        editable, posts nothing
1  submitted    immutable in business terms, has posted its effect
2  cancelled    the effect was reversed; the document is history
```

In 16.35.0 the framework offers predicate methods:
`doc.docstatus.is_draft()`, `.is_submitted()`, `.is_cancelled()`. Use those in new code.

## The rules that keep history honest

- **A submitted document is not edited.** Changing it means cancelling and amending, which
  leaves the original visible and the amendment traceable. `on_update_after_submit` exists in
  v16 and is real, but reaching for it to rewrite a posted figure is how a ledger stops
  agreeing with itself.
- **Cancel is not delete.** A cancelled document keeps its name, its rows and its identity.
  Its effect is reversed, not erased. Never reuse a cancelled document's name or row.
- **Never rewrite historical stored data silently.** If the meaning of a stored value has to
  change, that is a migration with an explicit decision behind it, not an `UPDATE`.
- **Never fabricate a ledger, inventory or serial value.** Read
  `references/erpnext-domain.md` for which controller owns each one.
- **Amend produces a new document.** The link back to the original is part of the record.
  Write logic that copes with both halves being present.

## Idempotency and concurrency

- An operation that can run twice must produce one effect. Ask what happens if the job retries,
  if the operator double-clicks, if the scheduler fires while the previous run is still going.
- Check-then-act is a race. Two requests that both read "not yet registered" and both then
  write have produced two records. Where the invariant must hold, take the lock — `SELECT ...
  FOR UPDATE` inside the transaction — or make the write itself the arbiter with a unique
  constraint.
- Frappe commits at the end of a web request. Inside a long job, commit deliberately at a
  boundary you can explain, or not at all. A partial commit that leaves half a business event
  applied is worse than a failure that rolled back.
- A failure must fail loudly. Returning an empty result from a broken query is worse than
  raising, because a reader takes the empty result to mean "nothing to do".

This project already makes that choice in the missing-serials report: with no installation
timestamp it refuses with a code rather than returning an empty table. Follow that precedent.

## Writing to business data

- Use the document API: `frappe.get_doc`, the document's own methods, `insert`, `submit`,
  `cancel`. Not `frappe.db.set_value` on a ledger table, not `db_set`, not raw SQL.
- `frappe.db.set_value` and `db_set` bypass validation, hooks and the ledger. They are the
  right tool for a field that genuinely has no behaviour attached; they are the wrong tool for
  anything a controller is supposed to react to.
- Where the framework offers a bulk API, prefer it to a loop of single writes, and say which
  hook still runs and which does not.
