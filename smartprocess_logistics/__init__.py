"""Optional warranty serial numbers for Sales Invoice item rows.

The app records serial numbers against individual `Sales Invoice Item` rows. It is
deliberately independent of ERPNext's own stock serial numbers: it does not enable
`Item.has_serial_no`, it does not create or edit `Serial No` or `Serial and Batch Bundle`
records, and it never posts stock or accounting entries.
"""

__version__ = "0.7.0"
