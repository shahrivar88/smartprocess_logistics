# Frappe / ERPNext v16 — facts verified against the installed source

Verified on the `erpnext-backend-1` container, image
`smartprocess/erpnext:v16.35.0-official-apps.1`:

- `frappe.__version__` = `"16.35.0"`
- `erpnext.__version__` = `"16.35.0"`

Anything not listed here has **not** been verified for this version. Read the source before
relying on it.

## Lifecycle hooks the framework actually calls

Grepped from the call sites in `frappe/model/document.py`:

```
before_insert   validate   before_submit   on_submit
before_cancel   on_cancel  before_update_after_submit   on_update_after_submit
```

`on_update_after_submit` and `before_update_after_submit` are real. The generic skills
sometimes omit them.

## DocStatus as an object

`frappe/model/docstatus.py` defines `DocStatus(int)` with:

```
doc.docstatus.is_draft()      doc.docstatus.is_submitted()      doc.docstatus.is_cancelled()
```

The `DocStatus.draft()` / `.submitted()` / `.cancelled()` static methods are kept for
backwards compatibility only. Prefer the predicate methods in new code.

## Hook names that exist in 16.35.0

Confirmed present in the framework source:

```
before_install  after_install  after_app_install  after_app_uninstall
before_uninstall  after_uninstall
before_migrate  after_migrate
doc_events  scheduler_events  jinja  fixtures  doctype_js  page_js
permission_query_conditions  has_permission  has_website_permission
override_whitelisted_methods  extend_bootinfo  user_data_fields
website_route_rules  app_include_js  app_include_css
```

`override_doctype_class` exists. So does `extend_doctype_class`, which is the less invasive
of the two.

## extend_doctype_class — prefer it over a whole-class override

`frappe/model/base_document.py` builds the controller like this:

```python
extensions = frappe.get_hooks("extend_doctype_class", {}).get(doctype)
```

Each entry is a dotted path to a class. The extension classes are mixed into the base document
class, in reverse order of the list. The project rule forbids replacing a whole DocType class,
so when standard behaviour must be added to an existing DocType, `extend_doctype_class` is the
mechanism to reach for first — and `doc_events` before that.

Before adding either, search the installed apps for an existing hook on the same target and
name the owner of that behaviour. See section 16 of `smartprocess-erpnext`.

## Where the source lives

Application code, read-only:

```
/home/frappe/frappe-bench/apps/frappe/frappe/
/home/frappe/frappe-bench/apps/erpnext/erpnext/
```

Reach it with the existing stack only:

```
docker compose exec backend sh -lc "grep -rn ... /home/frappe/frappe-bench/apps/erpnext/erpnext/accounts/"
```

No new container, image or bench. `bench` does not exist on the Windows host.
