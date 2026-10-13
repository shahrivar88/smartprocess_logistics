"""Logistics Import Session controller.

Empty on purpose. Frappe imports a DocType's controller by path and takes the class named after
the DocType with its spaces and dashes removed, so the module must exist and be importable; a
DocType whose module cannot be imported fails inside `sync_for`. The session's state machine and
its expiry rules live in `core/`, where a test can reach them without a site.
"""

from frappe.model.document import Document


class LogisticsImportSession(Document):
	pass
