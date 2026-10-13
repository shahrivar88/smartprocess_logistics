# Test manifest

Every record this app creates while testing is listed here, so cleanup is exact rather than a
guess. Nothing in this file is created automatically.

Rules:

1. Add a row **before** creating the record, so an interrupted run still leaves a trace.
2. Names carry the `SPT-` prefix, which marks a record as test data at a glance.
3. Delete in reverse order of creation. A child row cannot be deleted before its parent, and
   an assignment cannot be deleted before the invoice it points at.
4. Never delete a record this file does not list.
5. Delete only what this app owns. Serial and assignment records are owned by the app;
   Company, Customer, Item and Sales Invoice records belong to the site.

| Date | Phase | Site | DocType | Name | Purpose | Created by | Deleted on |
| ---- | ----- | ---- | ------- | ---- | ------- | ---------- | ---------- |
| 2026-10-13 | 4 | SmartProcessERP | Item | `SP-TEST-WARRANTY-ITEM` | A non-stock item carrying `sp_serial_required`, so the report and the button have something eligible | main agent | **kept** |
| 2026-10-13 | 4 | SmartProcessERP | Sales Invoice | `IN-260017` | One submitted invoice with three required serials | main agent | **kept** |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Serial | `LS-2026-00001` … `LS-2026-00016` | Serial identities created by the registration, correction, TXT and XLSX tests | main agent | 2026-10-13 |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Serial | `LS-2026-00017` | The one serial the kept invoice still points at | main agent | **kept** |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Serial Assignment | `LSA-2026-00007` … `LSA-2026-00009` | Assignments on `IN-260017`; the extras were replaced by the correction tests, leaving one | main agent | partly |
| 2026-10-13 | 4 | SmartProcessERP | Logistics Import Session | `8b38ev48hm`, `8bto91828p`, `a90mqv2f0b`, `b8btb303on`, `gt6tctqkmh` | One row per preview of a TXT or XLSX payload | main agent | 2026-10-13 |
| 2026-10-13 | 4 | SmartProcessERP | User | `sp_ui_viewer@` / `sp_ui_editor@` / `sp_ui_manager@` / `sp_ui_none@example.com` | The four principals of the permission matrix; each holds `Accounts User` so the report's own ref-doctype check can be reached | main agent | 2026-10-13 |

| 2026-10-13 | 5 | SmartProcessERP | Sales Invoice | `IN-260018` | A two-row invoice: one row of two, one of one, for the duplicate and partial-import cases | main agent | **kept, cancelled** |
| 2026-10-13 | 5 | SmartProcessERP | Sales Invoice | `IN-260019` | A one-row invoice of three, for the concurrency and rollback cases | main agent | **kept, cancelled** |
| 2026-10-13 | 5 | SmartProcessERP | Logistics Serial | the serials created by the phase-5 cases, `LS-2026-00020` … `LS-2026-00030` among them | One row per serial the tests registered | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | Logistics Import Session | one row per preview, including two raced at once | Preview rows, all applied or expired | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | User | `sp_p5_probe@example.com` | One principal for the live permission matrix, with a Company user permission | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | File | `sp-phase5-xlsx-test.xlsx` in the site's `public/files` | A workbook the browser could fetch, to test the XLSX path through the real dialog | main agent | 2026-10-13 |
| 2026-10-13 | 5 | SmartProcessERP | Sales Invoice | `IN-260020` | A one-row invoice of two, for the typed-list check through the dialog itself | main agent | **kept, cancelled** |

| 2026-10-13 | 6 | SmartProcessERP | Company | `SP TEST COMPANY` | A second company, created with `create_chart_of_accounts_based_on = Existing Company`, to prove company isolation with two real companies | main agent | **kept** |
| 2026-10-13 | 6 | SmartProcessERP | Sales Invoice | `IN-260021` (draft) | One draft invoice in the second company, with two serials registered by an Editor who could reach only that company | main agent | **kept, draft** |
| 2026-10-13 | 6 | SmartProcessERP | Logistics Serial | the two serials the company-B draft points at | Created by that Editor's confirmed import | main agent | **kept** |
| 2026-10-13 | 6 | SmartProcessERP | User | `sp_p6_company_b@example.com`, `sp_p6_final@example.com` | Editors with a Company user permission for the second company only: one for the isolation matrix, one for the create-only session check | main agent | 2026-10-13 |
| 2026-10-13 | 6 | SmartProcessERP | Logistics Import Session | previews made while testing the sweep, all applied or expired | The cleanup job then expired and removed them | main agent | 2026-10-13 |
| 2026-10-13 | 6 | SmartProcessERP | File | `smartprocess_logistics-uninstall-<timestamp>.json` in `private/files` | Written by calling `install.before_uninstall` on live data, to check the guard without uninstalling | main agent | 2026-10-13 |

