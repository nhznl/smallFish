"""Shared CSV schema for user-authored sold/tracking stock lists.

The FastAPI backend owns behaviour in ``stock-app/app/tracked_stocks.py``.
The column layout lives here so batch tooling and the API share one contract
without importing each other across Python environments.
"""

from __future__ import annotations

CATEGORY_SOLD_STOCK = "Sold Stock"
CATEGORY_TRACKING = "Tracking"
CATEGORY_READY_TO_TRADE = "Ready to Trade"
TRACKED_STOCK_CATEGORIES = (
    CATEGORY_SOLD_STOCK,
    CATEGORY_TRACKING,
    CATEGORY_READY_TO_TRADE,
)

#: ``tracked_stocks.csv`` — one row per symbol the user is monitoring,
#: including names a brokerage sync moved here after a full equity close.
TRACKED_STOCK_HEADERS = [
    "symbol",
    "category",
    "coverage_initiation_date",
    "notes",
    "target_date",
    "target_amount",
    "created_at",
]
