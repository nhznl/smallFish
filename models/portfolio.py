"""Shared CSV schema for user-authored portfolios.

The FastAPI backend owns portfolio *behaviour* — creation, validation, returns,
and the SPY comparison — in ``stock-app/app/portfolios.py``. The batch side only
needs to seed an example portfolio during ``bootstrap-data``, and the two run in
separate Python environments that must not import each other.

So the column layout lives here, in the one package both may depend on. Without
it each side would carry its own copy of the headers, and a column added on one
side would silently produce files the other cannot read.

Standard library only, like everything in ``models/``.
"""

from __future__ import annotations

#: ``portfolios.csv`` — one row per portfolio.
PORTFOLIO_HEADERS = [
    "id", "name", "description", "sector", "industry", "created_date", "created_at",
]

#: ``portfolio_members.csv`` — one row per symbol in a portfolio.
#:
#: ``price_at_add`` is the close on the date the symbol joined. Returns are
#: computed against it, so a member written without a correct price silently
#: distorts the portfolio's performance rather than failing loudly.
MEMBER_HEADERS = ["portfolio_id", "symbol", "added_date", "price_at_add"]