| 2026-10-13 | 7 | SmartProcessERP | Sales Invoice | `IN-260021` (draft) | The company-B draft, and the two assignments and serials that pointed at it | main agent | 2026-10-13 |
| 2026-10-13 | 7 | SmartProcessERP | Logistics Serial | five serials nothing pointed at any more | Released by the replacements the phase-6 tests made | main agent | 2026-10-13 |

Two notes on this run.

The rows were written **after** the records were created, which breaks rule 1 and is recorded
here rather than hidden. The names are exact, so cleanup is still a list and not a search.

A user cannot be deleted while anything links to it, and on this site four things do: a
`Raven User` row (created by Raven when the user is created), a `Contact` (created by ERPNext
for the same reason), and the `Has Role` children the app itself added. The `Contact` and the
`Raven User` row had to be deleted first, in that order. The one item, one invoice and one
assignment are **kept on purpose**: they are what makes the Desk button, the dialog and the
report demonstrable on an otherwise empty module, and they are named so that they read as test
data. Remove them in reverse order when they are no longer wanted: the invoice cannot be
deleted while it is submitted, so cancel it first.

`IN-260018` and `IN-260019` were **cancelled rather than deleted**. Cancelling reverses their
accounting entries, so they now affect no balance and no report; deleting them would mean
removing the `GL Entry` rows the framework refuses to delete through, which is a write to
accounting data this app does not own and should not make on a site it is only tested against.
They can be deleted from the Desk by anyone who decides otherwise. `IN-260020` is cancelled for
the same reason, and `IN-260021` is a **draft**, so it has no accounting entries at all.

`SP TEST COMPANY` is left in place on purpose. It exists for one check — that a user restricted
to it cannot reach another company's invoice through any endpoint, while the report shows only
its own rows — and removing a company means removing its chart of accounts and every link to it,
which is a larger operation than the check it would clean up. Delete it from the Desk when it is
no longer wanted; it holds one draft invoice and no transactions.

## Environment

The only site this app is tested against is `SmartProcessERP`, reached on port 8080. That
environment holds test data, not production data, but it is still not written to without
explicit approval for the specific step.

The stack on port 8081 is out of scope for this project: its containers, its Compose invocation
and its apps are not to be touched, and none of them was.

It is **not** a second environment, and this was measured rather than assumed. Both stacks report
the same `sites` directory (the same inode) and the same `frappe.conf.db_name`
(`_939af57aabbacf4d`) over the same `db` host, and both see the same rows — nine Sales Invoices
and the same two companies, including every record this project created. The 8081 backend has no
`smartprocess_logistics` in its app list, so it serves the shared data without this app's code
present, and it answers 200.

So "out of scope" means: do not restart it, do not change it, do not install anything into it. It
does not mean the data differs. Every write this project made was authorized for the disposable
test site and is visible through both front doors; an uninstall or a migration ordered against
one of them is ordered against the other.

## The isolation lab

From 0.2.0 the removal-and-reinstall rehearsal runs in a stack of its own,
`deployment/lab/compose.isolation-lab.yaml`: Compose project `sp-logistics-isolation-lab`, its own
network, its own five volumes (`sp_logistics_lab_db`, `_sites`, `_logs`, `_redis_cache`,
`_redis_queue`), its own site `sp_logistics_lab`, and one published port on loopback (8095). It
shares **nothing** with the `erpnext` project — measured, not assumed: its MariaDB reports a
different hostname and holds only its own database, and no volume of one project is mounted into
the other.

Records the rehearsal creates, all inside that stack and all removed by
`docker compose … down -v`:

| Record | Purpose |
| ------ | ------- |
| `Company` `SP LAB COMPANY` | a company for the invoices to belong to |
| `Item` `SP-LAB-WARRANTY-ITEM` | a non-stock item carrying `sp_serial_required` |
| `Customer` `SP LAB CUSTOMER` | the invoices' customer |
| two `Sales Invoice` documents with 3 and 2 serial rows | realistic rows for the serials to be registered on, through the app's own endpoint |
| `Logistics Serial` — `LAB-W-0001` … `LAB-W-0005` | the serials those invoices carry, and the identities the export has to keep |
| the export document | written by the `export` phase into `<site>/private/files/lab-serial-export-*.json`; it is the copy the cycle is about |
| `<site>/private/files/lab-cycle-<phase>-*.json` | one evidence file per phase |

The rehearsal's phases are `seed`, `export`, `after-removal` and `after-reinstall`; the removal and
the reinstall between them are run by a person, because the workspace's own safety hook refuses
those two commands to the agent. `deployment/lab/README.md` holds them.

No row of this list belongs to `SmartProcessERP`, and none of it is written to a site that
matters. `verify_isolation_cycle.py` refuses to run unless both the site name and the database
name say it is the lab.
