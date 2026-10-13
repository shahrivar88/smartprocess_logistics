"""Logistics Serial Assignment controller.

Empty on purpose. Frappe imports a DocType's controller by path and takes the class named after
the DocType with its spaces and dashes removed, so the module must exist and be importable; a
DocType whose module cannot be imported fails inside `sync_for`. The rules this app applies live
in `core/`, where a test can reach them without a site.
"""

from frappe.model.document import Document


class LogisticsSerialAssignment(Document):
	pass
