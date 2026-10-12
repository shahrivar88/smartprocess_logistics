"""The Frappe-facing layer: whitelisted endpoints and the gateway they drive.

**Inactive.** The app is not installed on any site, so nothing in this package is reachable.
It exists so the endpoints can be reviewed before they are switched on, and so that everything
deciding *what* happens lives in `core`, where it is tested.

The split:

- `gateway` owns every database call. It is the only module in the app that touches the ORM.
- `endpoints` parses arguments, builds a `Principal`, asks `core.authz` whether the request is
  allowed, asks `core` what to do, and hands the plan to the gateway. An endpoint should never
  contain a decision, only wiring.

No endpoint uses `ignore_permissions`, and none of them calls `commit` or `rollback`.
"""
