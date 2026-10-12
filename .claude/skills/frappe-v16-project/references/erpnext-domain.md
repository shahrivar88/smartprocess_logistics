# ERPNext v16 domain semantics

The generic skills carry no ERPNext domain knowledge. This file carries the minimum needed to
avoid designing a parallel system. **The source is the authority for every field; the field
lists below were read from the DocType JSON in 16.35.0.**

Model a requirement with existing ERPNext concepts. Before creating a DocType, confirm no
standard DocType can represent it — including through a Custom Field or Customize Form.

## Sales

Documents: `Quotation` → `Sales Order` → `Delivery Note` → `Sales Invoice`, plus
`Sales Invoice Item` as the child row.

- A **submitted** `Sales Invoice` posts to the ledger. It does not post stock; the
  `Delivery Note` owns that.
- A serial number the app records on a `Sales Invoice Item` row is **not** ERPNext's own stock
  serial. This project never turns on `Item.has_serial_no` and never writes a `Serial No` or
  `Serial and Batch Bundle` record. Keeping those two ideas separate is the whole point of
  the app.
- Cancelling an invoice does not delete the ledger rows; it marks them.

## Stock

The single source of truth for quantity and valuation is `Stock Ledger Entry` (SLE). It is
append-only in spirit: you do not edit history, you post a correcting entry.

Verified SLE fields include:

```
item_code   warehouse   posting_date   voucher_type   voucher_no
actual_qty   qty_after_transaction   valuation_rate   stock_value   stock_value_difference
stock_queue   serial_and_batch_bundle   company   is_cancelled
```

`Bin` holds the running balance per item **and** warehouse, for read performance:

```
warehouse   item_code   actual_qty   ordered_qty   reserved_qty   projected_qty
valuation_rate   stock_value   company
```

Never write either table directly. A report that needs availability reads them; it never
mutates them to compute a number.

## Accounting

Three ledgers, three jobs:

| Doctype | What it is |
| ------- | ---------- |
| `GL Entry` | The general ledger. One row per account movement. |
| `Payment Ledger Entry` | The party-wise receivable / payable subledger. |
| `Journal Entry` | A manual voucher that produces `GL Entry` rows. |

Verified `GL Entry` fields include:

```
posting_date   account   party_type   party   cost_center   company   account_currency
debit   credit   debit_in_account_currency   credit_in_account_currency
voucher_type   voucher_no   against_voucher_type   against_voucher
is_opening   is_cancelled   fiscal_year   finance_book
```

Verified `Payment Ledger Entry` fields include:

```
posting_date   account_type   party_type   party   company   cost_center
voucher_type   voucher_no   against_voucher_type   against_voucher_no
amount   account_currency   amount_in_account_currency   delinked
```

**Watch this one.** `Payment Ledger Entry` uses `against_voucher_no`. `GL Entry` uses
`against_voucher`. They are not the same column name, and a query copied from one to the
other fails or silently returns nothing.

There is no `is_cancelled` field on `Payment Ledger Entry` in 16.35.0 — do not filter on one.

Multi-currency has four separate things to get right, and they are separate on purpose:
transaction currency, account currency, company currency, and the exchange rate used.

## The rule that outranks all of the above

Never manufacture a `GL Entry`, a `Payment Ledger Entry`, an SLE or a `Bin` value by hand as a
shortcut. Identify which standard controller produces the entry, and reuse its posting logic.
If a solution requires disabling a standard accounting validation, prove first why the
requirement conflicts with it, and limit the override to the smallest possible scope.

## Before writing custom logic, read the standard logic

```
docker compose exec backend sh -lc "grep -rn 'def on_submit' /home/frappe/frappe-bench/apps/erpnext/erpnext/accounts/doctype/sales_invoice/"
```

Read how standard ERPNext already posts it. Then extend, do not duplicate.
