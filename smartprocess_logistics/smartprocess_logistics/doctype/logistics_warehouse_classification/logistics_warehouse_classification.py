"""Logistics Warehouse Classification controller.

Empty on purpose. A child table still needs an importable module and a class named after the
DocType, so the row can be created and saved; it decides nothing. The rules that read and check
a stored mapping live in `warehouse/policy.py`, which imports no Frappe, and the hook that
applies them lives in `warehouse/classification_settings.py`.
"""

from frappe.model.document import Document


class LogisticsWarehouseClassification(Document):
	pass
