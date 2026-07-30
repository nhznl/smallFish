"""Capability reporting for optional integrations and core data readiness.

Backs ``GET /capabilities``. The UI uses it to tell four situations apart that
all otherwise look like an empty table:

- the provider is not configured at all;
- it is configured but no data has been synced yet;
- it is configured and something went wrong;
- core market data has not been bootstrapped.

Readiness is never inferred from a ledger being empty. An empty configured
account and an unconfigured provider are different states, and conflating them
produces the dead-end instruction this endpoint exists to remove.

This module reports only presence and shape. It never returns a secret, an
account identifier, or any part of a credential, and it makes no provider call:
the response must be safe to log and to put in a screenshot.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import date

from . import config

# Capability states, shared with tools/brokerages.py.
NOT_CONFIGURED = "NOT_CONFIGURED"
INCOMPLETE = "INCOMPLETE"
CONFIGURED = "CONFIGURED"
NEEDS_REGISTRATION = "NEEDS_REGISTRATION"
READY = "READY"
ERROR = "ERROR"

SETUP_DOCS = "docs/BROKERAGES.md"
DATA_DOCS = "docs/DATA.md"


@dataclass
class Capability:
    """One optional feature's availability, as the UI needs to render it."""

    id: str
    label: str
    #: What the user loses while this is unavailable.
    provides: str
    state: str
    available: bool
    #: Human-readable explanation, safe to display verbatim.
    reason: str
    #: The exact command or action that advances the state, if any.
    action: str = ""
    #: Provider name, for "who am I trusting with this" questions.
    provider: str = ""
    docs: str = SETUP_DOCS
    #: Settings this capability needs, with presence only — never values.
    requires: dict[str, bool] = field(default_factory=dict)


def _setting(name: str) -> str:
    return os.environ.get(name, "").strip()


def _present(*names: str) -> dict[str, bool]:
    return {name: bool(_setting(name)) for name in names}


# ------------------------------------------------------------- core data

def core_data_capability() -> Capability:
    """Whether the price cache has been bootstrapped.

    Distinguishing "no data yet" from "no results" is what turns a blank table
    into an actionable first-run screen.
    """
    data_dir = config.data_dir()
    current_year = str(date.today().year)
    year_dir = data_dir / current_year
    symbol_count = len(list(year_dir.glob("*.txt"))) if year_dir.is_dir() else 0

    if symbol_count:
        return Capability(
            id="core-data", label="Market data", provides="stock and ETF price history",
            state=READY, available=True,
            reason=f"{symbol_count} symbols cached for {current_year}.",
            provider="Yahoo Finance", docs=DATA_DOCS)

    any_year = any(p.is_dir() for p in data_dir.glob("[12][0-9][0-9][0-9]")) \
        if data_dir.is_dir() else False
    if any_year:
        return Capability(
            id="core-data", label="Market data", provides="stock and ETF price history",
            state=CONFIGURED, available=True,
            reason=f"No {current_year} price data yet, only earlier years.",
            action="./commands.sh bootstrap-data",
            provider="Yahoo Finance", docs=DATA_DOCS)

    return Capability(
        id="core-data", label="Market data", provides="stock and ETF price history",
        state=NOT_CONFIGURED, available=False,
        reason="No price data has been downloaded yet.",
        action="./commands.sh bootstrap-data",
        provider="Yahoo Finance", docs=DATA_DOCS)


# ------------------------------------------------------------- providers

def finnhub_capability() -> Capability:
    configured = bool(_setting("FINNHUB_API_KEY"))
    return Capability(
        id="earnings", label="Earnings calendar",
        provides="upcoming earnings dates, used by the Pre-Earnings Momentum scan "
                 "and by the earnings-window labels on Wheel candidates and ledger "
                 "positions",
        state=CONFIGURED if configured else NOT_CONFIGURED,
        available=configured,
        reason=("A Finnhub API key is configured." if configured else
                "No Finnhub API key. The Pre-Earnings Momentum scan cannot run, "
                "and earnings windows on Wheel candidates and ledger positions "
                "read as unknown. Every other feature works."),
        action="" if configured else "Add FINNHUB_API_KEY to app.env",
        provider="Finnhub", docs="docs/CONFIGURATION.md",
        requires=_present("FINNHUB_API_KEY"))


