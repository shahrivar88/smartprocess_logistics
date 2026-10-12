# Permissions and company isolation

Enforcement is **server-side**. A hidden button, a filtered list view, or a disabled field is
presentation, not authorization.

## How a permission decision is actually made

| Layer | Doctype / hook | Decides |
| ----- | -------------- | ------- |
| Role permission | `DocPerm` (child table on `DocType`) | Which role may read, write, create, submit, cancel, delete |
| Row-level restriction | `User Permission` | Which values of a link field a user may see at all |
| Share | `DocShare` | A single document shared with a user or a role |
| Query filter | `permission_query_conditions` hook | Which rows appear in a list or report |
| Single-row check | `has_permission` hook | Whether one specific document is allowed |
| Website | `has_website_permission` hook | The portal equivalent of `has_permission` |

`permission_query_conditions`, `has_permission` and `has_website_permission` are all confirmed
present in 16.35.0. See `references/v16-framework.md`.

`User Permission` restricts by the value of a link field. When the link field is `company`,
that is how a user is confined to a company. This is the mechanism — not a hand-written
`WHERE company IN (...)` that somebody can forget.

## Company isolation for this project

- Apply the restriction **in the query**, from the principal's own company list. Do not filter
  in Python after fetching, and do not fetch and then hide.
- The framework's own read conditions are **required**, never optional. An argument that
  defaults to "no conditions" is one somebody forgets, and forgetting it produces a report
  that ignores permissions.
- `core/authz.py` is where this project decides. It stays server-side and it is tested. A
  decision that lands in `api/` instead is a decision no test can reach.

## ignore_permissions

Do not use it. Not in an endpoint, not in a background job, not "just this once". A missing
permission is a design problem: either the role is wrong, or the operation genuinely needs an
elevated context that should be named and justified. `frappe.set_user` is not a substitute —
it changes the principal, and hiding that change is worse than the original problem.

The generic `frappe-app-dev` skill documents the mechanism, including when Frappe itself uses
it internally. This project's rule is stricter than the framework's capability.

## Writing the query filter

- Every caller-supplied value becomes a named parameter. Never interpolate.
- An `IN` list is expanded into one placeholder per value — do not depend on the driver's own
  tuple formatting.
- Filter by `company` from the principal's company list, and pass the framework's invoice read
  conditions through rather than reimplementing them.
- Bound the result. An unbounded scan of a transaction table is a correctness problem as well
  as a performance one.
