"""Install-time setup helpers for smartprocess_logistics.

Three concerns, one module each:

- `roles`: the three roles this app grants, and the function that creates them.
- `constraints`: the composite unique constraints that a DocType JSON cannot declare.
- `seeds`: the values written once, at install time.

Nothing in this package is wired into `hooks.py` yet. `install.py` calls these helpers, and
`install.py` itself is not registered, so installing this app currently does nothing.
"""
