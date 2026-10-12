# Reports, Desk UI, and Persian RTL

## Where a report lives

Verified layout in 16.35.0, using `general_ledger` as the reference:

```
erpnext/accounts/report/general_ledger/
    general_ledger.json    the Report document — type, ref_doctype, filters, roles
    general_ledger.py      the columns() and data() functions
    general_ledger.js      the filter form and client-side behaviour
    general_ledger.html    optional, for a custom print layout
```

A **Script Report** is a folder like that. A **Query Report** is SQL in the Report document
itself — no Python file. Prefer the Script Report when the query needs a parameter, a
permission condition, or any logic at all, because the Query Report's SQL cannot take the
framework's read conditions through cleanly.

## What this project requires of a report

The two reports planned here — Sales Serial Register and Missing Sales Serials — already set
the precedent. Keep to it:

- **One statement.** The registered count comes from a grouped subquery joined in, not from a
  query per row. A report that runs a query inside a loop is a report that will time out on
  real data.
- **Every caller value is a named parameter.** An `IN` list is expanded into one placeholder
  per value rather than relying on the driver to format a tuple.
- **Company isolation lives in the query**, from the principal's own company list.
- **The framework's read conditions are required, not optional.** An argument that defaults to
  no conditions is one somebody forgets.
- **No silent default window.** The missing-serials report has no default date window on
  purpose: a report that quietly covers ninety days answers a question nobody asked and hides
  the rows outside it. When it cannot answer, it refuses with a code rather than returning an
  empty table.
- Declare the report's roles in its `.json`. A report is a permission surface.

## Desk scripts

- Client Script and the doctype `.js` file are for interaction, not for authorization. A rule
  that matters belongs on the server.
- Do not push `frappe-ui` components, design tokens or `createResource` into a traditional
  Desk form script, a `*.js` form script, a print format or a Jinja portal page. `frappe-ui`
  applies to modern Vue single-page frontends only.
- Prefer `frm.set_query`, `frm.add_fetch` and the documented form events over reaching into
  the DOM.

## Persian and RTL

**The direction rule, verified in 16.35.0** — `frappe/public/js/frappe/utils/utils.js`:

```js
is_rtl(lang = null) {
    return ["ar", "he", "fa", "ps"].includes(lang || frappe.boot.lang);
}
```

So Persian is RTL to the framework, and the direction follows the active language. Do not
hard-code `direction: rtl` in a stylesheet; follow the framework so a site running in English
still lays out correctly.

What this means in practice:

- **Use logical CSS properties** — `margin-inline-start`, `padding-inline-end`,
  `inset-inline-start` — instead of `left` / `right`. A physical property is a bug that only
  shows up in one of the two directions.
- **Do not reverse a layout by hand.** Direction is applied by the framework from the language;
  a manual flip produces a double flip.
- **Numbers and identifiers** in a Persian screen stay in their original form. Do not
  transliterate a DocType name, a field name, a serial number or an API route into Persian
  digits — they are identity, not prose.
- **Menu labels** follow the existing convention: the Persian title first, the English ERPNext
  name in parentheses, for example «تنظیمات فرم» (Customize Form).

## Reuse the Persian infrastructure that is already installed

Two apps in this stack already own Persian concerns. Do not rebuild either:

- `jalali_shamsi_datepicker` — the Jalali calendar and date input.
- `smartprocee_erpnext_persiant` — Persian translation and locale wiring.

Before writing any Persian-facing code, check what those two already do. The reuse ladder in
`smartprocess-erpnext` applies here exactly as it does anywhere else.

## Testing a Persian screen

A browser test for an RTL screen has to check the direction, not just the text: an element
that reads correctly but sits on the wrong side is the failure this catches. See the browser
testing rules in the project instructions — isolated test data, explicit approval for any test
that writes to the database, and no destructive automation against existing records.
