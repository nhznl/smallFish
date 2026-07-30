"""SnapTrade importer: option-activity materialization and its artifact seams.

Holdings normalization and the summary shape are exercised in
``test_importer_snaptrade_holdings.py``; this suite covers the immutable
option-event ledger the ACTIVITY resource owns.
"""

from __future__ import annotations

import csv
from datetime import date
from types import SimpleNamespace

import pytest

from app import config
from app.brokerages.importers import snaptrade as importer


@pytest.fixture
def opts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(tmp_path / "holdings.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_EVENTS", str(tmp_path / "events.csv"))
    return tmp_path


def _account():
    return SimpleNamespace(id="acct-1", name="BrokerageLink")


def _opt_activity(act_id, action, activity_type, occ, underlying, opt_type, strike,
                  expiry, amount, units, price="1.0", fee="0.66",
                  trade_date="2026-07-15T04:00:00Z"):
    """A SnapTrade get_account_activities option row, shaped like the SDK body
    (structured option_symbol + activity-level option_type action)."""
    return SimpleNamespace(
        id=act_id, type=activity_type, option_type=action, amount=amount,
        units=units, price=price, fee=fee, trade_date=trade_date,
        settlement_date=trade_date,
        description=f"{action} {opt_type} ({underlying})",
        option_symbol=SimpleNamespace(
            ticker=occ, strike_price=strike, expiration_date=expiry,
            option_type=opt_type,
            underlying_symbol=SimpleNamespace(symbol=underlying),
        ),
    )


def _provider(*activities):
    return lambda start_date, end_date: [(_account(), list(activities))]


_MSFT_OCC = "MSFT  260724P00380000"


def test_sync_events_imports_options_and_is_idempotent(opts_env):
    provider = _provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1"),
        SimpleNamespace(id="d1", type="DIVIDEND", option_symbol=None, amount="5",
                        units="0"),  # non-option: skipped
    )
    r = importer.sync_events(provider=provider)
    assert (r["events_received"], r["events_inserted"], r["events_updated"]) == (1, 1, 0)

    events = importer.read_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["underlying_symbol"] == "MSFT"
    assert ev["option_type"] == "PUT"          # from option_symbol
    assert ev["action"] == "SELL_TO_OPEN"      # from activity-level option_type
    assert ev["occ_symbol"] == _MSFT_OCC
    assert ev["net_value"] == "370.34"
    assert ev["units"] == "-1"

    # Re-running the same window upserts by id — no duplicates.
    r2 = importer.sync_events(provider=provider)
    assert (r2["events_inserted"], r2["events_updated"]) == (0, 1)
    assert len(importer.read_events()) == 1


def test_sync_events_rejects_inverted_window(opts_env):
    with pytest.raises(importer.RetirementOptionsError):
        importer.sync_events(provider=_provider(),
                             start_date=date(2026, 7, 10),
                             end_date=date(2026, 7, 1))


def test_sync_activity_is_the_registry_name_for_sync_events(opts_env, monkeypatch):
    """The registry ACTIVITY command and the legacy seam are one implementation."""
    calls = []
    monkeypatch.setattr(importer, "sync_events",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or {})

    importer.sync_activity()

    assert calls == [((), {})]


def test_read_events_returns_every_header_column(opts_env):
    importer.sync_events(provider=_provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1"),
    ))

    rows = importer.read_events()

    assert [row.keys() for row in rows] == [dict.fromkeys(importer.EVENT_HEADERS).keys()]
    with config.retirement_option_events_csv().open(encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == importer.EVENT_HEADERS
