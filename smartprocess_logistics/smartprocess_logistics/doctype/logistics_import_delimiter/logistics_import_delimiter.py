"""Logistics Import Delimiter controller.

Empty on purpose, and a child table is no different in v16: Frappe imports a DocType's controller
by path and takes the class named after the DocType with its spaces and dashes removed, so the
module must exist and be importable. A DocType whose module cannot be imported fails inside
`sync_for`. This is the DocType that stopped the first installation attempt, because it was the
first one `sync_for` reached. How a stored delimiter is interpreted lives in `core/delimiters.py`.
"""

from frappe.model.document import Document


class LogisticsImportDelimiter(Document):
	pass
