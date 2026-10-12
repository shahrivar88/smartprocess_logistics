"""Install-time setup helpers for smartprocess_logistics.

Three concerns, one module each:

- `roles`: the three roles this app grants, and the function that creates them.
- `constraints`: the composite unique constraints that a DocType JSON cannot declare.
- `seeds`: the values written once, at install time.

`install.py` is the only caller, and `hooks.py` registers its `after_install` and `after_migrate`
entry points. Nothing in this package reads a business document or touches a DocType this app
does not own.
"""
