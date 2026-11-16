# Installing smartprocess_logistics on another ERPNext system

This is the portable install path. It needs no particular operating system, no host path, no
custom Compose file, no core edit and no second app. Everything below was verified against the
installed source in this project, or measured on the test site this app was developed on.

## 1. Versions verified

| Component | Version | How it was verified |
| --------- | ------- | ------------------- |
| `frappe` | 16.55.2 | Read at runtime on the development site |
| `erpnext` | 16.54.0 | Read at runtime on the development site |
| Python | 3.10 or later | Declared in `pyproject.toml` as `requires-python = ">=3.10"` |

`pyproject.toml` declares the supported range as `frappe >=16.0.0,<17.0.0` and
`erpnext >=16.0.0,<17.0.0`. Only 16.55.2 / 16.54.0 have actually been run. A v15 site is not
supported. A v16 site on a different patch level is expected to work but has not been tested —
see section 11.

## 2. Dependencies

**Required.** `frappe` and `erpnext`, declared in `hooks.py` as
`required_apps = ["frappe", "erpnext"]`. `bench install-app` reads that list and installs a
missing required app first, so installing this app on a Frappe-only site pulls ERPNext in
rather than failing half way.

**Python packages.** One runtime dependency, `openpyxl >=3.1,<4`, used by the XLSX import and
export endpoints. It is already present in a standard ERPNext v16 environment. No other package
is imported at runtime.

**Optional.** `smartprocess_erpnext_accounting`. This app does not import it. When it is
installed, the «وابستگی‌ها و تغییرات بین‌ماژولی» page reports its version; when it is absent,
the same page reports it as not installed and every other feature keeps working. Nothing has to
be installed or configured for a site without it.

## 3. Get the app at a known release

The repository is <https://github.com/shahrivar88/smartprocess_logistics>.

```bash
bench get-app https://github.com/shahrivar88/smartprocess_logistics
```

That clones the default branch and installs the app into `apps/`. To pin the exact verified
release instead of tracking a branch:

```bash
git -C apps/smartprocess_logistics checkout v0.7.6
```

Pinning the tag is the reproducible choice: the default branch moves, a tag does not. Run the
checkout before `install-app`, because the checked-out code is what the install imports.

## 4. Install on the target site

Replace `<site>` with the site name on the target bench. Nothing here depends on the site name,
so there is no value to copy from another environment.

```bash
bench --site <site> install-app smartprocess_logistics
```

What this runs, and why it is enough:

- Frappe imports the DocTypes, the Reports and the app-rooted `Sidebar`
  (`installer.install_app` → `sync_for`), so the navigation exists on a fresh install and does
  not need a `migrate`.
- `after_install` (`smartprocess_logistics.install.after_install`) creates the four roles, the
  two composite unique constraints and the archive tables, and seeds `Logistics Settings`. It is
  idempotent and needs no permission bypass.
- `fixtures` are imported: the `Physical` Warehouse Type and the two Property Setters. See
  section 7.

`bench --site <site> migrate` is not needed after a fresh install. It is needed after every
upgrade — see section 9.

## 5. Build assets and compile the Persian label

One command does both of the steps this app needs:

```bash
bench build --app smartprocess_logistics
```

Verified from the installed source: `bench build` calls
`frappe.gettext.translate.compile_translations(app)`, which reads
`apps/smartprocess_logistics/smartprocess_logistics/locale/fa.po` and writes
`sites/assets/locale/fa/LC_MESSAGES/smartprocess_logistics.mo`. That is exactly the path Frappe
reads Persian translations from at runtime. No file has to be copied by hand.

The module label `لجستیک پردازش هوشمند` comes from that catalogue — the entry
`msgid "SmartProcess Logistics"` in `locale/fa.po`. It appears once the user's language is
Persian (see section 6).

The stylesheet needs no build: `app_include_css` points at
`/assets/smartprocess_logistics/css/logistics.css`, a plain file served from `public/`.

Verify the compile:

```bash
ls -l sites/assets/locale/fa/LC_MESSAGES/smartprocess_logistics.mo
```

If the file is missing, `bench build` did not run or did not pick the app up. Re-running with
`--force` regenerates it:

```bash
bench build --app smartprocess_logistics --force
```

Two cases where the `.mo` does not survive on its own, both specific to a Docker stack whose
`sites/assets` is a symlink into image content rather than a volume: the compiled file is
discarded by a container recreate, and `bench build` cannot write at all if the app directory is
mounted read-only. Neither applies to a normal bench. This deployment solves them with a
file-over-file bind mount in `deployment/` — a workaround for its own layout, not a requirement
of the app.

## 6. Initial configuration

Nothing below is required for the app to load. Each item changes behaviour.

1. **Roles.** `after_install` creates four:
   `Logistics Serial Viewer`, `Logistics Serial Editor`, `Logistics Serial Manager`,
   `Logistics Serial Archivist`. They grant nothing until assigned. Assign them to the users who
   work with serials. An Administrator does not need them.
2. **Language.** Set each Persian-speaking user's language to Persian
   (`User` → `Language` = `fa`, or «زبان» in the user form). A user left on English sees the
   English module label; the catalogue is not applied regardless of site default.
