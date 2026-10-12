# Changelog

All notable changes to `smartprocess_logistics`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 — unreleased

Application skeleton and data model. The app is not installable yet, and it registers no
hooks.

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
  `after_install`, `after_migrate` and `before_uninstall`. All four are idempotent and none
  of them is registered yet.
- Composite unique constraints on `(item_code, normalized_serial)` and
  `(sales_invoice, logistics_serial)`, applied through `frappe.db.add_unique`.
- `tests/schema_checks.py`, a pure validator for the DocType definitions that runs without a
  site, and `tests/test_doctype_schemas.py` to run it under Frappe.
- `core/`, the app's rules with no Frappe import, no database and no clock: versioned serial
  normalization, delimiter interpretation and splitting, marker validation and matching,
  requirement and shortage arithmetic, report eligibility, and the registration plan.
- `core/defaults.py`, the delimiters and markers a fresh install starts with, kept where the
  core tests can check them.
- `tests/base.py`, the one place that falls back to `unittest.TestCase` so the database-free
  tests run on a machine with no bench.
- 340 database-free tests across normalization, delimiters, markers, quantities, eligibility,
  the registration plan, the configuration checks, the DocType definitions, the derived
  identity, the archive preflight, text decoding, the worksheet policy and the import engine.
- `Logistics Serial Assignment.normalized_serial`, a server-derived copy of the referenced
  serial's identity, so that `UNIQUE(sales_invoice, normalized_serial)` can forbid the same
  serial twice on one invoice even across two rows naming different items. The previous
  constraint on `(sales_invoice, logistics_serial)` could not: a link to a serial record
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
  touches the ORM. **Inactive** — the app is not installed and nothing is reachable.
- `tests/test_authz.py`, `tests/test_session.py` and `tests/test_orchestration.py`: 455 tests
  in total, including authorization, replay, rollback and adversarial-input cases against a
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

- No `doc_events`, `scheduler_events`, `after_install`, `after_migrate` or `before_uninstall`
  hook is registered. Each one is enabled in the step that activates it.
- The installation timestamp is written once, and `set_only_once` makes the framework refuse
  any later change to it.
- `before_uninstall` refuses the uninstall while the app still owns records. A fresh export is
  not treated as authorization to destroy data.
- The app declares no runtime dependency. The XLSX reader is added in the phase that
  implements import, together with its justification.
