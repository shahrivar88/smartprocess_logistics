"""Byte counts, written the way an operator can read them back.

The ceilings are set in kilobytes on `Logistics Settings` and held in bytes in the readers. A
message that renders a byte ceiling with integer division by a megabyte turns a 1 KB cap into
"0 megabytes", which is how a refusal came to tell an operator that the file was larger than
nothing. The unit is chosen from the value, so the number in the message is always the number
the operator typed.
"""

KILOBYTE = 1024
MEGABYTE = 1024 * KILOBYTE


def describe_bytes(value):
	"""A byte count in the largest unit that leaves it readable."""
	if value >= MEGABYTE:
		return f"{value / MEGABYTE:g} مگابایت"

	if value >= KILOBYTE:
		return f"{value / KILOBYTE:g} کیلوبایت"

	return f"{value} بایت"
