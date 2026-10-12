"""The base class for this app's database-free tests.

`UnitTestCase` is Frappe's, and it only adds site setup that these tests do not use. When they
run on a machine with no bench, `frappe.tests` cannot be imported at all, so the import falls
back to `unittest.TestCase`, which is the class `UnitTestCase` itself extends. The two are
interchangeable for a test that touches no database, which is every test in this package.

This is the single place that decides, so a test module never carries the try/except and a
missing Frappe is never mistaken for a broken test.
"""

try:
	from frappe.tests import UnitTestCase
except ImportError:  # no bench on this machine; the pure tests do not need one
	from unittest import TestCase as UnitTestCase

__all__ = ["UnitTestCase"]
