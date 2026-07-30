"""Beta and Greeks for the option legs a brokerage currently holds.

Reads the materialized SnapTrade holdings ledger, decides which underlyings and
exact contracts need fresh market data, asks the provider-neutral
``services.options_market`` API for them, and writes the beta/Greek artifacts the
risk table reads:

    sync_betas(fetcher)               -> underlying beta observations
    sync_greeks(fetcher)              -> exact-contract IV/Greek observations
    sync_held_option_market_data()    -> both, best-effort and independent

Provider selection and provider symbol syntax (OCC to dxFeed, for instance) stay
behind the neutral API; nothing here imports a provider transport package.

Retain-prior-on-miss is the rule for both artifacts: a currently held
symbol/contract the provider omits keeps its previously stored observation, and
an observation is dropped only when the holding itself is no longer current.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from services import options_market

from ... import config, options_activity
from ...options_risk import apply_call_coverage
from . import snaptrade as snaptrade_importer

BETA_HEADERS = [
    "schema_version", "source", "symbol", "beta", "beta_updated_at", "retrieved_at",
]
GREEKS_HEADERS = [
    "schema_version", "source", "account", "contract_symbol", "contract_key",
    "streamer_symbol", "implied_volatility", "option_price", "delta", "gamma",
    "theta", "rho", "vega", "observed_at", "event_time_ms", "retrieved_at",
]


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch_ms_to_iso(value: Any) -> str:
    """UTC ISO timestamp from dxFeed epoch-millis, or '' if unparseable."""
    try:
        millis = float(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).isoformat()


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def atomic_write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    """Replace ``path`` with ``rows`` in one rename, or leave it untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: ("" if row.get(key) is None else row.get(key)) for key in headers})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_rows(path: Path, headers: list[str]) -> list[dict[str, str]]:
    """Read a materialized market-data artifact, restricted to ``headers``."""
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [{key: row.get(key, "") for key in headers} for row in csv.DictReader(handle)]


def _greek_key(row: dict[str, str]) -> tuple[str, str]:
    """Identity of a greeks row: (account, contract). Retirement legs can span
    sub-accounts, so both dimensions are part of the key."""
    return (str(row.get("account", "")).upper(), str(row.get("contract_key", "")))


# --------------------------------------------------------------------------- #
# current option legs from the holdings ledger                                 #
# --------------------------------------------------------------------------- #

def _is_share_holding(row: dict[str, Any]) -> bool:
    """Cash is not a deliverable share; every other non-option class is."""
    asset_class = str(row.get("asset_class") or "").upper()
    return asset_class not in {"OPTION", "CASH", ""} and bool(row.get("symbol"))


def _share_pool(ledger: list[dict[str, Any]]) -> dict[tuple[str, str], Decimal]:
    """Long share counts per account and ticker, for short-call coverage."""
    pool: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for row in ledger:
        if not _is_share_holding(row):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        pool[(str(row.get("account_name") or ""), symbol)] += _dec(row.get("quantity"))
    return dict(pool)


def _option_rows() -> list[dict[str, Any]]:
    """Current option legs from the SnapTrade ledger, in the risk-engine row
    shape (underlying in ``symbol`` for price-cache/beta lookup)."""
    ledger = snaptrade_importer.read_holdings_ledger()
    rows: list[dict[str, Any]] = []
    for row in ledger:
        if row.get("asset_class") != "OPTION":
            continue
        quantity = _dec(row.get("quantity"))
        if quantity == 0:
            continue
        option_type = str(row.get("option_type", "")).upper()
        if quantity < 0:
            trade_type = "SHORT_PUT" if option_type == "PUT" else "SHORT_CALL"
        else:
            trade_type = "LONG_PUT" if option_type == "PUT" else "LONG_CALL"
        contract = row.get("symbol", "")
        strike = _dec(row.get("strike"))
        rows.append({
            "id": f"retirement-option:{row.get('account_id', '')}:{contract}",
            "contract_symbol": contract,
            "contract_key": contract,
            "account": row.get("account_name", ""),
            "wheel_id": "",
            "symbol": str(row.get("underlying_symbol", "")).upper(),
            "trade_type": trade_type,
            "qty": float(abs(quantity)),
            "strike": float(strike) if strike else None,
            "expiry": row.get("expiry", ""),
            "open_date": "",
            "mark_price": float(_dec(row.get("price"))),
            "mark_retrieved_at": row.get("retrieved_at", ""),
            "credit": None,
            "debit": None,
            "status": "OPEN",
            "non_standard": False,
            "notes": "",
        })
    apply_call_coverage(rows, _share_pool(ledger))
    return rows


