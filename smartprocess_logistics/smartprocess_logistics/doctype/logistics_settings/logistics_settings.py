"""Logistics Settings controller.

Empty on purpose, and a Single is no different: Frappe imports a DocType's controller by path and
takes the class named after the DocType with its spaces and dashes removed, so the module must
exist and be importable. The values this Single carries are seeded and interpreted in `setup/`
and `core/`, where a test can reach them without a site — and `tests/test_doctype_controllers.py`
holds this file to that, which is why the next-column command in it is checked where it is served
(`smartprocess_logistics.api.endpoints._scan_command`) rather than in a hook here.
"""

from frappe.model.document import Document


class LogisticsSettings(Document):
	pass
