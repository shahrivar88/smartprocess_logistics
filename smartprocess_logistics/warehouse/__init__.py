"""Virtual warehouse classification and server-side guards.

This package reads the standard `Warehouse Type`, applies the pure rules in `policy.py`, and
refuses a sale, delivery, reservation or non-transfer movement that would touch a virtual
warehouse. It cannot write GL, SLE or Bin rows; stock movement remains the standard ERPNext
controller's job.
"""
