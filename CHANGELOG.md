# Changelog

All notable changes to `smartprocess_logistics`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## 0.7.7 — 2026-11-17

**The Logistics tile now sits in the ERPNext rail beside Accounting, Buying, Selling and Stock,
and its cube is a cargo container.**

### Fixed

- **The tile never reached the ERPNext rail.** The v16 rail is per app:
  `dock.js` draws `collect_dock_entries(get_sidebar_app())`, and `get_sidebar_app()` resolves
  `module_sidebars[current_module].app` through `boot.app_rail_host`. Without a mount this app
  kept a rail of its own, so its tile appeared only after the user was already inside that
  shell. The record now carries `mount_on: "erpnext"`: `Dock.mounted_apps` appends its rows to
  the `erpnext` base, the app drops out of `docked_apps()`, and `boot` reports
  `on_apps_screen: False` — the companion route India Compliance takes onto ERPNext. `modified`
  moved with the change, because a standard record the site already holds is only re-imported
  when the shipped file is newer.
- **The tile's tooltip was the untranslated Persian string.** `rail_entry` passes the row
  `title` through `_()`, so a Persian title there is a msgid nothing translates. The row now
  carries the msgid `SmartProcess Logistics`, which `locale/fa.po` already renders as
  «لجستیک پردازش هوشمند».
- **The icon was a plain cube (`stock`).** It is now `container`, the lucide cargo container.
  Every standard tile draws a `-soft` id from its own app's `module-icons.svg`, and none of
  those sprites carries a package, vehicle or warehouse shape, so the closest native logistics
  glyph comes from the lucide sprite `app_include_icons` already preloads on every desk page.
  It is drawn on the same 24 grid with the same 1.5 stroke and inherits the tile's hover and
  active colour, and no other Dock icon is touched.

### Added

- Three tests in `TestAppsScreenEntry`: the mount, the translated title msgid, and the icon.

### Unchanged

- No DocType, schema, patch, permission or Python runtime behaviour. The `SmartProcess
  Logistics` Sidebar keeps its 27 items and 5 sections, and `locale/fa.po` and its compiled
  form are not edited.

## 0.7.6 — 2026-11-16

**The app is now installable from GitHub on another ERPNext system, and the install command in
the README was wrong.**

### Added

- `docs/installation.md` — the portable install path in one place: the versions actually
  verified (`frappe` 16.55.2, `erpnext` 16.54.0), required and optional dependencies, the
  install, asset-build and translation commands with a `<site>` placeholder, the initial
  configuration checklist, warehouse classification, the company/account/currency assumptions,
  upgrade, rollback and the unverified scenarios.

### Fixed

- **The README's install command could not work.** It read
  `bench get-app <repository-url> smartprocess_logistics`. Measured against the installed bench
  (`bench get-app --help`, and `bench/commands/make.py`): the usage is
  `bench get-app [OPTIONS] [NAME]... GIT_URL`, the app name positional is a dummy kept for
  backward compatibility, and the **last** positional is the Git URL. The README therefore
  passed the app name as the URL and the URL as an ignored argument. The command is now
  `bench get-app <repository-url>` followed by a `git checkout` that pins the release tag.

### Unchanged

- No Python or JavaScript behaviour, no DocType, no Custom Field, no patch, no permission, no
  navigation, no schema change. `locale/fa.po` and its compiled form are untouched.

## 0.7.5 — 2026-11-16

**The Persian module label now survives a container recreate on the stack this app is deployed
to. It did not before, and 0.7.4 said it did.**

### Fixed

- **A catalogue compiled inside the running container is erased by the next recreate.** 0.7.4
  claimed the compiled `.mo` "lands in `sites/assets` … inside the site's volume, so it survives a
  container recreate". Measured on the deployment stack, that is false. `sites/assets` is a
  symlink to `bench/assets` (`lrwxrwxrwx … sites/assets -> /home/frappe/frappe-bench/assets`), and
  `bench/assets` is image content: only `sites` is a volume. `compile-translations` therefore wrote
  into a layer the container recreate discards. Proven by running it, recreating `backend`, and
  finding `sites/assets/locale/fa/LC_MESSAGES/smartprocess_logistics.mo` gone while
  `smartprocess_logistics.__version__` still read `0.7.4` from the bind mount.
- **The compiled catalogue now ships with the app** at
  `smartprocess_logistics/locale/fa/LC_MESSAGES/smartprocess_logistics.mo`, and
  `deployment/compose.smartprocess-logistics.yaml` binds that one file over
  `/home/frappe/frappe-bench/assets/locale/fa/LC_MESSAGES/smartprocess_logistics.mo` on `backend`.
  The file survives the recreate because it lives on the host, in the release snapshot. It is
  mounted file-over-file, not directory-over-directory: `assets/locale/fa/LC_MESSAGES/` also holds
  `frappe.mo` and `erpnext.mo`, and hiding those would break Persian site-wide.
- **The `.mo` is a source file in this arrangement**, so two tests hold it to the `.po`:
  `test_the_shipped_catalogue_is_the_compiled_po` compares the compiled catalogue against
  `locale/fa.po` entry by entry, and `test_a_message_outside_the_catalogue_is_left_alone` asserts
  `Purchase Invoice` is still untranslated by this app.

### Unchanged

- No schema change, no DocType, no Custom Field, no patch, no Python or JavaScript behaviour, no
  permission and no navigation change. `locale/fa.po` is untouched; only its compiled form is now
  shipped.

### Verified on the deployment stack

- `resolve_sidebar("SmartProcess Logistics", "Administrator")` → `لجستیک پردازش هوشمند`, and
  `resolve_sidebar("Accounts", "Administrator")` → `حسابداری`, so the one-entry catalogue did not
  reach another app's label.
- After `up -d --force-recreate backend` against the mounted snapshot, the label still resolves to
  Persian.

## 0.7.4 — 2026-11-16

**The Persian module label actually reaches the desk: the app now ships the compiled catalogue
Frappe v16 reads, not only the CSV it stopped reading.**

### Fixed

- **0.7.3 shipped a translation that v16 never compiled.** The claim in 0.7.3 that one
  `translations/fa.csv` row renames the module was wrong for this version. In v16 the Desk reads
  translations from a compiled catalogue only — `get_translations_from_mo`
  (`frappe/gettext/translate.py`) reads `sites/assets/locale/<lang>/LC_MESSAGES/<app>.mo` — and
  that catalogue is built from the `.po` files in the app's `locale/` directory:
  `compile_translations` calls `_compile_translation`, which returns immediately when
  `get_po_path(app, locale)` does not exist. `csv_to_po` does not rescue a CSV-only app either: it
  copies `msgstr` values into the messages the app's POT already holds, and an app with no
  `locale/main.pot` has an empty catalogue, so every row is discarded. `bench migrate` therefore
  left no `smartprocess_logistics.mo` behind, and the module kept its English title.
- **The app now ships `smartprocess_logistics/locale/fa.po`**, holding the module label and
  nothing else: `SmartProcess Logistics` → «لجستیک پردازش هوشمند». `bench build --app
  smartprocess_logistics` compiles it (`frappe/commands/utils.py:109`), and a stack that mounts
  `public/` instead of building can run `bench compile-translations --app smartprocess_logistics
  --locale fa`. The compiled `.mo` lands in `sites/assets/locale/fa/LC_MESSAGES/`, inside the
  `sites` volume, so it survives a container recreate.