def tastytrade_capability() -> Capability:
    secret, token = _setting("TT_CLIENT_SECRET"), _setting("TT_REFRESH_TOKEN")
    environment = _setting("TT_ENV").lower() or "sandbox"
    requires = _present("TT_CLIENT_SECRET", "TT_REFRESH_TOKEN")
    provides = ("the options ledger, DXLink quotes, and exact-contract Greeks "
                "and beta")

    if not secret and not token:
        return Capability(
            id="tastytrade", label="Tastytrade", provides=provides,
            state=NOT_CONFIGURED, available=False,
            reason="Tastytrade is not connected. The options ledger stays empty "
                   "because there is no broker to read from — this is not an error.",
            action="./setup-brokerages.sh setup tastytrade",
            provider="Tastytrade", requires=requires)

    if not secret or not token:
        missing = "TT_CLIENT_SECRET" if not secret else "TT_REFRESH_TOKEN"
        return Capability(
            id="tastytrade", label="Tastytrade", provides=provides,
            state=INCOMPLETE, available=False,
            reason=f"Tastytrade is partially configured: {missing} is missing.",
            action="./setup-brokerages.sh setup tastytrade",
            provider="Tastytrade", requires=requires)

    if environment not in ("sandbox", "live"):
        return Capability(
            id="tastytrade", label="Tastytrade", provides=provides,
            state=ERROR, available=False,
            reason=f"TT_ENV must be 'sandbox' or 'live', found {environment!r}.",
            action="Correct TT_ENV in app.env",
            provider="Tastytrade", requires=requires)

    return Capability(
        id="tastytrade", label="Tastytrade", provides=provides,
        state=CONFIGURED, available=True,
        reason=f"Tastytrade credentials are configured ({environment}). "
               "Sync to import broker activity.",
        action="Sync Tastytrade", provider="Tastytrade", requires=requires)


def snaptrade_capability() -> Capability:
    client_id, consumer_key = _setting("SNAPTRADE_CLIENT_ID"), _setting("SNAPTRADE_CONSUMER_KEY")
    user_id, user_secret = _setting("SNAPTRADE_USER_ID"), _setting("SNAPTRADE_USER_SECRET")
    requires = _present("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY")
    provides = "retirement holdings and option positions"

    if not client_id and not consumer_key:
        return Capability(
            id="snaptrade", label="Retirement brokerage", provides=provides,
            state=NOT_CONFIGURED, available=False,
            reason="No retirement brokerage is connected. Fidelity and others "
                   "connect through SnapTrade; smallFish never receives your "
                   "brokerage password.",
            action="./setup-brokerages.sh setup snaptrade",
            provider="SnapTrade", requires=requires)

    if not client_id or not consumer_key:
        missing = "SNAPTRADE_CLIENT_ID" if not client_id else "SNAPTRADE_CONSUMER_KEY"
        return Capability(
            id="snaptrade", label="Retirement brokerage", provides=provides,
            state=INCOMPLETE, available=False,
            reason=f"SnapTrade is partially configured: {missing} is missing.",
            action="./setup-brokerages.sh setup snaptrade",
            provider="SnapTrade", requires=requires)

    if not client_id.upper().startswith("PERS-") and not (user_id and user_secret):
        return Capability(
            id="snaptrade", label="Retirement brokerage", provides=provides,
            state=NEEDS_REGISTRATION, available=False,
            reason="Commercial SnapTrade keys need an externally created user "
                   "and linked brokerage before they can be used.",
            action="./setup-brokerages.sh setup snaptrade",
            provider="SnapTrade", requires=requires)

    return Capability(
        id="snaptrade", label="Retirement brokerage", provides=provides,
        state=CONFIGURED, available=True,
        reason="SnapTrade is configured. Sync to import holdings.",
        action="Sync holdings", provider="SnapTrade", requires=requires)


def retirement_risk_capability() -> Capability:
    """Exact-contract Greeks/beta for retirement options need Tastytrade too.

    SnapTrade does not supply those market-data inputs, so this is reported
    separately from the SnapTrade capability. The id remains ``retirement-risk``
    for compatibility; it describes enrichment readiness, not a risk dashboard.
    """
    snaptrade_ready = snaptrade_capability().available
    tastytrade_ready = tastytrade_capability().available
    provides = "exact-contract Greeks and market-metric beta for retirement options"

    if not snaptrade_ready:
        return Capability(
            id="retirement-risk", label="Retirement option market data",
            provides=provides,
            state=NOT_CONFIGURED, available=False,
            reason="Requires a connected retirement brokerage.",
            action="./setup-brokerages.sh setup snaptrade", provider="SnapTrade")

    if not tastytrade_ready:
        return Capability(
            id="retirement-risk", label="Retirement option market data",
            provides=provides,
            state=INCOMPLETE, available=False,
            reason="Holdings are available, but SnapTrade does not supply "
                   "exact-contract Greeks or market-metric beta. Connect "
                   "Tastytrade to enrich held retirement option market data.",
            action="./setup-brokerages.sh setup tastytrade",
            provider="Tastytrade + SnapTrade")

    return Capability(
        id="retirement-risk", label="Retirement option market data",
        provides=provides,
        state=CONFIGURED, available=True,
        reason="Both providers are configured; exact-contract market inputs "
               "can be materialized for held retirement options.",
        provider="Tastytrade + SnapTrade")


CAPABILITIES = (
    core_data_capability,
    finnhub_capability,
    tastytrade_capability,
    snaptrade_capability,
    retirement_risk_capability,
)


def snapshot() -> dict:
    """The full capability report. Contains no secret and no identifier."""
    items = [asdict(build()) for build in CAPABILITIES]
    return {
        "schemaName": "smallfish.capabilities",
        "schemaVersion": 1,
        "capabilities": items,
        "unavailable": [item["id"] for item in items if not item["available"]],
    }