# --------------------------------------------------------------------------- #
# underlying beta                                                              #
# --------------------------------------------------------------------------- #

def _fetch_betas(symbols: list[str]) -> list[Any]:
    """Underlying beta observations for ``symbols`` via options market-data."""
    result = options_market.fetch_underlying_metrics(symbols, metrics=("beta",))
    if result.error:
        raise RuntimeError(result.error)
    return list(result.observations)


def sync_betas(fetcher=_fetch_betas) -> dict[str, Any]:
    """Fetch market-metric beta for each held option underlying and store it for
    the risk table. Requires the options market-data provider.

    Retain-prior-on-miss: an underlying whose beta the fetch omits keeps its
    previously stored value instead of disappearing from the risk table."""
    current_underlyings = {row["symbol"] for row in _option_rows() if row["symbol"]}
    if not current_underlyings:
        atomic_write(config.retirement_option_betas_csv(), BETA_HEADERS, [])
        return {"observed": 0, "retained": 0, "missing": 0, "symbols": []}

    metrics = fetcher(sorted(current_underlyings))
    now = _now()
    newest_betas: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        beta = getattr(metric, "beta", None)
        if beta is None:
            continue
        symbol = str(getattr(metric, "symbol", "")).upper()
        updated = getattr(metric, "beta_updated_at", None)
        provenance = (
            getattr(metric, "provenance", None)
            or options_market.PROVENANCE_TASTYTRADE_MARKET_METRICS
        )
        newest_betas[symbol] = {
            "schema_version": "1",
            "source": provenance,
            "symbol": symbol,
            "beta": str(beta),
            "beta_updated_at": updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
            "retrieved_at": now,
        }

    # Retain-prior-on-miss, mirroring options_activity: newest fresh beta per
    # underlying, else the previously stored row; underlyings no longer held drop.
    previous_betas = {row["symbol"].upper(): row
                      for row in read_rows(config.retirement_option_betas_csv(), BETA_HEADERS)}
    persisted = []
    for symbol in sorted(current_underlyings):
        row = newest_betas.get(symbol) or previous_betas.get(symbol)
        if row is not None:
            persisted.append(row)
    atomic_write(config.retirement_option_betas_csv(), BETA_HEADERS, persisted)
    return {
        "observed": len(newest_betas),
        "retained": sum(1 for s in current_underlyings
                        if s not in newest_betas and s in previous_betas),
        "missing": sum(1 for s in current_underlyings
                       if s not in newest_betas and s not in previous_betas),
        "symbols": [row["symbol"] for row in persisted],
    }


# --------------------------------------------------------------------------- #
# exact-contract Greeks and implied volatility                                 #
# --------------------------------------------------------------------------- #

def _fetch_greeks(legs: list[dict[str, str]], timeout_seconds: float) -> list[Any]:
    """Exact-contract Greek observations for ``legs`` via options market-data."""
    contracts = [leg["contract_symbol"] for leg in legs]
    result = options_market.fetch_greeks(contracts, timeout_seconds=timeout_seconds)
    if result.error:
        raise RuntimeError(result.error)
    return list(result.observations)


