# smartprocess_logistics — project instructions

Standalone Frappe v16 / ERPNext v16 app: optional warranty serial numbers for `Sales Invoice`
Item rows. See [README.md](README.md) for what it does and
[docs/integrity-invariants.md](docs/integrity-invariants.md) for the rules it keeps true.

These instructions are the project-level layer. The machine-wide rules in
`~/.claude/CLAUDE.md` still apply, and the `smartprocess-erpnext` skill holds the reuse order,
production-safety limits and container command routing. Do not restate either one here.

## Always

- **Communicate in Persian.** Keep code, identifiers, file paths, DocType and field names,
  commands and API names in their original language.
- **Open every coding turn with:** project, phase, step X of Y, estimated duration. A turn
  that does not say which step it is has no way to be reviewed.
- **Fix root causes.** A change that makes a symptom disappear without explaining why is not
  finished.

## Never

- **Never modify `frappe`, `erpnext` or any other app's source.** Extension points only.
- **Never monkey-patch, override a whole DocType class, or bypass permissions.** No
  `ignore_permissions`, anywhere, ever. A missing permission is a design problem.
- **Never write business data with raw SQL.** The app writes through the ORM and its own
  whitelisted endpoints; see `core/orchestration.py`.
- **Never hardcode an environment path, site name, port or container name in app code.**
  `deployment/` is environment wiring and is not imported by the app.
- **Never add a site, bench, server, port or container.** The stack that exists is the stack.
- **Never run an unapproved write.** No install, migrate, restart, container recreate, database
  write, infrastructure change or `git push` without explicit authorization for that specific
  step. Prepare the command and ask.

## Environment

`SmartProcessERP` on port 8080 is a **development** environment. It is not production, and it
still is not written to without approval. Port 8081 and `ptm_clean_test2` belong to a different
project and are out of scope.

Persistent configuration must survive a restart and a `docker compose` recreate. Verify that
rather than assuming it.

## Shape of the app

- `smartprocess_logistics/core/` — every rule, with **no Frappe import**. Pure, deterministic,
  testable without a bench.
- `smartprocess_logistics/api/` — the six whitelisted endpoints and the one gateway that
  touches the ORM. An endpoint parses arguments and wires; it never decides.
- `smartprocess_logistics/setup/`, `install.py` — idempotent install, migrate and uninstall.
  Not registered in `hooks.py` yet.
- `smartprocess_logistics/tests/` — database-free tests, including a static validator for the
  DocType JSON.
- `docs/` — the invariants and the test manifest.

Keep the split. A decision that lands in `api/` is a decision no test can reach.

## Tests

```bash
python -m unittest discover -s smartprocess_logistics/tests -t .
```

An isolated `.venv` exists for the XLSX tests, which need `openpyxl`. Run the same command with
`.venv/Scripts/python.exe`. Nothing is written to the ERPNext Python environment.

Tests never run against a live site: `IntegrationTestCase` commits. See the testing section of
the README.

## Rules for this app specifically

- Installable and uninstallable on its own, with a documented recovery path.
- Versioned, upgrade-safe migrations. Idempotent patches. No destructive migration without
  approval and a backup.
- Data integrity and historical document consistency come before convenience.
- Permissions and company isolation are enforced **server-side**, in `core/authz.py`, not by
  hiding things in the UI.
- Keep the architecture modular and testable. Small modules, one responsibility each.
- `before_uninstall` must not destroy records silently.

## Review roles

Six reviewer agents live in `.claude/agents/`. They cost tokens, so match them to the risk:

| Risk | What to run |
| ---- | ----------- |
| An edit still in progress | Nothing — the main agent and the tests |
| MEDIUM | One reviewer whose subject matches the change |
| HIGH | `security-permissions-reviewer` and `db-integrity-reviewer`, plus `erpnext-domain-reviewer` when a ledger, a stock figure or a serial is involved |
| Before a release | `qa-regression-engineer` and `release-deployment-reviewer` |

Each one returns verified facts, potential defects with a severity, evidence with `file:line`,
required fixes, and unverified assumptions. A vague approval is not a result.

## Known gaps — read the source, not a recollection

The generic skills this machine installs carry no version-specific guidance:

- `frappe-app-dev` and `quality-code-review` do not mention v16 at all, and say nothing about
  ERPNext domain semantics, Docker persistence, or Persian RTL. They are also thin on
  `patches.txt` mechanics, company isolation, and Script Report or Workspace authoring.
- Use `.claude/skills/frappe-v16-project/` for those, and read the source itself — `frappe` and
  `erpnext` 16.35.0 inside `erpnext-backend-1` under `/home/frappe/frappe-bench/apps/` — before
  trusting any recollection.
- Never invent a v16 API, hook name, DocType or field. If it is not in the source, it does not
  exist.

## This repository

Local only. There is no remote and no push. A commit is a rollback point, so keep each one
focused and honest about what it changes.
