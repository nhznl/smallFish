"""Path resolution for the FastAPI backend.

Normal startup loads root ``app.env`` before importing the application. Shared
locations are derived from ``SFP_DATA_DIR`` and ``SFP_LOG_DIR``. Repository-relative defaults remain available for
tests and direct module use:

    price cache       ../data                    (shared OHLCV flat files)
    strategy reports  ../data/reports/pre_earnings_momentum
                                              (study scan snapshot source)
    wheel reports     ../data/wheel                 (/wheelCandidates source)
    universe registry ../data/universe.csv
    retired symbols   ../data/retired_symbols.csv

Individual artifacts remain overridable via their existing environment variables
(used by tests to point at fixtures). Resolution happens at call time.
"""

from __future__ import annotations

import os
from pathlib import Path

# stock-app/ (this file is app/config.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# Browser origins allowed to call this API. Override with a comma-separated
# CORS_ORIGINS setting in app.env.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:4200"
    ).split(",") if o.strip()
]
# Back-compat alias.
ANGULAR_ORIGIN = ALLOWED_ORIGINS[0]

def _resolve(env_var: str, default_rel: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser().resolve()
    return (BASE_DIR / default_rel).resolve()


def _under(env_var: str, root: Path, relative: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser().resolve()
    return (root / relative).resolve()


def data_dir() -> Path:
    return _resolve("SFP_DATA_DIR", "../data")


def logs_dir() -> Path:
    return _resolve("SFP_LOG_DIR", "../logs")


def price_cache_root() -> Path:
    return _under("SFP_PRICE_CACHE", data_dir(), ".")


def reports_dir() -> Path:
    return _under(
        "SFP_REPORTS_DIR", data_dir(), "reports/pre_earnings_momentum"
    )


def wheel_dir() -> Path:
    return _under("SFP_WHEEL_DIR", data_dir(), "wheel")


def sector_rotation_dir() -> Path:
    return _under("SFP_SECTOR_ROTATION_DIR", data_dir(), "sector_rotation")


def studies_dir() -> Path:
    """Mutable Research Studies root: user overrides and written scan snapshots."""
    return _under("SFP_STUDIES_DIR", data_dir(), "studies")


def bundled_studies_dir() -> Path:
    """Read-only Research Studies artifacts packaged with the repository.

    The materialized catalog and study records are committed, so they are
    available in any clone. They are reachable independently of ``SFP_DATA_DIR``
    on purpose: pointing the mutable data root at an empty external directory
    must not make the bundled studies disappear.
    """
    return (BASE_DIR / "../data/studies").resolve()


def universe_csv() -> Path:
    return _under("SFP_UNIVERSE_CSV", data_dir(), "universe.csv")


def retired_symbols_csv() -> Path:
    return _under("SFP_RETIRED_SYMBOLS_CSV", data_dir(), "retired_symbols.csv")


def options_activity_csv() -> Path:
    """Immutable normalized Tastytrade transactions used by Symbol Ledger."""
    return _under("SFP_OPTIONS_ACTIVITY", data_dir(), "ledger_trading/options_activity.csv")


def options_position_marks_csv() -> Path:
    """Latest broker position marks used for open group P/L."""
    return _under("SFP_OPTIONS_POSITION_MARKS", data_dir(), "ledger_trading/options_position_marks.csv")


def tastytrade_positions_csv() -> Path:
    """Latest normalized Tastytrade positions for the broker-neutral combined
    ledger. Unlike ``options_position_marks_csv``, this includes every current
    equity and option position and is not consumed by the legacy options API."""
    return _under(
        "SFP_TASTYTRADE_POSITIONS", data_dir(), "ledger_trading/positions.csv"
    )


def trading_holdings_trend_csv() -> Path:
    """Per-holding adverse-move state for Tastytrade equity positions."""
    return _under(
        "SFP_TRADING_HOLDINGS_TREND", data_dir(), "ledger_trading/holdings_trend.csv"
    )




def trading_holdings_enrichment_csv() -> Path:
    """Editable Trading holding classifications and notes, separate from broker facts."""
    return _under(
        "SFP_TRADING_HOLDINGS_ENRICHMENT",
        data_dir(),
        "ledger_trading/holdings_enrichment.csv",
    )


def trading_holdings_settings_csv() -> Path:
    """Per-ledger performance baselines for the Trading holdings view."""
    return _under(
        "SFP_TRADING_HOLDINGS_SETTINGS",
        data_dir(),
        "ledger_trading/holdings_settings.csv",
    )


def options_greeks_csv() -> Path:
    """Latest timestamped Tastytrade Greeks for current option positions."""
    return _under("SFP_OPTIONS_GREEKS", data_dir(), "ledger_trading/options_greeks.csv")


def options_betas_csv() -> Path:
    """Latest timestamped Tastytrade beta for current option underlyings."""
    return _under("SFP_OPTIONS_BETAS", data_dir(), "ledger_trading/options_betas.csv")


def snaptrade_holdings_csv() -> Path:
    """Normalized brokerage holdings imported from SnapTrade (Fidelity, etc.)."""
    return _under(
        "SFP_SNAPTRADE_HOLDINGS", data_dir(), "ledger_retirement/positions.csv"
    )


def holdings_trend_csv() -> Path:
    """Per-holding gain/loss trend state (peak high-water mark + adverse-move
    alerts) tracked across syncs, so a position whose gain is shrinking or whose
    loss is deepening can be flagged for action. Kept separate from the immutable
    holdings ledger."""
    return _under(
        "SFP_HOLDINGS_TREND", data_dir(), "ledger_retirement/holdings_trend.csv"
    )




def holdings_enrichment_csv() -> Path:
    """Editable symbol classifications (category/industry/note) merged onto
    imported broker holdings; kept separate from broker facts like the options
    trade-group metadata."""
    return _under(
        "SFP_HOLDINGS_ENRICHMENT", data_dir(), "ledger_retirement/holdings_enrichment.csv"
    )


def holdings_settings_csv() -> Path:
    """Per-ledger performance baselines for the Retirement holdings view."""
    return _under(
        "SFP_HOLDINGS_SETTINGS", data_dir(), "ledger_retirement/holdings_settings.csv"
    )


def retirement_option_events_csv() -> Path:
    """Immutable, append-only ledger of SnapTrade option transaction events for
    the retirement view. Keyed by the SnapTrade activity id; retained across a
    contract's close so a fully-closed group keeps its realized P/L instead of
    vanishing with the live-positions feed (mirrors options_activity.csv)."""
    return _under(
        "SFP_RETIREMENT_OPTION_EVENTS", data_dir(), "ledger_retirement/options_activity.csv"
    )


def retirement_option_betas_csv() -> Path:
    """Tastytrade market-metric betas for the retirement option underlyings,
    fetched for risk analytics (SnapTrade does not provide beta)."""
    return _under(
        "SFP_RETIREMENT_OPTION_BETAS", data_dir(), "ledger_retirement/option_betas.csv"
    )


def retirement_option_greeks_csv() -> Path:
    """Tastytrade dxFeed exact-contract IV/Greeks for the retirement option legs,
    fetched for risk analytics (SnapTrade does not provide Greeks)."""
    return _under(
        "SFP_RETIREMENT_OPTION_GREEKS", data_dir(), "ledger_retirement/option_greeks.csv"
    )


def symbol_ledger_metadata_csv() -> Path:
    """Editable per-symbol notes for the brokerage-agnostic Symbol Ledger.

    Keyed by ``(brokerage_id, symbol)``, so one file serves every configured
    brokerage. App-owned metadata only: it never contains a broker fact.
    """
    return _under(
        "SFP_SYMBOL_LEDGER_METADATA", data_dir(), "ledger_symbols/symbol_ledger_metadata.csv"
    )


def symbol_ledger_archives_csv() -> Path:
    """Immutable reset boundaries sealing a completed, flat symbol period.

    A boundary records where a period ended and what it contained; it never
    copies, moves, or deletes a broker event. Displayed archive values are
    recomputed and verified against the creation-time hash on every read.
    """
    return _under(
        "SFP_SYMBOL_LEDGER_ARCHIVES", data_dir(), "ledger_symbols/symbol_ledger_archives.csv"
    )


def symbol_ledger_gain_loss_snapshots_csv() -> Path:
    """User-captured holding G/L percentages for the brokerage-agnostic Holdings
    resource, keyed by brokerage so Trading and Retirement stay separate."""
    return _under(
        "SFP_SYMBOL_LEDGER_GL_SNAPSHOTS", data_dir(),
        "ledger_symbols/holdings_gain_loss_snapshots.csv",
    )


def options_activity_excluded_symbols() -> set[str]:
    """Symbols intentionally kept outside the imported broker-activity ledger."""
    return {
        symbol.strip().upper()
        for symbol in os.environ.get("SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS", "").split(",")
        if symbol.strip()
    }


def portfolios_csv() -> Path:
    """User-authored portfolio definitions (name/description/tags/creation date)."""
    return _under("SFP_PORTFOLIOS_CSV", data_dir(), "portfolios/portfolios.csv")


def portfolio_members_csv() -> Path:
    """Symbols belonging to each user-authored portfolio."""
    return _under("SFP_PORTFOLIOS_MEMBERS_CSV", data_dir(), "portfolios/portfolio_members.csv")


def premiums_dir() -> Path:
    return _under("SFP_PREMIUMS_DIR", data_dir(), "premiums")


def events_csv() -> Path:
    return _under("SFP_EVENTS_CSV", data_dir(), "events.csv")


def repo_root() -> Path:
    """Repository root containing ``commands.sh``; defaults to ``..``."""
    return _resolve("SFP_REPO_ROOT", "..")


def stockdat_script() -> Path:
    """Path to this backend's self-contained yfinance information bridge."""
    return _resolve("SFP_STOCKDAT_SCRIPT", "app/stock_data_retriever.py")


def static_dir() -> Path:
    """Built Angular bundle root; generated by ``./commands.sh build-ui``."""
    root = _resolve("SFP_STATIC_DIR", "static")
    browser = root / "browser"
    return browser if browser.is_dir() else root
