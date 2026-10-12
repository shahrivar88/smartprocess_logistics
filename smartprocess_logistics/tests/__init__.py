"""Tests for smartprocess_logistics.

Test policy for this app:

- Tests never run against a production site. `IntegrationTestCase.setUpClass` calls
  `make_test_records` and then `frappe.db.commit()`, and the framework's test cleanup issues
  a final commit, so a run on a live site persists data.
- Test modules that live directly in this package derive from `UnitTestCase`, which neither
  seeds records nor rolls back, and they must not write. Anything that writes belongs in a
  doctype test module and only runs on a site authorized for it.
- Every record created during an authorized test run is listed in `docs/test-manifest.md` in
  the repository root, so cleanup is exact and reviewable.

`schema_checks` holds the pure validators, so the DocType definitions can also be checked
without a site:

	python -c "from smartprocess_logistics.tests.schema_checks import report; print(report())"

A fail-fast guard that refuses to run unless the connected site is an authorized test site is
added in a later phase, alongside the settings DocType it reads its allow-list from. The
pattern already used by `smartprocess_navatel` in `smartprocess_navatel/tests/guard.py` is the
model.
"""