def sync_greeks(fetcher=_fetch_greeks, timeout_seconds: float = 12.0) -> dict[str, Any]:
    """Fetch exact-contract IV/Greeks for each held option leg.

    Retain-prior-on-miss: a contract whose fetch returns nothing keeps its
    previously stored observation instead of dropping out of the risk table."""
    legs = [
        {
            "contract_symbol": row["contract_symbol"],
            "contract_key": row["contract_key"],
            "account": row["account"],
        }
        for row in _option_rows()
        if row["contract_symbol"]
    ]
    if not legs:
        atomic_write(config.retirement_option_greeks_csv(), GREEKS_HEADERS, [])
        return {"observed": 0, "retained": 0, "missing": 0, "requested": 0, "streamers": []}

    by_contract = {leg["contract_symbol"]: leg for leg in legs}
    observations = fetcher(legs, timeout_seconds)
    now = _now()
    normalized_greeks: list[dict[str, Any]] = []
    for observation in observations:
        contract_symbol = str(getattr(observation, "contract_symbol", "") or "")
        leg = by_contract.get(contract_symbol)
        if leg is None:
            continue
        iv = getattr(observation, "implied_volatility", None)
        if iv is None:
            iv = getattr(observation, "volatility", None)
        if iv is None:
            continue
        # Stamp with the provider quote time, not wall-clock now: a live fetch
        # after UTC midnight would otherwise be dated "tomorrow" and dropped.
        event_time_ms = getattr(observation, "event_time_ms", None)
        if event_time_ms in (None, ""):
            event_time_ms = getattr(observation, "time", None)
        observed_at = getattr(observation, "observed_at", None) or (
            _epoch_ms_to_iso(event_time_ms) or now
        )
        option_price = getattr(observation, "option_price", None)
        if option_price is None:
            option_price = getattr(observation, "price", "") or ""
        provider_symbol = str(
            getattr(observation, "provider_symbol", "")
            or getattr(observation, "event_symbol", "")
            or ""
        )
        provenance = (
            getattr(observation, "provenance", None)
            or options_market.PROVENANCE_TASTYTRADE_DXLINK
        )
        normalized_greeks.append({
            "schema_version": "1",
            "source": provenance,
            "account": leg["account"],
            "contract_symbol": leg["contract_symbol"],
            "contract_key": leg["contract_key"],
            "streamer_symbol": provider_symbol,
            "implied_volatility": str(iv),
            "option_price": str(option_price),
            "delta": str(getattr(observation, "delta", "") or ""),
            "gamma": str(getattr(observation, "gamma", "") or ""),
            "theta": str(getattr(observation, "theta", "") or ""),
            "rho": str(getattr(observation, "rho", "") or ""),
            "vega": str(getattr(observation, "vega", "") or ""),
            "observed_at": observed_at,
            "event_time_ms": str(event_time_ms or ""),
            "retrieved_at": now,
        })

    # Retain-prior-on-miss, mirroring options_activity: newest fresh observation
    # per contract, else the previously stored row; contracts no longer held drop.
    newest_greeks = {
        _greek_key(row): row
        for row in sorted(normalized_greeks, key=lambda item: item["observed_at"])
    }
    previous_current = {
        _greek_key(row): row
        for row in read_rows(config.retirement_option_greeks_csv(), GREEKS_HEADERS)
    }
    current_keys = {(leg["account"].upper(), leg["contract_key"]) for leg in legs}
    persisted = []
    for key in sorted(current_keys):
        row = newest_greeks.get(key) or previous_current.get(key)
        if row is not None:
            persisted.append(row)
    atomic_write(config.retirement_option_greeks_csv(), GREEKS_HEADERS, persisted)
    return {
        "observed": len(newest_greeks),
        "retained": sum(1 for k in current_keys
                        if k not in newest_greeks and k in previous_current),
        "missing": sum(1 for k in current_keys
                       if k not in newest_greeks and k not in previous_current),
        "requested": len(legs),
        "streamers": [row["streamer_symbol"] for row in persisted],
    }


# --------------------------------------------------------------------------- #
# MARKET_DATA resource command                                                 #
# --------------------------------------------------------------------------- #

def sync_market_data() -> dict[str, Any]:
    """Refresh both betas and Greeks for the risk table.

    Each leg is best-effort so one failing source doesn't sink the other.
    """
    report: dict[str, Any] = {}
    try:
        report["betas"] = sync_betas()
    except Exception as exc:  # noqa: BLE001 — betas are optional.
        report["betas_error"] = options_activity._safe_market_data_error(exc)
    try:
        report["greeks"] = sync_greeks()
    except Exception as exc:  # noqa: BLE001 — greeks are optional.
        report["greeks_error"] = options_activity._safe_market_data_error(exc)
    return report


#: Registry name for the held-option market-data resource.
def sync_held_option_market_data() -> dict[str, Any]:
    return sync_market_data()
