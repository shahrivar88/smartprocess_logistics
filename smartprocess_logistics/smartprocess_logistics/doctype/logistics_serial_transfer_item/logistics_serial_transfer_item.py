"""Logistics Serial Transfer Item controller.

Empty on purpose, and a child table is no different in v16: Frappe imports a DocType's controller
by path and takes the class named after the DocType with its spaces and dashes removed, so the
module must exist and be importable. A DocType whose module cannot be imported fails inside
`sync_for`. Which serial goes where is decided in `core/transfer.py`.
"""

from frappe.model.document import Document


class LogisticsSerialTransferItem(Document):
	pass
