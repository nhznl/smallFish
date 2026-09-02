"""Pure, non-persistent stock/ETF Portfolio Analysis preview."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ... import config, data_reader, universe_read
from ..contracts import BrokerageSnapshot
from ..portfolio_analysis_profile import BUCKETS
from . import portfolio_analysis


class PreviewValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _positive_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PreviewValidationError("INVALID_PREVIEW", f"{field} must be greater than zero.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PreviewValidationError("INVALID_PREVIEW", f"{field} must be greater than zero.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise PreviewValidationError("INVALID_PREVIEW", f"{field} must be greater than zero.")
    return parsed


def _price(snapshot: BrokerageSnapshot, symbol: str, assumed: Any
           ) -> tuple[Decimal, str, str | None]:
    if assumed not in (None, ""):
        return (
            _positive_decimal(assumed, "assumed_price"), "USER_ASSUMPTION",
            datetime.now(timezone.utc).isoformat(),
        )
    candidates = [
        row for row in snapshot.positions
        if row.symbol == symbol and row.instrument == "EQUITY"
        and row.mark_per_unit is not None and row.mark_per_unit > 0
    ]
    if candidates:
        newest = max(candidates, key=lambda row: (
            row.provenance.observed_at or row.provenance.retrieved_at or ""
        ))
        return (
            newest.mark_per_unit, "PROVIDER_MARK",
            newest.provenance.observed_at or newest.provenance.retrieved_at,
        )
    root = config.price_cache_root()
    try:
        years = sorted(
            [int(path.name) for path in root.iterdir()
             if path.is_dir() and path.name.isdigit()],
            reverse=True,
        )[:2] if root.is_dir() else []
    except OSError:
        years = []
    try:
        frame = data_reader.read_prices(root, symbol, sorted(years)) if years else None
    except Exception:
        frame = None
    if frame is not None and not frame.empty:
        row = frame.iloc[-1]
        price = Decimal(str(row["adj_close"]))
        if price > 0:
            return price, "CACHED_ADJUSTED_CLOSE", row["date"].date().isoformat()
    raise PreviewValidationError(
        "PRICE_UNAVAILABLE",
        "No saved provider mark or cached adjusted close is available; enter an assumed price.",
    )


def _finding_key(row: dict[str, Any]) -> tuple[str, str | None, str]:
    return row["code"], row.get("symbol"), row.get("scope", "")


def build(snapshot: BrokerageSnapshot, *, payload: dict[str, Any],
          profile_path: Path, classifications_path: Path,
          metadata_path: Path | None = None) -> dict[str, Any]:
    allowed = {
        "account_id", "side", "symbol", "quantity", "notional",
        "assumed_price", "funding_source", "allocation_bucket",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise PreviewValidationError(
            "UNSUPPORTED_FIELD", f"Cannot preview {', '.join(sorted(unknown))}.",
        )
    account_id = str(payload.get("account_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    side = str(payload.get("side") or "").strip().upper()
    funding = str(payload.get("funding_source") or "").strip().upper()
    if not account_id:
        raise PreviewValidationError("ACCOUNT_REQUIRED", "An account is required.")
    known_accounts = {
        row.account.account_id for row in (*snapshot.positions, *snapshot.account_capital)
    }
    if account_id not in known_accounts:
        raise PreviewValidationError("UNKNOWN_ACCOUNT", "That account is not in this brokerage snapshot.")
    if side not in {"BUY", "SELL"}:
        raise PreviewValidationError("INVALID_PREVIEW", "side must be BUY or SELL.")
    if not symbol:
        raise PreviewValidationError("INVALID_SYMBOL", "A symbol is required.")
    universe = universe_read.load_registry(config.universe_csv())
    already_held = any(row.symbol == symbol for row in snapshot.positions)
    if symbol not in universe and not already_held:
        raise PreviewValidationError(
            "UNSUPPORTED_SYMBOL", "Version 1 previews only universe or currently held symbols.",
        )
    security_type = str(universe.get(symbol, {}).get("type") or "STOCK").upper()
    if security_type not in {"STOCK", "ETF"} and not already_held:
        raise PreviewValidationError("UNSUPPORTED_INSTRUMENT", "Version 1 previews only stocks and ETFs.")
    has_quantity = payload.get("quantity") not in (None, "")
    has_notional = payload.get("notional") not in (None, "")
    if has_quantity == has_notional:
        raise PreviewValidationError(
            "INVALID_PREVIEW", "Send exactly one of quantity or notional.",
        )
    price, price_source, price_as_of = _price(snapshot, symbol, payload.get("assumed_price"))
    if has_quantity:
        quantity = _positive_decimal(payload["quantity"], "quantity")
        notional = quantity * price
    else:
        notional = _positive_decimal(payload["notional"], "notional")
        quantity = notional / price
    if side == "BUY" and funding not in {"ACCOUNT_CASH", "NEW_CONTRIBUTION"}:
        raise PreviewValidationError(
            "INVALID_FUNDING_SOURCE", "A buy requires ACCOUNT_CASH or NEW_CONTRIBUTION.",
        )
    raw_bucket = str(payload.get("allocation_bucket") or "").strip().upper()
    if raw_bucket and raw_bucket not in BUCKETS - {"UNKNOWN", "CASH"}:
        raise PreviewValidationError(
            "INVALID_CLASSIFICATION", "allocation_bucket is not supported for this preview.",
        )
    if side == "SELL":
        held = sum((
            row.signed_quantity for row in snapshot.positions
            if row.account.account_id == account_id and row.symbol == symbol
            and row.instrument == "EQUITY" and row.signed_quantity > 0
        ), Decimal("0"))
        if quantity > held:
            raise PreviewValidationError(
                "SHORT_SALE_NOT_SUPPORTED", "The proposed sale exceeds the long position.",
            )
    sign = Decimal("1") if side == "BUY" else Decimal("-1")
    capital_delta = notional if side == "BUY" and funding == "NEW_CONTRIBUTION" else Decimal("0")
    liquid_delta = (
        -notional if side == "BUY" and funding == "ACCOUNT_CASH"
        else notional if side == "SELL" else Decimal("0")
    )
    before = portfolio_analysis.build(
        snapshot, profile_path=profile_path, classifications_path=classifications_path,
        metadata_path=metadata_path, include_historical=False,
    )
    after = portfolio_analysis.build(
        snapshot, profile_path=profile_path, classifications_path=classifications_path,
        metadata_path=metadata_path,
        capital_delta=capital_delta, liquid_delta=liquid_delta,
        position_adjustments={(account_id, symbol): (sign * quantity, sign * notional)},
        temporary_classifications=(
            {(account_id, symbol): raw_bucket} if raw_bucket else None
        ),
        include_historical=False,
    )
    before_findings = {_finding_key(row): row for row in before["summary"]["findings"]}
    after_findings = {_finding_key(row): row for row in after["summary"]["findings"]}
    new_keys = after_findings.keys() - before_findings.keys()
    resolved_keys = before_findings.keys() - after_findings.keys()
    common = before_findings.keys() & after_findings.keys()
    worsened = [
        after_findings[key] for key in common
        if (after_findings[key].get("actual") or 0) > (before_findings[key].get("actual") or 0)
    ]
    improved = [
        after_findings[key] for key in common
        if (after_findings[key].get("actual") or 0) < (before_findings[key].get("actual") or 0)
    ]
    metric_names = (
        "growth_pct", "liquid_pct", "deployment_pct", "gross_marked_exposure_pct",
    )
    deltas = []
    for metric in metric_names:
        old = before["summary"]["allocation"].get(metric)
        new = after["summary"]["allocation"].get(metric)
        deltas.append({
            "metric": metric, "before": old, "after": new,
            "change": None if old is None or new is None else new - old,
        })
    return {
        "schema_name": "smallfish.portfolio-analysis-preview",
        "schema_version": 1,
        "brokerage": before["brokerage"],
        "proposal": {
            "account_id": account_id, "side": side, "symbol": symbol,
            "quantity": float(quantity), "notional": float(notional),
            "assumed_price": float(price), "price_source": price_source,
            "price_as_of": price_as_of, "funding_source": funding if side == "BUY" else "SALE_PROCEEDS_TO_CASH",
            "fees_taxes_slippage_included": False,
        },
        "before": before["summary"], "after": after["summary"],
        "metric_deltas": deltas,
        "new_findings": [
            after_findings[key]
            for key in sorted(new_keys, key=lambda item: (item[0], item[1] or "", item[2]))
        ],
        "worsened_findings": worsened, "improved_findings": improved,
        "resolved_findings": [
            before_findings[key]
            for key in sorted(resolved_keys, key=lambda item: (item[0], item[1] or "", item[2]))
        ],
        "persisted": False,
    }
