"""Pure business logic for smartprocess_logistics.

Everything in this package is deterministic and self-contained: no Frappe import, no
database, no filesystem, no clock, no network. That is what makes the registration rules
testable before the app is installed anywhere, and what keeps them testable afterwards on a
machine that has no bench.

The modules and what each one decides:

- `normalization`: the text an operator typed, turned into the single form that is the
  serial's identity.
- `delimiters`: how a configured delimiter is interpreted, and how a file is split into
  tokens.
- `markers`: the `{n}` item markers that say which invoice row a group of serials belongs to.
- `quantities`: how many labels a row needs, and how many it is short of or over.
- `eligibility`: which invoices the missing-serial report may cover.
- `validation`: everything above combined into one registration plan, plus the checks that
  keep a configuration from being ambiguous.
- `limits`: which of an operator's ceilings apply to a payload, and the rule that one may only
  tighten the ceilings the readers were built against.

Errors are raised as exceptions carrying a stable machine-readable `code` beside a message,
so a caller can translate or group them without matching on prose.
"""
