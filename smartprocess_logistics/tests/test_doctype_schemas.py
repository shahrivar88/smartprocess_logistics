"""The DocType definitions must satisfy Frappe's own rules before anything is installed.

This test reads JSON files from disk and derives from `UnitTestCase`, which neither seeds
records nor rolls anything back, so it writes nothing and needs no test data.
"""

from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.tests.schema_checks import check_all


class TestDocTypeSchemas(UnitTestCase):
	def test_every_doctype_definition_is_valid(self):
		problems = check_all()

		self.assertEqual(problems, [], msg="\n".join(problems))