3. **Settings.** `Logistics Settings` is a Single and is seeded automatically. Review its
   inventory policy fields before the first purchasing run: allocation priority, whether virtual
   stock covers procurement, and whether the recommended purchase quantity is net of open
   purchase orders. The seeded values are the approved defaults, written once and marked with
   `sp_inventory_policy_version`, so a later change by an administrator is not overwritten by the
   next migrate.
4. **Cache.** If the sidebar or the module label does not appear after an upgrade rather than a
   fresh install, run `bench --site <site> clear-cache`.

## 7. Warehouse classification

The app classifies warehouses by `Warehouse Type`, and the classification is what several
reports and the virtual-warehouse policy read.

- The install creates one Warehouse Type named `Physical`, and a Property Setter that makes it
  the default for a **new** Warehouse. Opening the new-Warehouse form therefore preselects it.
- The install also sets `translated_doctype` on Warehouse Type, which is what lets its label
  render in Persian.
- **Existing warehouses are not touched.** No fixture rewrites a warehouse, and no default is
  applied retroactively. On a site that already has warehouses, set `warehouse_type` on each one
  deliberately. A warehouse left with no type is treated as unclassified by the reports, not
  guessed at.
- A second Property Setter makes the field default apply only to newly created documents, so
  nothing changes for a document already saved.

## 8. Company, account and currency assumptions

The app makes none, and stores none.

- **No hardcoded company, account, currency, cost center, warehouse or fiscal year** exists in
  the source. Every value is read from the document being worked on or from the site's own
  configuration.
- **Company isolation is derived, not configured.** A serial belongs to a company only through
  the assignments that name it, and the policy is applied server-side in
  `core/authz.py` plus `permission_query_conditions` in `hooks.py`. A user restricted to one
  company sees that company's serials and no other's. No per-site setup is needed to get this.
- **Currency.** The app reads amounts in the transaction currency of the document it is given
  and performs no conversion. A multi-currency site needs no extra configuration for that reason.
- **Accounting.** The app posts no journal entry, no GL Entry and no Payment Ledger Entry. It
  reads standard ERPNext purchasing and inventory documents. No account, no tax template and no
  posting date setup is required by this app.

## 9. Upgrade

Back up first — see section 10. Then, on the bench that holds the site:

```bash
cd apps/smartprocess_logistics
git fetch --tags
git checkout v0.7.6
cd ../..
bench --site <site> migrate
bench build --app smartprocess_logistics
bench --site <site> clear-cache
```

`migrate` runs `after_migrate`, which repeats the five idempotent install steps and repairs a
site where `after_install` was interrupted. Fixtures are re-imported, replacing each record by
its fixed name rather than adding a second one. No patch in this app is destructive; each is
idempotent.

## 10. Rollback and backup

**A backup is required before an upgrade and before an uninstall. Neither has an automatic
reversal.**

```bash
bench --site <site> backup --with-files
```

- **Rolling back the code** is `git checkout <previous-tag>` followed by
  `bench --site <site> migrate`. Frappe applies schema changes forward; it does not undo them.
  A release that only changed code or documentation rolls back this way cleanly. A release that
  added a column or a table does not — restoring the backup is the correct rollback, and a
  code-only checkout on top of a newer schema is not supported.
- **Uninstalling** is `bench uninstall-app smartprocess_logistics`. Frappe's `remove_app` drops
  the table behind every DocType of the app's modules, so **the app's own records are deleted**.
  This app deliberately registers no `before_uninstall` that would imply otherwise: the
  protection is the backup and an export of the app's records, both taken on purpose first.
  `after_uninstall` removes what the framework's cleanup cannot see — the app icon, which is
  named in Persian while the app title is not, and the four roles, except a role a user still
  holds.

## 11. Known limitations and unverified scenarios

- **A second-site install was not executed during this audit.** No new site may be created in
  this project's environment, so the fresh-install path was verified by reading the installed
  Frappe source (`installer.install_app`, `sync_for`, `compile_translations`) and by comparing
  the shipped `Sidebar` against the deployed one — 27 items, 5 section breaks, every child at
  `indent: 1`, identical in both. The commands above follow that source, but they have not been
  run end to end on a clean system.
- **A v16 patch level other than 16.55.2 / 16.54.0 is untested.** The supported range in
  `pyproject.toml` is wider than the version that was measured.
- **`app_email` is a placeholder** (`info@smartprocess.local`). It is shown in the Apps screen
  and used for nothing else.
- **`deployment/` is this stack's wiring, not part of a portable install.** The Compose overlay
  and its `.env.lab` exist to keep configuration across a container recreate on this particular
  Docker layout. Nothing in the app imports them, and no other system needs them.
- **Two `workspace_sidebar/*.json` files remain in the tree.** Frappe v16 stopped importing that
  folder; the supported v16 source is `sidebar/smartprocess_logistics/smartprocess_logistics.json`.
  The old files are inert. Editing them changes nothing.
- **The reference documents under `docs/` describe this development environment**, including
  container names and ports. Read them as history, not as install instructions.
