---
name: frappe-v16-project
description: >-
  Project knowledge for smartprocess_logistics on Frappe/ERPNext v16. Covers ERPNext document
  semantics for Sales, Stock, Accounting and CRM; server-side permission and company isolation;
  the submit/cancel/amend data-integrity rules; versioned patches and migration mechanics;
  Docker bind-mount persistence for this stack; Script Reports and Desk UI; and Persian RTL.
  Load it together with frappe-app-dev whenever a change in this project touches an ERPNext
  document, a permission check, a migration, a report, or the deployment wiring.
---

# smartprocess_logistics — Frappe / ERPNext v16 project knowledge

## What this skill is, and what it is not

This file holds only what the generic skills cannot carry: the v16 facts verified against the
installed source, and the mechanics specific to this project and this stack.

| Need | Load instead |
| ---- | ------------ |
| DocType authoring, controllers, lifecycle hooks, ORM, background jobs, bench commands | `frappe-app-dev` |
| Diff review for correctness, security, concurrency, performance | `quality-code-review` |
| The reuse ladder, risk classes, production-safety limits, stop conditions | `smartprocess-erpnext` |
| Vue 3 SPA with the frappe-ui component library | `frappe-ui` |
| Prose for README, CHANGELOG, commits and PRs | `technical-writing` |

Do not restate those skills here, and do not copy this file into them.

## The authority, and the order to read it in

1. **The installed source** is the primary authority for version-specific behaviour:
   `frappe` and `erpnext` 16.35.0 inside the `erpnext-backend-1` container, under
   `/home/frappe/frappe-bench/apps/`. Read it before trusting any recollection.
2. **Official documentation** is secondary.
3. **Never invent** a v16 API, hook name, DocType or field. If it is not in the source, it does
   not exist. `references/v16-framework.md` lists what was verified for 16.35.0.

## Reference files — load only the one you need

| File | Load it when |
| ---- | ------------ |
| `references/v16-framework.md` | You need a hook name, a lifecycle call, or a v16 API detail |
| `references/erpnext-domain.md` | The change touches Sales, Stock, Accounting or CRM documents |
| `references/permissions-and-company.md` | The change reads, writes, filters or reports on data across companies |
| `references/data-integrity.md` | The change creates, submits, cancels, amends or back-fills documents |
| `references/migrations-and-deployment.md` | You add a patch, a Custom Field, a fixture, or deployment wiring |
| `references/ui-reports-and-rtl.md` | You write a report, Desk script, print format, or any Persian-facing UI |

## Rules that apply to every file in this project

- No edit to `frappe`, `erpnext`, or any other app's source. Extension points only.
- No monkey patch, no whole-class DocType override, no `ignore_permissions`.
- Every value a caller supplies becomes a named parameter. No SQL fragment is ever built from
  input.
- Permission and company isolation are enforced server-side. Hiding a button is not a control.
- Historical documents are never silently rewritten. A cancelled or amended document keeps its
  own meaning.
- A change is finished only when its tests pass and its behaviour is explained, not when the
  symptom disappears.
