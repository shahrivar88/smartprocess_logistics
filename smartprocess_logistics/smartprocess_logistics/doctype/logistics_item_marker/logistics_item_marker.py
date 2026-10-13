"""Logistics Item Marker controller.

Empty on purpose, and a child table is no different in v16: Frappe imports a DocType's controller
by path and takes the class named after the DocType with its spaces and dashes removed, so the
module must exist and be importable. A DocType whose module cannot be imported fails inside
`sync_for`. Which invoice row a `{n}` marker names lives in `core/markers.py`.
"""

from frappe.model.document import Document


class LogisticsItemMarker(Document):
	pass
