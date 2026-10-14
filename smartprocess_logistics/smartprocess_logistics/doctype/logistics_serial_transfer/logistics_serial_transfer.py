"""Logistics Serial Transfer controller.

Empty on purpose. Frappe imports a DocType's controller by path and takes the class named after
the DocType with its spaces and dashes removed, so the module must exist and be importable; a
DocType whose module cannot be imported fails inside `sync_for`. What a transfer decides lives in
`core/transfer.py`, where a test can reach it without a site.
"""

from frappe.model.document import Document


class LogisticsSerialTransfer(Document):
	pass
