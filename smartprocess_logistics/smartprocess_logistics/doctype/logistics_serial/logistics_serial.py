"""Logistics Serial controller.

Frappe resolves a DocType's controller by importing this module at the path
`<app>.<module>.doctype.<folder>.<folder>` and taking the class named after the DocType with its
spaces and dashes removed. The module has to exist and be importable for that to succeed, which
is why this file is load-bearing even though it decides nothing: a DocType whose module cannot
be imported fails inside `sync_for`, which is how the first installation attempt stopped.

Every rule this app has lives in `core/`, where it can be reached by a test that needs no site.
A decision made here could not be.
"""

from frappe.model.document import Document


class LogisticsSerial(Document):
	pass