- **The catalogue holds one entry on purpose.** A compiled catalogue is merged with every other
  app's catalogue for that language, so an entry for a msgid ERPNext also owns — `Purchase
  Invoice`, `all`, `physical` — would translate that message on every screen, not only this app's.
  The other rows `translations/fa.csv` carries stay where they are and remain inert under v16;
  moving them into a catalogue is a decision about site-wide wording and is not part of this fix.

### Unchanged

- No schema change, no new DocType, no new Custom Field, no patch, no Python or JavaScript change,
  and no change to a document, a ledger, a stock figure, a permission, a role or the navigation of
  0.7.3.

### Verified

- `tests/test_translations.py` holds the catalogue to exactly one entry, holds that entry's msgid
  to the `title` the module sidebar ships, holds the `fa.csv` brand row to the same Persian string,
  and refuses a second `.po` in `locale/`.
- `bench --site SmartProcessERP compile-translations --app smartprocess_logistics --locale fa`
  produced `sites/assets/locale/fa/LC_MESSAGES/smartprocess_logistics.mo`, and
  `get_translations_from_mo("fa", "smartprocess_logistics")` returns
  `{"SmartProcess Logistics": "لجستیک پردازش هوشمند"}`.

## 0.7.3 — 2026-11-16

**The sidebar breathes like the rest of the desk, the app answers to its Persian name, and the
site can show what this app depends on and what it changed.**

### Fixed

- **The excessive gap between collapsed sidebar sections is gone.** In Frappe v16 a Section Break
  renders one of two ways (`frappe/public/js/frappe/ui/sidebar/sidebar_item.html`): as an ordinary
  row with an icon when `indent == 1`, or as a bare heading when it is not. The heading branch
  carries `frappe/public/scss/desk/sidebar.scss:584`
  (`.sidebar-items > .section-item:has(.section-break):not(:first-child) { margin-top: 16px }`),
  and every Section Break in this app's sidebar had `indent: 0`, so each one drew that heading and
  its 16px margin on top of the row's own spacing. All five — «گزارش‌ها», «خرید», «انبار», «فروش»
  and «سریال لجستیک» — now carry `indent: 1`, the same value every sidebar ERPNext ships uses, and
  the standard spacing applies. «سریال لجستیک» also gains the `hash` icon so it looks like its
  neighbours. No CSS rule was added for the Sidebar, and no other app's CSS was touched.

### Added

- **The app is named «لجستیک پردازش هوشمند» in the desk.** `resolve_sidebar` renders its label as
  `_(base.title or shell)` (`frappe/desk/doctype/sidebar/sidebar.py:1661`) and the apps screen
  renders its title as `_(app_detail.get("title"))` (`frappe/apps.py:66`), so one translation row —
  `SmartProcess Logistics,لجستیک پردازش هوشمند` in `translations/fa.csv` — renames both displays
  without renaming the app. The app name, module name, DocTypes, routes, fields and package stay in
  English, a site without Persian keeps the English label, and no record is migrated.
- **«وابستگی‌ها و تغییرات بین‌ماژولی» is a tab on Logistics Settings.** It lists the apps this app
  declares (`frappe` and `erpnext` required, `smartprocess_erpnext_accounting` optional) and the
  changes it applied to the site — Custom Fields, the shipped fixture records, the four roles, the
  doc events and form scripts, and the stylesheet — with each entry's purpose, configuration,
  how it is applied (automatic or manual), verification method and removal path. The list is static
  metadata in `core/interop.py`, joined at read time with facts read from the site by one
  whitelisted GET (`interop.py`, guarded by `has_permission("Logistics Settings", "read")`). It
  stores nothing, writes nothing, adds no DocType and no column, and never says a change is
  applied when the site's own facts do not show it.

### Unchanged

- No schema change, no new DocType, no new Custom Field, no patch, and no change to a document, a
  ledger, a stock figure, a permission or a role. The navigation entries, reports and workspaces of
  0.7.2 are unchanged apart from the Section Break attributes above.

### Verified

- `tests/test_stage_d_artifacts.py` holds every Section Break to `indent == 1` and
  `collapsible == 1`, and holds the Persian module row in `translations/fa.csv`.
- `tests/test_interop.py` (25 tests) holds the registry to `hooks_composition`, the shipped
  `custom/*.json` and `fixtures/*.json`, the roles `setup/roles.py` imports, the CSS path in
  `hooks.py`, and proves the optional app is never imported and the reader never writes.
- `python -m unittest discover -s smartprocess_logistics/tests -t .` — **1,687 tests, OK**; with
  the isolated interpreter that has `openpyxl`, 2 skips remain and both are by design.

## 0.7.2 — 2026-11-16

**The serial pages are one section again.** They were split across three Section Breaks —
«سریال لجستیک» holding only the Workspace link, «سریال‌ها» holding the three serial pages and
«تنظیمات» holding the settings page — so the panel showed three unrelated groups for one
feature. They are now one collapsible «سریال لجستیک» section.

### Fixed

- **«سریال لجستیک» is a single collapsible section.** It holds the `Logistics` Workspace link
  (سریال لجستیک), `Logistics Serial` (شماره سریال), `Logistics Serial Assignment` (ارجاع سریال به
  فاکتور), `Logistics Import Session` (پیش‌نمایش درج) and `Logistics Settings` (تنظیمات سریال), each
  nested with `child: 1`.
- **The «سریال‌ها» and «تنظیمات» Section Breaks are gone.** The panel no longer repeats a header
  for pages that belong to the same feature.

### Unchanged

- Every link's label, icon, route, target DocType and position relative to the other pages; the
  «گزارش‌ها», «خرید», «انبار» and «فروش» sections; the «انبار و خرید» link; both Workspaces and
  their cards; report roles and record permissions. Warehouse classification, inventory figures,
  accounting, serial rules and measurement snapshots are not touched.

### Verified

- `tests/test_desk_artifacts.py` gains
  `test_the_serial_pages_share_one_collapsible_section`, which fails on the 0.7.1 layout (three
  owners for the serial pages, seven sections) and passes on this one (one owner, five sections).
- Every DocType, Report and Workspace the sidebar links to was resolved on the site; no link
  targets a document that is missing or disabled, and no `Custom Sidebar` row overrides the
  shipped definition.
- The reloaded `Sidebar` document carries 27 items, and the resolved boot payload nests all five
  serial links under the one section.

## 0.7.1 — 2026-11-16

**The module sidebar renders its sections again.** In Frappe 16 the Desk module panel nests a
link under the Section Break above it only when the link carries `child: 1`; a link left at
`child: 0` is dropped without an error, and a Section Break with no nested link draws nothing at
all. Every link in this app's module sidebar was written that way, so «گزارش‌ها», «خرید»,
«انبار» and «فروش» were invisible on the live site while the serial sections — the only rows
carrying `child: 1` — rendered normally. The links still reachable through the Workspaces and the
workspace sidebars hid the omission.

### Fixed

- **The «گزارش‌ها» section of the module sidebar is a real, expandable group.** All eight Logistics
  reports sit under it as `child: 1` links: «تراز موجودی», `Warehouse Inventory`, `Inventory and
  Procurement Dashboard`, `Procurement Shortage`, «کالاهای خریداری‌شده و دریافت‌نشده»,
  `Sales Orders Ready for Invoicing`, `Item Weight and Volume` and «کسری سریال فروش».
- **«کسری سریال فروش» is grouped with the other reports.** It was listed under «سریال لجستیک»
  and is now under «گزارش‌ها», with its label, route and permission unchanged.
- **Two reports are reachable from the module panel for the first time.** `Inventory and
  Procurement Dashboard` and `Procurement Shortage` shipped and were documented but appeared in no
  module sidebar and no Workspace.
- **«خرید», «انبار», «سریال‌ها» and «تنظیمات» render their links.** Each row under a Section Break
  is nested in it, so no section is left empty.

### Unchanged

- Report routes, titles, roles and permissions; both Workspaces and their «گزارش‌ها» cards; the two
  legacy workspace sidebars; «انبار و خرید», «سریال لجستیک» and «تنظیمات سریال» remain outside the
  reports section. Warehouse classification, inventory figures, accounting, serial rules and
  measurement snapshots are not touched.

### Verified

- Database-free suite: **1659 tests, OK, 2 skipped** in the interpreter that carries `openpyxl`
  (12 skipped in the system interpreter, which does not). Six new tests read the module sidebar
  and fail against the 0.7.0 file.

## 0.7.0 — 2026-11-16

**Reconciliation of the phase-2 tree onto the published 0.6.0 application.** This release adds the
Purchased Not Received report and the warehouse-classification settings table, and makes the three
operational reports select the user's permitted default company so they load when opened. It keeps
every 0.6.0 capability: the serial app, «تراز موجودی», the authorized-stock selectors, the Purchase
Invoice cancel guard, the measurement panels, the fixtures and both Logistics workspaces. The
Commitment work remains deferred and excluded.

### Added

- **«کالاهای خریداری‌شده و دریافت‌نشده» (`Purchased Not Received`).** One row per submitted Purchase
  Invoice item line, with the accounting receipt ERPNext recorded beside the quantity that reached
  a sellable warehouse. Partial receipts, purchase returns, cancellations, transit transfers and
  unmatched receipt rows keep their own meaning: a line received only into a Transit or Virtual
  warehouse is received and not arrived, and an undeterminable physical quantity is reported as
  such rather than as zero.
- **Warehouse classification settings.** A `Logistics Settings` table lists every `Warehouse Type`
  on the site plus the «بدون نوع انبار» bucket and stores one of three stable codes — `physical`,
  `virtual`, `transit` — per type. Persian is a display label only. An empty table preserves the
  effective policy exactly, so an unconfigured site behaves as it did in 0.6.0; `Transit` remains
  non-deliverable by default.
- **`Logistics Warehouse Classification`** child DocType, its controller and its whitelisted
  settings reader, with write permission re-checked server-side.

### Changed

- The three operational reports — «تراز موجودی», `Warehouse Inventory` and `Purchased Not Received`
  — default their mandatory company filter to `frappe.defaults.get_user_default("Company")`, so a
  user with a permitted default company sees results on open instead of "Please set filters".
  Server-side company and report permission checks are unchanged.
- The «انبار و خرید» Workspace card «گزارش‌ها» lists the new report, and the Workspace Sidebar's
  collapsible «گزارش‌ها» section links it too. Both Logistics workspaces, their routes, their
  operational shortcuts and their role permissions are preserved.

### Verified

- Database-free suite: **1653 tests, OK, 2 skipped** in the interpreter that carries `openpyxl`
  (12 skipped in the system interpreter, which does not).

## 0.6.0 — 2026-11-15

**GitHub publication of the approved tree at `f50e761`.** The application source is the tree
deployed and verified on `SmartProcessERP`. This release adds version metadata, release
documentation, and the corrections to documentation drift; it does not change the deployed
application.

### Added

- **«تراز موجودی»** as the primary operational report. It shows physical, Transit and Virtual
  stock as separate columns, and only physical plus Transit count toward operational shortage.
- **Purchase Receipt weight/volume completion**: document-level accepted/rejected totals,
  UOM-normalized net/gross/volume, missing-vs-zero coverage warnings, and a read-only Desk panel
  backed by a GET endpoint.
- **v16 app entry**: `add_to_apps_screen`, a standard `Dock` record, and the compatibility
  `desktop_icon/لجستیک.json` that links the «انبار و خرید» Workspace Sidebar.
- **Warehouse Type fixtures**: the explicit `Physical` type, its default for new Warehouses, and
  the translated-doctype property.
- **Purchase Invoice cancel guard.** A submitted Purchase Invoice cannot be cancelled while a
  submitted Purchase Receipt still depends on it. `before_cancel` refuses with a Persian message
  that names the blocking receipts, so the operator is told to cancel the receipt first. It reads
  the receipt row's `purchase_invoice` and `purchase_invoice_item` links and an optional receipt
  header link, ignores draft, cancelled and other-company receipts, and locks the invoice row so
  a concurrent receipt submit cannot race it. ERPNext's back-link check stays the backstop.

### Changed

- Quotation and Sales Order selectors show physical and Transit stock separately and exclude
  Virtual warehouses from both Item availability and warehouse choices.
- Sales Invoice selectors remain physical-only.
- Procurement Shortage and the operational dashboard share the same physical/Transit/Virtual
  calculation; a rejected Purchase Receipt quantity must name a Virtual warehouse, on draft,
  submit and return.
- The README documents the required invoice-first order: submitted Purchase Order → submitted
  Purchase Invoice with `update_stock = 0` → Purchase Receipt linked to the invoice row →
  Material Transfer from Transit to a physical warehouse before sale or delivery.

### Fixed

- Legacy procurement shortage no longer counts Virtual stock as coverage; `virtual_stock` is
  Virtual-only and `transit_stock` is separate.
- Shared shortage/report calculations no longer drift between «تراز موجودی» and
  `Procurement Shortage`.
- Rejected quantities in a non-Virtual warehouse are refused instead of silently becoming
  sellable stock.
- The rejected-warehouse type lookup on `Purchase Receipt` is scoped to the document's company,
  so a row cannot probe another company's warehouse classification.
- A site's `sp_virtual_warehouse_types` override now decides membership for `policy.kind` exactly
  as it already did for `policy.is_virtual_type`. One list, one answer: an override that leaves
  `Transit` out makes that stock physical in every report instead of physical in one and transit
  in another.
- «تراز موجودی» reads `sp_recommended_purchase_net_of_open_po` like the other two reports, so an
  open Purchase Order nets the purchase suggestion there too when the site says it should.
- A missing `sp_serial_required` custom field no longer switches off the whole Purchase Receipt
  handler. The standard `is_stock_item` flag is read unconditionally, so the invoice-first and
  rejected-warehouse rules still run on a site whose fixtures have not been synced yet.

### Verified

- Full database-free suite on the release commit: **1592 tests OK**, 12 skipped with the system
  interpreter. The focused purchase rules suite is 32 tests, all passing.
- Isolated end-to-end cancellation on `SmartProcessERP` in `_Test Company`: a submitted Purchase
  Invoice (`BI-260158`) and its submitted Purchase Receipt (`BR-050005`) were created; cancelling
  the invoice first was refused with a `ValidationError` that named `BR-050005` and left the
  invoice submitted; cancelling the receipt first and the invoice second succeeded; both Stock
  Ledger Entries and all four GL Entries ended cancelled with zero net; the Bin quantity returned
  to its starting value; both documents were then deleted, leaving no Purchase Invoice, Purchase
  Receipt, Stock Ledger Entry or GL Entry behind.
- The whole package was synced to the deployed mount and verified byte-for-byte; cache was
  cleared; the five application services were restarted. `frappe.ping` answers `pong` on port
  8080, and the last two hours hold no `Error Log` entry and no container traceback.
- Backups taken before deployment: database, public files, private files and `site_config_backup`
  as `20261112_181109-SmartProcessERP-…` under `sites/SmartProcessERP/private/backups/`, and the
  pre-sync deployed app as `deployed_app_before_sync_20261112_1811.tar.gz` outside the repository.

### Known limitations

- The Serial Ledger has no Report document: `core.reports.build_register_query` is written and
  tested, but it is reachable only from a console or a future report.
- There is no custom Stock Movement report. ERPNext's standard `Stock Ledger` report is the
  supported movement view.
- This release is source and documentation only. No production deployment, migration or restart
  is part of it.
- Purchase serial tracking through Purchase Receipt, warehouse transfer, sales and warranty is
  designed but not implemented; serial work remains Sales-Invoice-side.
- The cancel guard has three edges. A cancel that sets `doc.flags.ignore_links` bypasses it,
  because that is the flag ERPNext's own back-link check honours and a documented data repair
  needs it; a cross-company `purchase_invoice_item` Data link is not a Link and no standard check
  sees it, so it is ignored by design; and the `purchase_invoice` column of
  `Purchase Receipt Item` is not indexed, so the lookup costs a scan on a large receipt table.
- The Purchase Receipt weight/volume panel has not been accepted visually in a real browser.
- `sp_virtual_stock_covers_procurement` is retained as a legacy, non-operational setting.
- Purchase Invoice GL errors remain in the Accounting project; this app does not change ledger
  behaviour.

## 0.5.1 — 2026-10-24

**GitHub publication of the production-approved 0.5.0 functionality.** The application source in
this release is the tree deployed and verified on `SmartProcessERP`. This release adds version
metadata and release documentation. It does not change the deployed application.

### Included

- The invoice-first domestic and foreign purchase workflow. Stock Purchase Invoices use
  `update_stock = 0`. A Purchase Receipt receives the stock.
- Virtual and physical warehouse classification from the standard `Warehouse Type`. Movement
  uses the standard Stock Entry Material Transfer.
- The Stock Entry Item selector for Material Transfer. It filters by the source warehouse and
  shows the available quantity.
- Five Logistics reports: `Warehouse Inventory`, `Inventory and Procurement Dashboard`,
  `Sales Orders Ready for Invoicing`, `Procurement Shortage`, and `Item Weight and Volume`.
  The existing `Missing Sales Serials` report remains in the app.
- The Sales Order billing-progress panel. It reads the existing invoice links with a GET request.
- The Sales Invoice physical-warehouse selectors and the stock-aware Item search.
- The Workspace «انبار و خرید» permission fix. The Workspace has no module gate. The allowed
  roles are Stock User, Purchase User, Sales User, and System Manager.
- The approved integrity checks, scan station, serial transfer, restore operations, and their
  tests.

### Verified

`SmartProcessERP` runs the approved tree on Frappe v16 and ERPNext 16.35.0. The deployment held
GL Entry at 2304 rows, Stock Ledger Entry at 1083 rows, and Bin at 178 rows. The Bin quantity was
848 and the stock value was 12,806,345,000. No Bin row had negative stock. The database-free
suite and integration evidence are in `docs/test-manifest.md`. The existing `v0.5.0` GitHub tag
remains unchanged.

### Known limitations

- Authenticated browser acceptance on production remains pending. The documented credentials
  returned `401` during the reopening. The guest login page, HTTP access, and WebSocket passed.
- Most production users have language `en`. Persian RTL and the Persianizer font need user-level
  acceptance.
- No active submitted Sales Order existed for a live progress-panel test.
- The Commitment work remains deferred. This release excludes it.

### Future

- Add Persian/Jalali month and year filters and true Jalali monthly aggregation. Keep Gregorian
  storage in the database.

## 0.5.0 — 2026-10-22

**Purchasing, virtual warehouses, standard Material Transfer, and the source-warehouse Item
selector.** This release covers the invoice-first purchase workflow and the warehouse
classification built on ERPNext's standard `Warehouse Type`. It does **not** include the paused
Sales Invoice committed-stock UI or any change to sales fulfilment; that work remains unshipped
and uncommitted. Nothing here is tagged or published by this file.

Verified on `SmartProcessERP` (Frappe v16, ERPNext 16.35.0, port 8080) with the database-free
suite and the integration proofs named in `docs/test-manifest.md`. The suite runs **1,268 tests,
OK (1 skipped)** under `.venv` with `openpyxl`; the same suite under a Python without `openpyxl`
runs 1,268 tests, OK with 11 skipped (the XLSX reader tests).

### Added

- **The Stock Entry Item Code selector can be limited to the source warehouse.** In a
  `Material Transfer`, the standard Link offers only items with a positive `Bin.actual_qty` in
  the row's `s_warehouse`, else the document's `from_warehouse`, and shows the quantity beside
  the item (`Stock: 120 Nos`). The link keeps Frappe's search, keyboard navigation and paging.
  No source warehouse shows a Persian prompt and an empty list rather than an unfiltered one.
  Changing the source after choosing an item warns in Persian and leaves the row and its
  quantity untouched.
- **A permission-aware, read-only Link query.** `warehouse.item_search.items_in_stock` refuses a
  missing, group, wrong-company or unreadable warehouse, requires Item read, and applies
  `get_match_cond("Item")` (role permissions, Item user permissions, `if_owner` and the site's
  permission-query hooks) to the `Item`/`Bin` statement. It writes nothing and does not replace
  the standard submit-time validations.
- **Invoice-first purchasing.** Every domestic and foreign stock Purchase Invoice bills with
  `update_stock = 0`; stock is received only by a Purchase Receipt. A stock receipt row must link
  a submitted Purchase Invoice and match its company, supplier, item, row, UOM and conversion
  factor. Partial and multiple receipts against one invoice remain standard.
- **Virtual and physical warehouses from the standard `Warehouse Type`.** A warehouse whose type
  is `Virtual`, `مجازی` or `Transit` (or a site override in `sp_virtual_warehouse_types`) is
  virtual. Its stock is real inventory and part of company assets, but it is not sellable,
  deliverable or reservable. Movement in all four directions uses the standard Stock Entry
  **Material Transfer**; a non-transfer purpose touching a virtual warehouse is refused.
- **Financial-only purchase returns.** A Purchase Invoice Return/Debit Note may post with
  `update_stock = 0`; a stock-affecting PI return is refused and stock returns use the standard
  Purchase Receipt Return.

### Changed

- The Stock Entry `item_code` query is replaced only for `Material Transfer`; every other purpose
  keeps ERPNext's own `erpnext.queries.item({is_stock_item: 1})`.
- A linked stock Purchase Receipt now applies the invoice-row checks to every row, not only rows
  whose item is marked `sp_serial_required`.
- The paused committed-stock feature is isolated behind `hooks_commitment.py`. `hooks.py` builds
  the approved composition from `hooks_composition.py` and loads the development extension only
  when that module is present; the release subset excludes it. The development tree keeps the
  same hooks — the committed-stock panel script, the two Sales Invoice lifecycle handlers and the
  combined Delivery Note validator — while the approved release imports and registers without
  any commitment module.
- The approved managed Delivery Note rule (I30) remains in the release: a managed row must name
  its own submitted invoice row, item, company and warehouse company; a stock row names a source;
  and an invoice that already moved stock is not deducted twice. The committed-warehouse checks
  (I36) stay in the development-only `commitment_delivery.py`. The release runs the approved rule
  plus the virtual-warehouse refusal; the development tree runs the combined validator.

### Fixed

- **Malformed Link filters return an empty list instead of a server error.** A direct caller
  sending a non-string `warehouse`, `company` or `item_code` (for example
  `{"warehouse": ["=", "WH-001"]}`) is refused at the boundary; normal Link searches are
  unchanged.
- A Product Bundle cannot ship its components from a virtual warehouse through `packed_items`;
  the same bundle sold from a physical warehouse is unaffected.
- Purchase Receipt rows can no longer name a supplier, company, item or invoice row that differs
  from the linked Purchase Invoice, and a UOM/conversion-factor mismatch is refused.

### Known limitations

- The Item selector is a search convenience. The server filters suggestions; the standard
  Purchase/Stock controllers still decide quantity, negative stock, valuation and permissions at
  submit.
- Items with zero or negative stock in the source warehouse are not offered, and manually typing
  such an item into the Link is refused by standard Link validation. A row selected earlier is
  never silently cleared when the source warehouse changes.
- Group and wrong-company warehouses produce no suggestions. Disabled warehouses follow standard
  ERPNext behaviour: the Stock Entry warehouse Link filters company and non-group, and an
  existing disabled value is left to the standard document lifecycle.
- The query returns at most 500 rows per request and formats quantities to three decimals.
- Landed Cost Voucher plus a partial Purchase Receipt Return can leave a residual
  `Stock Received But Not Billed` balance: the physical return is valued at the LCV-adjusted
  rate while a financial Debit Note credits the invoice rate. This is standard ERPNext
  behaviour, not a duplicate posting. A fully matched physical and financial return clears the
  invoice portion; the remaining LCV portion needs a freight credit note or an LCV adjustment.
- Large-catalog behaviour was checked by query shape, indexes and paging; it was not benchmarked
  against a production-scale Item master.

### Compatibility

- Frappe v16 and ERPNext 16.35.0; no files in `frappe` or `erpnext` are modified.
- No schema change, custom field or migration is required by the Item filter. The purchase and
  warehouse guards use standard DocTypes and `doc_events`.
- The paused Sales Invoice committed-stock UI is not part of this release.

## 0.4.0 — 2026-10-15

**The release candidate: run against a real site, not installed on `SmartProcessERP` and not
migrated there.** Everything below has been exercised end to end on the isolated lab
(`sp_logistics_lab`) and nowhere else. Not installed on a live site means exactly that: the two
DocTypes do not exist on `SmartProcessERP`, and nothing in this section has run there. Phase 19
executed all eight phases of `docs/integration-test-plan.md` on the isolated lab
(`sp_logistics_lab`, port 8095): the two DocTypes migrate creates, the four roles, a submitted
record that refuses to be changed or deleted, an eighteen-serial transfer onto an amendment with
the cancelled invoice left untouched, ten places contested by two processes, the historical
restore against the ordinary one, and the audit records placed from an archive. The run found four
defects — three of which no database-free suite could reach — and all four were fixed and re-run
inside it. What it did **not** do is the uninstall/reinstall cycle of phase 7, whose removal and
reinstall steps belong to a person, and nothing here has been installed on or migrated into
`SmartProcessERP`. See `docs/test-manifest.md`, phase 19.

### Added

- **A cancelled invoice's serials can be moved onto its amendment.** When ERPNext cancels an
  invoice and amends it, it keeps the cancelled document and its registrations and gives the
  amendment **new** item rows — so the serials a warehouse scanned sit on a document that is now
  history, and the amendment reads as a shortage. «انتقال سریال» on an amended invoice offers to
  move them: a preview of what would happen to every serial, the operator's selection, an explicit
  confirmation, and a result.
- **Nothing is lost moving it.** The cancelled invoice is never written to — its registrations are
  read, named in the record, and left exactly as they were. Every serial it held comes out placed,
  refused with a reason, or left because the operator did not select it, and the record says which.
  What does not fit (an amendment that invoices less than its predecessor) is the ordinary case
  rather than a failure, and each such serial is named with its reason.
- **`Logistics Serial Transfer`** and its child: a submitted record of what was done, one row per
  serial, naming the source registration, the target row, the outcome and the reason. It cannot be
  rewritten or deleted — by any role, by the Administrator included — because the framework refuses
  both for a submitted document, and no role holds `write`, `delete`, `cancel` or `amend` on it.
- **A serial live on another active invoice is a warning, not an error.** The operator is told and
  has to acknowledge it, and the acknowledgment is recorded. This app has never had a site-wide
  uniqueness rule over serial text, and inventing one now would call the operator's own existing
  data a conflict.
- **`core/transfer.py`**: every rule of the above as a pure function over plain data, with no
  Frappe import — the gates, the placement, the three-way outcome, the audit record and the values
  written to the amendment.


- **The historical restore has a button.** «بازآوری تاریخچهٔ فاکتورهای لغو‌شده» on
  `Logistics Settings`, shown only to a holder of `Logistics Serial Archivist`, with its own
  preview, a warning that says what it does before the operator confirms, and a result reported
  under its own title. It is a button of its own rather than a mode of the ordinary one: an
  operator who means the ordinary restore must not be one click from an act that writes to
  cancelled invoices.
- **An archive of transfer records must agree with itself.** `core.export.check_transfer_records`
  refuses a file whose rows name a record it does not contain, whose counts do not equal its own
  rows, whose outcomes do not add up to its source total, or whose rows contradict their own
  result — none of which a recomputed digest can catch. Read on both previews and both restores,
  so a hand-made archive is refused before anything is planned.
- **`docs/integration-test-plan.md`**: the isolated run this app has never had, in eight phases.


- **The audit records can be restored, and a restored record says so.** `restored_from`,
  `restored_on` and `restored_by` are written by the third restore and by nothing else, so an
  original record carries them empty. The record's *act* still belongs to whoever performed it —
  `creation` and `owner` travel from the file, as every restore does — and the marker is what makes
  the document a restored one rather than one that claims an act happened here.
- **A name collision is refused, and nothing is overwritten or renamed.** The naming series is per
  site (`LST-.YYYY.-.#####`), so two sites both produce `LST-2026-00001` for different transfers.
  Such a record is reported as `RECORD_EXISTS`, with its name. Overwriting would destroy an audit
  record; renaming would lose the identity the archive exists to carry.
- **A record is placed whole, or not at all.** Every obstacle is found before a single write — a
  taken name, a target invoice that is missing or has since been cancelled, a row whose serial is
  not on this site — so an operator learns what stands in the way rather than watching a file half
  apply. A record missing a row would be a record that lies, so one blocked row blocks the record.
- **«بازآوری سابقه‌های انتقال»** on `Logistics Settings`, for a holder of
  `Logistics Serial Archivist`, with its own preview and its own warning.
- **`core.export.MODES`**: the three restores as three named modes — an assignment on a live
  invoice, an assignment on a cancelled one, a transfer record — instead of a growing set of
  switches. They are disjoint, so which restore this is is answered by the mode rather than by
  reading a combination of flags.

### Changed

- **The export format is version 2.** A document now carries the transfer records and their rows,
  beside the serials and the assignments. They are carried because an uninstall drops their tables
  and this file is what survives the removal. The two *assignment* restores still place none of
  them and report only how many the file holds — so a site with a thousand transfers does not
  preview a thousand conflicts — and placing them is the third restore's own, separately authorized
  job. A version 1 file is read exactly as it always was; its absent sections count as empty, and a
  version 1 file that has lost a section it *does* declare is still refused.
- **I22 has one exception, named and gated.** A cancelled invoice could not be added to by any
  path. It can now, by exactly one pair of endpoints — `preview_cancelled_serial_history` and
  `restore_cancelled_serial_history` — and only for rows whose invoice is cancelled; a row on a
  live invoice is reported as `NOT_HISTORY` and left to the ordinary restore, so the pair is not a
  superset of it. Both ask for the new `Logistics Serial Archivist` role **on top of** a Manager
  level, so a site with several Managers decides which of them may do this. The ordinary restore is
  unchanged and pinned by its own tests.
- **The refusal sentence points at the way out.** A cancelled-invoice row reported by the ordinary
  preview now names the endpoint that can place it, because a file the operator cannot use and no
  explanation is the version of this that helps nobody.

### Fixed

- **A collision used to be reported as a concurrency failure.** Two sites both numbering from
  `LST-2026-00001` met on the primary key, and the sentence the ordinary restore printed was
  «هم‌زمان درخواست دیگری…» — which invites a retry that can never succeed. The collision has its
  own reason now, and the pre-flight finds it before the write.
- **The preview's line about transfer records contradicted the button.** It said they are not
  restored, in all three modes — including the one that restores them.
- **The two navigation files were deleted by every `bench migrate`.** `remove_orphan_entities`
  looks for an app-level `Workspace Sidebar` or `Desktop Icon` at `<folder>/<scrub(name)>.json`
  and deletes the record when that path is missing, so a Latin file name under a Persian document
  name lost the app's sidebar and its icon — measured on the lab, where the records were imported
  on install and removed on the first migrate. Both files are now named after their documents,
  as `frappe.scrub` produces them, and a test pins the rule.
- **The second preview of a transfer crashed on a target that already held registrations.**
  `gateway.read_target_rows` counted through a helper written for row *dicts* and handed it
  `ExistingAssignment` objects, so `AttributeError` — but only when the amendment already carried
  a serial, which every test of this feature had avoided by previewing against an empty one. Found
  by pressing the button twice in a row, on the lab.
- **A restricted caller restoring a file that named another company's serial lost the whole
  request.** The serial was invisible in that caller's world — the only assignment carrying it is
  in a company they cannot see — so the planner called the master new and inserted it, colliding
  on the primary key of `Logistics Serial`. The sentence the operator saw was the one for a
  concurrent request. `core.export.World` now carries the **names** the document mentions, which
  cannot be scoped to a company and are unique over the site, and a name that is taken is a serial
  this site already has.
- **A restored audit record could be asked to arrive as a draft.** The record's `docstatus`
  travels in the file, and neither the archive validator nor the plan questioned it — so a
  hand-made file, sealed again with a digest computed *from itself*, could place a record at
  `docstatus = 0`, which is the one state I24 exists to prevent: a draft can be edited and
  deleted. The validator refuses such a file whole (a fifth rule, beside the four it already
  checked), and the plan writes the submitted value rather than carrying the file's.
- **Four endpoints read the site's settings before they were authorized.** Every one of them
  carried a comment saying nothing this app reads on behalf of a request is read before the
  request is allowed; the settings row — read for the serial-length policy, never to decide
  whether the caller may act — was read anyway, in `get_serial_assignments`, `set_row_serials`,
  `add_row_serial` and `add_row_serials`, and in `_transfer_request` for both transfer endpoints.
  The read now happens after the check, which is a rule the tests enforce rather than a promise
  the comments make: `test_no_endpoint_reads_the_site_settings_before_it_is_authorized` compares
  the two line numbers for every endpoint and every helper.
- **The names a file mentions were read from one section too few.** `core.export.world_names`
  tells the gateway which documents the plan is about, and it took them from the serial and
  assignment rows only — so a file whose assignment section was empty (a site whose registrations
  have since been removed, which is exactly what a recovery is for) named no invoice at all, and
  every audit record came back `INVOICE_MISSING` with its amendment sitting on the site. Found in
  the recovery rehearsal, where a file was built to place a record; the records' own
  `source_invoice`/`target_invoice` and company are named now, and a test pins it.
- **A record name held by another company was reported as a concurrent request.** The pre-flight
  can only see the record names of the caller's own companies — that is the disclosure policy, not
  an oversight — so a name from anywhere else arrived at the insert and collided there, where the
  sentence was the one for a race: an invitation to retry something that can never succeed. The
  records' insert now has its own handler and says the name is taken.
- **`preview_restore_audit_records` answered `EXPORT_BAD_SHAPE` for every file.** The records mode
  refuses a document with no restore marker, and the shared preview helper built its plan without
  one, so the third restore could never be reached through the API: the button's preview always
  failed and the apply behind it was unreachable. The preview passes the same stamp the apply does.

## 0.3.0 — 2026-10-14

The app's records can now leave the site whole and come back whole: two buttons on
`Logistics Settings` export every serial, every assignment and every relationship they hold into
one JSON file, and import it again after the app has been removed and installed anew. The
permanent in-database archive that 0.2.0 added is removed, and that is the point of the release.

### Release notes

- **The serial window is a scan station now.** «ثبت سریال» opens one column per invoice item row
  — model, required, registered and remaining, a standing input, one cell per serial with its own
  delete icon, and a green tick when a column completes — instead of a row of boxes. It writes one
  serial per request through the new `add_row_serial`, which **adds** to the row rather than
  replacing it: two operators scanning the same invoice no longer overwrite each other's labels.
  A column's completion makes one short chime, switchable per user; a refused label keeps its text
  and shows the server's own sentence.
- **The stuck «در حال ثبت...» overlay is fixed at the root.** The dialog used
  `frappe.dom.freeze`/`unfreeze`, which is counter-based: `unfreeze()` returns early when the
  counter is zero, so a single unpaired `freeze()` — reachable from a scan arriving while another
  was in flight, from a chain whose work threw before returning a promise, or from the form
  reloading mid-write — left the counter above zero and the overlay over the page until a reload.
  The station keeps its own busy state now, released from a `finally` that cannot be skipped, and
  it shows progress over itself rather than over the page.
- **An operator must be able to read the invoice.** The station's every call passes
  `gateway.assert_can_read_invoice`, so its user needs a role that grants read on `Sales Invoice`
  (`Accounts User` or `Accounts Manager`, measured; `Sales User` alone is not enough on a current
  ERPNext) as well as a Logistics role.
- **Historical invoices are audited again.** The missing-serial report covered only invoices
  created at or after `Logistics Settings.installed_on`, so every invoice older than the app was
  left out of it however short it was — measured on the test site, where two such invoices holding
  twenty-four registrations between them could not appear at all. That timestamp is no longer read
  by reporting: what makes a row a shortage is its item's `sp_serial_required` flag, which says
  nothing about when the invoice was written, so coverage survives an upgrade, a reinstall, a
  restore, a move to another server, and any change to the install marker. `core.eligibility` now
  states that rule — marked items are mandatory, unmarked ones are optional and can never be short
  — and the scan window reads it too: an unmarked column is labelled «اختیاری» and never turns
  green or rings, which is the same reason the report would never have listed it.
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

- **A workbook in the repository, so the reading order is pinned by something every checkout has.**
  `tests/fixtures/synthetic-workbook.xlsx` is the operator's own file's *shape* with values that
  belong to nobody — a stray cell in the first column, and `*1`, `*2`, `*3` over five, ten and
  twenty values, three of them stored as numbers and one as text that looks like a number.
  `TestTheSyntheticWorkbook` asserts its column-by-column reading exactly, and the node and browser
  checks are built from the same values, so the three suites read one file. The operator's own
  workbook is still checked when it is on the machine, as an optional test that asserts its shape
  and not its serials.
- `add_row_serials` — one row's labels added in one request, accepted whole or refused whole,
  through the same rule set as a single label. The station's save uses it, so a column is one round
  trip rather than one per label; the refusal names the serials it is about, because a batch's
  refusal has to say which label to remove. `core.orchestration.build_append_plan` takes a tuple
  now, so one rule serves one label and twenty.
- **One intake, three ways in.** A scanned label, a pasted list and an imported file now meet at
  one `intake` in the station: it splits what a list is, reads the two commands, judges each serial
  on its own, refuses the duplicates this invoice already holds, and stages the rest in the columns.
  Nothing writes but the one «ذخیره سریال‌ها», through `add_row_serials`, and it writes what the
  columns held when it was pressed.
- **`#N` is the same command as `*N`.** Both select the invoice row the columns show as `#N`, and
  `N` is any positive whole number. A number the invoice has no row for is answered with a sentence
  and moves nothing: the values after it stay where they were.
- **A workbook is read column by column, top to bottom, and only where it holds something.** The
  server used to route a file by the marker patterns in `Logistics Settings` and hand the window a
  row per serial; it now returns the file's nonempty cells as values — `A1…An, B1…Bn, …` — and the
  window runs them through the same intake a paste goes through, so `#2` in the first cell of a
  column reaches the serials beneath it and nothing else. Blank cells are not values, not counted
  and never refused for: a worksheet formatted to its bottom is read for the data in it, and the
  work of looking is bounded by `max_scanned_cells` rather than by the content limits.
- **Reading a file no longer writes.** `prepare_import_preview` answered with a stored session that
  `confirm_import` could apply; it now answers with values and stores nothing, so the site's
  `Logistics Import Session` table stops growing every time somebody looks at a file. The session
  machinery and `confirm_import` remain as the API they always were, unreachable from the window.
- **The column heading is `Item.item_name`, on one line, clipped at its start.** A long model name
  keeps its end — the lens size and the suffix that tell two cameras apart — and the whole name is
  in the tooltip.
- **Clipboard paste.** A pasted list is split by the separators this site actually uses — served
  with the invoice, interpreted by `core.delimiters.served_delimiters` — staged into the columns,
  and reported per token: the valid pieces go in, the duplicate and the impossible ones are named
  and dropped. `*N` names an invoice row and the site's own next-column command moves the cursor,
  both only as a whole token.
- **A file import no longer writes.** The preview's serials are staged into the same columns, the
  surplus is red there, and removing one is removing a pending cell — so the file path has no
  confirm step, no second write path and no set-aside round trip left to keep in step with the
  scanner. `prepare_import_preview` lost the `exclude` parameter and the preview table, and the
  window keeps no session.
- **The Save button cannot be left disabled.** A call that failed *before* it returned a promise
  never attached a `finally`, so the busy flag it had already set stayed set and the window showed
  «در حال ثبت…» until it was reloaded. Every async action in the window now runs through a helper
  whose promise exists before the work does, and the save snapshots the columns at the moment it is
  pressed, so the fix cannot change what a save writes.
- `deployment/lab/probe_user_workbook.py`: a real workbook with a `printerSettings` part, through
  the whole preview — preflight, parse and placement — with no site involved.
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

- **Only the invoice's serial rows are in the dialog, and one rule decides every colour.** Two
  different questions used to be asked of one row — *required* against *merely permitted* — and
  answered with two vocabularies: a required column was green when the server called it complete,
  and an unmarked row was a column of its own with an «اختیاری» label, never complete, and a second
  green of its own for being populated. Now a row whose item is not marked `sp_serial_required` is
  **not a serial row**: it is not served, so it is not drawn, not navigated to, and no paste,
  import, scan or `*N` can put a label on it — a command naming it is answered with a sentence
  naming the row, and never obeyed by a different one. The served rows keep the **invoice's own row
  numbers**, so a hidden row leaves a gap rather than renumbering the rest.
- **The four states are one machine.** `core.eligibility.row_state` — and the window's copy of it,
  which counts the labels the operator has in hand as well as the ones in the database — answers
  with exactly one of `INCOMPLETE` (gray), `STAGED` (green: exactly what the row needs, part of it
  unsaved), `SAVED` (blue: exactly what it needs, all of it written) or `CONFLICT` (red: more than
  the invoice allows, decided first). Every card is painted from it and nothing else: the tick, the
  chime and the «کامل» chip follow `SAVED`, and a second opinion about what "done" means no longer
  exists anywhere in the app. Saving turns green into blue from the server's own answer — the
  pending labels leave only when the row comes back — and scanning into a saved row, removing a
  label in hand, or clearing a column recomputes the state immediately.
- **A row that stops being marked keeps everything on it.** Nothing is deleted, rewritten or
  hidden: an unmarked row's registrations stay in `Logistics Serial Assignment`, are counted in the
  payload (`ineligible_rows`, `ineligible_registered`) and shown as a chip, are listed by
  `get_serial_assignments` and in the Desk list, and `delete_assignments` still removes them, because
  the refusal is about **adding** a label. Measured on the operator's own invoice: `IN-260025` row 3
  is unmarked and carries 18 registrations, and the endpoint answers with two served rows and one
  ineligible row reporting those 18 — while `get_serial_assignments` still returns all 33 of that
  invoice's assignments.
- **The serial toolbar is one button again.** A Sales Invoice carried a «سریال‌ها» dropdown holding
  «ثبت سریال» beside a read-only «جزئیات سریال‌ها» entry, so reaching the window the operator always
  wants took two clicks and a menu. The dropdown is gone and the button's own label is «سریال‌ها»:
  one left-click opens the station, through the same `open_serial_dialog` it always used — no second
  registration path, no copy of the dialog. Everything the button was gated on is unchanged: the
  Editor role, the settings switch, the acting user's company permissions and an uncancelled
  invoice, and the framework reuses a button by its label so a refresh cannot draw a second one.
  The details window it used to open is untouched and simply no longer has a toolbar entry — a
  Viewer therefore sees no button at all, which is the consequence of removing the entry that was
  the only one they could act on.
- **A scanning burst with no terminator files one label per scan and never glues two together.** A
  keyboard wedge configured without an Enter or a Tab produced one unbroken run of `input` events,
  and the second label was being appended to the first. The window now measures the run instead of
  sleeping through it: `WEDGE_MIN_LENGTH` characters within `WEDGE_BURST_MS` followed by
  `WEDGE_SETTLE_MS` of quiet is a scan, so two scans are two labels and a human typing is left
  alone. Measured at 1.0–1.6 ms per scan into one column against 2.8 ms before, and a label the
  scanner *did* end is still taken the instant it is ended.
- An Enter that ends an IME composition is not a scan. The guard had been written against
  `event.isComposing`, which jQuery does not copy into the event it hands a delegated handler, so
  it never fired and a half-composed Persian word was filed as a serial. It reads
  `event.originalEvent.isComposing` now, and `keyCode === 229` is the fallback where that is not
  reported at all.
- A save that is partly refused reports **one line per refused column**, naming its product and
  every label the refusal covers, instead of one line per label: a column is written in one request
  and refused whole, so twenty lines said one thing twenty times.
- `after_migrate` creates nothing beyond the DocTypes, the constraints, the roles and the settings
  single. A site upgraded from 0.2.0 has no leftover tables to clean by hand: the archive's tables
  belonged to the app and went when its DocTypes were dropped.
- The README states the order before a removal — export, then `bench backup --with-files`, then
  the removal — and the three limits of restoring a backup afterwards.

### Removed

- **The second write path, and everything that existed for it.** `confirm_import` was still
  registered and still answering, but nothing had prepared a session for it since the window
  stopped needing one — so it was a write surface reachable only by a caller who inserted a
  `Logistics Import Session` row itself (an Editor may, through the REST API), with a payload
  nothing validated the shape of. The endpoint, `_store_session`, `_load_payload`, the gateway's
  `create_session`/`read_session`/`read_session_payload`/`lock_session`, and
  `build_confirmation_plan`/`run_confirmation` are gone, and the endpoint and gateway lists, the
  `core.authz` tables and the tests that covered them were updated with them. What remains is one
  write path — `core.validation.plan_registration` behind `add_row_serial`, `add_row_serials` and
  `set_row_serials` — and the invariant that was about the removed one (I12) is marked retired
  rather than left claiming an enforcement nothing provides. The `Logistics Import Session` DocType
  and its rows are untouched, and the daily sweep still expires them.
- **«اجازهٔ ثبت بیش از تعداد فاکتور» (`allow_over_quantity`), and with it the setting.** The cap
  it switched off is the rule now: `core.validation` refuses a registration past a row's invoiced
  quantity for every caller, with nothing to turn it off (I20), and the settings page was
  regrouped into serial, scanning and parsing sections around what an operator actually changes.
  The field was a real column on `tabLogistics Settings`, so **`bench migrate` drops it** — one row,
  a flag nothing reads any more, and no document anywhere else is touched. `next_row_command` is
  the one field this release *adds* to that table.

- `before_uninstall`, and with it the whole preservation mechanism: `core/archive.py`,
  `setup/archive_store.py`, the three `sp_logistics_*` tables, `snapshot_serials_now`,
  `restore_archived_serials`, the two Manager actions in `core.authz`, `tests/test_archive.py`,
  and the fake runtime's SQL-recording surface that only those tests used.

### Notes

- **A cancelled invoice may be read and emptied, and may not be added to.** The window always had
  this rule and the server never did, so it was a property of the client: a registration through an
  API call, or from a window left open across a cancellation, went through. `core.authz` refuses
  `docstatus = 2` for the endpoints that write a registration and allows every read and every
  removal — the registrations a cancelled invoice holds were added when it was live, and somebody
  has to be able to see and remove them. The state is read from the stored document, never from a
  request, and re-checked **under the invoice lock**, so an invoice cancelled while the request
  waited is refused too.
- **A missing invoice and a forbidden one are the same answer everywhere.** The write endpoints
  checked the invoice only when the lookup had found one, so a name that does not exist answered
  `PLAN_INVOICE_CHANGED` (فاکتور یافت نشد) while an invoice in another company answered
  `AUTH_COMPANY_NOT_ALLOWED` — two answers where `core.authz` promises one, and a caller who can
  tell them apart has a way to find out which invoice names are real. `INVOICE_ENDPOINTS` names
  every endpoint that is about one invoice, and all of them check it either way; a static test
  reads the module and fails if an endpoint starts naming an invoice without being listed.
- **The archive preflight measures every part before it reads any of them.** Reading the
  content-type map to decide whether a `.bin` is printer settings put a whole-part decompression
  ahead of the very bounds meant to catch a bomb. Every part is now measured first — path, name,
  declared size, compression ratio — and the two extra reads (the content-type map, and eight bytes
  of a claimed printer-settings part) happen after that, which also means a package that fails any
  bound is refused without being opened. Both reads take the `ZipInfo` they were handed rather than
  the part's name: a ZIP may hold two members under one name, and opening by name answers with the
  same bytes however many times it is asked, so an OLE container hidden behind a name that also
  holds a benign part used to go unlooked-at. There is a test for each of those, and the second one
  fails against the code as it was.
- **A legitimate XLSX with printer settings is no longer refused.** The archive preflight treated
  any `xl/**.bin` as an embedded OLE object, and Excel writes `xl/printerSettings/printerSettings*`
  `.bin` parts into a workbook as a matter of course — so a perfectly ordinary file was rejected
  with «فایل دارای شیء جاسازیشده است». The rule now reads the two things that decide it — the
  content-type map's own declaration of the part, and the part's own first bytes — and accepts a
  printer-settings part that is declared as one and is not an OLE container. Nothing else changed:
  `xl/embeddings/`, `xl/oleobjects/`, `xl/activex/`, every `.emf`/`.wmf`/`.xls`/`.doc`/`.ole` under
  `xl/`, the ZIP traversal checks, the decompression-bomb bounds and the file-size limits are
  exactly as they were, and a `.bin` is not whitelisted by its extension. The reader now touches
  `[Content_Types].xml` and eight bytes of one part it has already decided to accept, and the
  module says so where it says what it reads.
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

- **Restoring an export can no longer write to a cancelled invoice.** `apply_serial_restore` is the
  one write path that does not go through `core.validation`, and it was also the one that never
  asked whether the invoice was cancelled: a file naming an invoice whose `docstatus` is 2 had its
  registrations created on it, which is the state the app documents it never creates and every other
  path refuses. The world the planner is given now carries the cancelled invoices (read from the
  stored document, never from the file) and `core/export.plan` reports such a row as a conflict
  (`INVOICE_CANCELLED`) and leaves it alone — so a file that mixes a cancelled invoice with live
  ones restores what it can and names the rest, exactly as it does for any other conflict.
- **An invoice in another company is refused the way a name that matched nothing is.** The *message*
  was already the same; the **code** was not, and the code travels to the caller inside it
  (`AUTH_COMPANY_NOT_ALLOWED` against `AUTH_INVOICE_NOT_FOUND`). Invoice names are sequential and
  guessable, so the difference was a way to learn which names are real. One sentence, one code,
  written once in `core.authz`.
- **The refusal Frappe renders is escaped.** `frappe.msgprint` treats what it is given as HTML, and
  the sentence it was given quotes the serial the server refused — a label pasted from a file is
  data, and a label that reads `<img …>` must appear as those characters.
- **One status read is bounded.** It loaded every registration of the invoice; it now loads at most
  `core.limits.MAX_INVOICE_ASSIGNMENTS` (5,000) and **refuses** rather than drawing a page that was
  cut short, because every count in that payload is a count of what was read.
- **A paste lands in the column it was pasted into.** Clicking the serial input of invoice row 2 and
  pasting sent every serial to invoice row 1: the paste handler read the field's own row key for its
  flash message and then handed the text to `intake` with the invoice's **first** row as the starting
  row, so the field's row was never the target and a list carrying no command had nothing to correct
  it with. Every other way in already went where it was aimed — a scanned label, a typed label — and
  the paste was the one that did not. `intake` is now called with the row the paste arrived in, which
  is what clicking that input says; `*N` and the next-column command still move the cursor from
  there for everything that follows, so `["N-1", "#2", "N-2"]` pasted into row 3 leaves `N-1` in row
  3 and sends `N-2` to row 2. A **file** is unchanged and still begins at invoice row 1, because a
  picker in the footer is not aimed at a column; that is the only input for which the difference
  shows, and it is now stated where the rule lives.
- **An optional column that holds serials is green instead of gray.** `column_is_complete` is false
  for a row whose item is not marked `sp_serial_required`, and rightly so — nothing was required of
  it — but that was the *only* state the window had for such a row, so twenty registered serials on
  the operator's optional row drew it exactly as it draws an empty one. The state vocabulary was
  missing a member rather than the stylesheet missing a rule: `core.eligibility.column_is_filled` is
  the populated state (one label to the row's quantity, for an optional row only), it is served as
  `row.is_filled` beside `is_complete`, and the window draws it as a green header with **no tick and
  no chime**, keeping the «اختیاری» label. A row past its quantity is the excess state as before, so
  a column somebody has to fix never looks finished, and the window's own verdict includes the labels
  still unsaved, so a paste into an optional column turns it green at once.
- **A file being read now says so, and cannot leave the window stuck.** The read had no notice of its
  own — the toolbar chip is the *write* indicator — and a `frappe.call` that threw before returning a
  promise skipped the whole chain behind it, leaving the picker and the button disabled for as long
  as the dialog stayed open. «لطفاً منتظر بمانید؛ فایل در حال پردازش است…» now stands where the
  file's result will appear while the request is out, the picker and the button are disabled and a
  second press asks for nothing, and a `finally` puts both back after a success **and** after a
  failure — with the refusal's own Persian sentence and a line saying the read is over in place of
  the notice. The call is made inside a `then`, so a call that fails before it returns anything is a
  rejection like any other rather than a way past the `finally`.
- **A multi-column paste is read column by column, like the workbook it came from.** The paste path
  split the whole clipboard with the site's separators and handed the flat token list to the intake,
  so a copied range of cells was flattened across its rows: the commands in the first line — `*1`,
  `*2`, `*3` — were all consumed before a single serial arrived, the cursor ended on the last one,
  and every serial was staged on that row. The same range uploaded from the workbook was routed
  correctly, which is how one set of rules gave two answers for one input. `intake` now decodes each
  text first (`smartprocess_logistics.decode_intake_values`): a **table** — two or more lines
  carrying a tab — yields its nonempty cells **column by column, top to bottom**, in the order
  `core.spreadsheet.column_major_texts` reads a workbook, and a **list** yields the whole text,
  which the separators take apart exactly as before. A shape has to span rows to be a shape, so one
  line with a tab, or a column of cells with newlines only, keeps the sequential reading it had;
  commands still switch rows as often as the operator wrote them. This is a decode step and not a
  second parser: it interprets nothing, chooses no row, and drops no nonempty cell, so a cell that
  belongs to no command is read at the cursor like any other value rather than silently discarded.
  Verified on the operator's own `01.xlsx` and on the clipboard copy of the same range: the two
  stage the same 36 serials — one cell of a command-less column, five under `*1`, ten under `*2`,
  twenty under `*3` — on the same rows, in the same order, with the same validation result.
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
