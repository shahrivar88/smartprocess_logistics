# Versioned migrations and Docker persistence

## patches.txt — the mechanism, read from erpnext 16.35.0

The file lives at `<app>/<app>/patches.txt`, next to `hooks.py`. Verified structure:

```
[pre_model_sync]
erpnext.patches.v16_0.set_secondary_item_valuation_type
erpnext.patches.v12_0.update_is_cancelled_field

[post_model_sync]
...
```

Facts, checked against the installed file:

- Two sections exist: `[pre_model_sync]` and `[post_model_sync]`.
- An entry is a **dotted module path**, not a path to a `.py` file.
- Patch modules live in a **version-named folder** — this project uses `patches/v16_0/`.
- Blank lines and `#` comments are allowed.
- A patch that has run is recorded in the site, so it does not run again. That is why the
  order in the file matters and why moving an entry changes behaviour.

`pre_model_sync` runs **before** the schema is synced, so it must not assume a new column
exists. `post_model_sync` runs after, and is the normal place for a data patch.

## Rules for a patch in this project

- **Idempotent.** Assume it may run against a site that already has the change, or that the
  previous attempt died halfway. Re-running must be safe.
- **Small and single-purpose.** One patch, one change. Do not combine a data cleanup with a
  feature migration.
- **Deterministic.** No dependence on today's date, on the current user, or on iteration order
  of an unordered dict.
- **Document-safe where business logic must run.** If the change has to trigger a controller,
  drive it through the document API — batched, and with the batch size stated. If it must not
  trigger behaviour, say so explicitly and say why.
- **Fail loudly.** A patch that swallows an exception and reports success leaves a site that is
  silently half-migrated.
- **No destructive operation without approval and a backup.** Dropping or renaming a populated
  column is a decision for the operator, not for a patch.
- Never edit `frappe` or `erpnext` core to make a patch unnecessary.

## Database-free tests first

This project's tests run without a bench:

```bash
python -m unittest discover -s smartprocess_logistics/tests -t .
```

Use `.venv/Scripts/python.exe` for the XLSX tests, which need `openpyxl`. Anything that needs
a live site belongs in a separate, explicitly approved step.

## Docker persistence on this stack

The app reaches the stack through `deployment/compose.smartprocess-logistics.yaml`, which is
not imported by the app. It works by three bind mounts:

1. The repository is mounted at the bench's `apps/` directory, so the code is present in every
   service.
2. `deployment/smartprocess_logistics.pth` is mounted into the environment's `site-packages`,
   which puts the app on `sys.path` for every Python service.
3. `smartprocess_logistics/public` is mounted at the bench's `assets/` path.

**Why a `.pth` file and not `PYTHONPATH`:** a Compose `environment:` entry replaces the
variable rather than appending to it, and this stack already sets `PYTHONPATH` to the path list
of five other apps. Setting it here would silently drop those imports. The `.pth` adds one path
without touching the variable.

Verify with `docker compose ... config` before `up`. A run without this file reports the app as
absent; it does not remove anything.

**The rule this implies:** persistent configuration must survive a container recreate.
`site-packages` lives in the image, so the `.pth` mount is re-created from the file on every
recreate — which is exactly why the file is in the repository and not typed into a container by
hand. Anything typed into a running container is gone at the next recreate. Verify persistence
by reasoning about the mount, not by assuming.
