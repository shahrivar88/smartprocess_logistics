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
|      |       |      |         |      |         |            |            |

## Environment

The only site this app is tested against is `SmartProcessERP`, reached on port 8080. That
environment holds test data, not production data, but it is still not written to without
explicit approval for the specific step.

The site on port 8081 and its database are out of scope for this project.
