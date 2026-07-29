from __future__ import annotations

import csv

import pytest

from app import config, snaptrade_service


@pytest.fixture
def holdings_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(tmp_path / "holdings.csv"))
    monkeypatch.setenv(
        "SFP_HOLDINGS_GL_SNAPSHOTS", str(tmp_path / "gain_loss_snapshots.csv")
    )
    for key in (
        "SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY",
        "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# fixtures shaped like real SnapTrade SDK response bodies                       #
# --------------------------------------------------------------------------- #

def _account():
    return {
        "id": "acct-1",
        "name": "BrokerageLink",
        "number": "652782616",
        "institution_name": "Fidelity",
        "balance": {"total": {"amount": "184261.04", "currency": {"code": "USD"}}},
    }


def _positions():
    """Shaped like a ``get_all_account_positions`` response body."""
    return {
        "results": [
            {
                "instrument": {
                    "kind": "stock",
                    "symbol": "JOBY",
                    "description": "Joby Aviation Inc",
                    "currency": "USD",
                },
                "units": "600",
                "price": "7.535",
                "cost_basis": "12.86",  # per share
                "currency": "USD",
            },
            {
                "instrument": {
                    "kind": "option",
                    "symbol": "CLX   260918P00070000",
                    "description": "CLX 70 Put",
                    "option_type": "PUT",
                    "strike_price": "70",
                    "expiration_date": "2026-09-18",
                    "multiplier": "100",
                    "underlying": {"kind": "stock", "symbol": "CLX"},
                },
                "units": "-1",
                "price": "1.25",
                "cost_basis": "24",  # per contract
                "currency": "USD",
            },
            {
                "instrument": {
                    "kind": "mutualfund",
                    "symbol": "FDRXX",
                    "description": "Fidelity Government Cash Reserves",
                    "currency": "USD",
                },
                "units": "179865.04",
                "price": "1",
                "cost_basis": "1",
                "currency": "USD",
                "cash_equivalent": True,
            },
        ],
        "data_freshness": {"as_of": "2026-07-23T22:10:59Z"},
    }


def _provider():
    return [(_account(), _positions())]


# --------------------------------------------------------------------------- #
# config                                                                       #
# --------------------------------------------------------------------------- #

def test_config_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SFP_SNAPTRADE_HOLDINGS", raising=False)
    assert config.snaptrade_holdings_csv() == tmp_path / "ledger_retirement" / "snaptrade_holdings.csv"
    override = tmp_path / "custom_holdings.csv"
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(override))
    assert config.snaptrade_holdings_csv() == override
    monkeypatch.delenv("SFP_HOLDINGS_GL_SNAPSHOTS", raising=False)
    assert config.holdings_gain_loss_snapshots_csv() == (
        tmp_path / "ledger_retirement" / "holdings_gain_loss_snapshots.csv"
    )


# --------------------------------------------------------------------------- #
# credentials                                                                  #
# --------------------------------------------------------------------------- #

def test_credentials_missing_app_keys(holdings_env):
    with pytest.raises(snaptrade_service.SnapTradeValidationError) as exc:
        snaptrade_service._credentials()
    assert exc.value.status_code == 503


def test_credentials_from_env_vars(holdings_env, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "cid")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    monkeypatch.setenv("SNAPTRADE_USER_ID", "uid")
    monkeypatch.setenv("SNAPTRADE_USER_SECRET", "usecret")
    creds = snaptrade_service._credentials()
    assert (creds.client_id, creds.consumer_key) == ("cid", "ckey")
    assert (creds.user_id, creds.user_secret) == ("uid", "usecret")


def test_personal_key_needs_no_user(holdings_env):
    creds = snaptrade_service.SnapTradeCredentials("PERS-ABC", "k", None, None)
    assert snaptrade_service._is_personal_key(creds)
    assert snaptrade_service._user_kwargs(creds) == {}


def test_commercial_key_requires_registered_user(holdings_env):
    creds = snaptrade_service.SnapTradeCredentials("c", "k", None, None)
    assert not snaptrade_service._is_personal_key(creds)
    with pytest.raises(snaptrade_service.SnapTradeValidationError) as exc:
        snaptrade_service._user_kwargs(creds)
    assert exc.value.status_code == 503
    registered = snaptrade_service.SnapTradeCredentials("c", "k", "uid", "usecret")
    assert snaptrade_service._user_kwargs(registered) == {
        "user_id": "uid", "user_secret": "usecret",
    }


def test_register_rejected_for_personal_key(holdings_env, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "PERS-ABC")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    with pytest.raises(snaptrade_service.SnapTradeValidationError, match="personal API keys"):
        snaptrade_service.register_user()


def test_registration_credentials_are_saved_without_being_printed(
        tmp_path, monkeypatch, capsys):
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "SNAPTRADE_CLIENT_ID=client\n"
        "SNAPTRADE_USER_ID=\n"
        "SNAPTRADE_USER_SECRET=\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)
    credentials = {"userId": "registered-user", "userSecret": "generated-secret"}
    monkeypatch.setattr(snaptrade_service, "register_user", lambda: credentials)
    monkeypatch.setattr(snaptrade_service.config, "repo_root", lambda: tmp_path)

    assert snaptrade_service._main(["register"]) == 0

    output = capsys.readouterr().out
    assert "registered-user" not in output
    assert "generated-secret" not in output
    assert "saved securely" in output
    body = env_path.read_text(encoding="utf-8")
    assert "SNAPTRADE_USER_ID='registered-user'" in body
    assert "SNAPTRADE_USER_SECRET='generated-secret'" in body
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_registration_refuses_to_replace_existing_credentials(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "SNAPTRADE_USER_ID=existing-user\nSNAPTRADE_USER_SECRET=existing-secret\n",
        encoding="utf-8",
    )

    with pytest.raises(snaptrade_service.SnapTradeValidationError) as exc:
        snaptrade_service._validate_registration_target(env_path)

    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- #
# sync / normalization / snapshot                                              #
# --------------------------------------------------------------------------- #

def test_sync_writes_ledger_and_summary(holdings_env):
    summary = snaptrade_service.sync(provider=_provider)

    by_symbol = {h["symbol"]: h for h in summary["holdings"]}
    assert set(by_symbol) == {"JOBY", "CLX   260918P00070000", "FDRXX"}

    equity = by_symbol["JOBY"]
    assert equity["assetClass"] == "STOCK"
    assert equity["quantity"] == pytest.approx(600)
    assert equity["marketValue"] == pytest.approx(4521.0)
    assert equity["costBasis"] == pytest.approx(7716.0)
    assert equity["openPnl"] == pytest.approx(-3195.0)  # mv - cost

    option = by_symbol["CLX   260918P00070000"]
    assert option["assetClass"] == "OPTION"
    assert option["underlyingSymbol"] == "CLX"
    assert option["optionType"] == "PUT"
    assert option["strike"] == pytest.approx(70.0)
    assert option["expiry"] == "2026-09-18"
    assert option["quantity"] == pytest.approx(-1)
    assert option["marketValue"] == pytest.approx(-125.0)  # -1 * 1.25 * 100
    assert option["costBasis"] == pytest.approx(-24.0)  # -1 * 24 per contract
    assert option["openPnl"] == pytest.approx(-101.0)

    cash = by_symbol["FDRXX"]
    assert cash["assetClass"] == "CASH"  # cash_equivalent money market
    assert cash["marketValue"] == pytest.approx(179865.04)

    assert summary["totalValue"] == pytest.approx(4521.0 - 125.0 + 179865.04)
    assert summary["byAssetClass"]["CASH"]["holdingCount"] == 1
    assert summary["byAccount"]["BrokerageLink"]["holdingCount"] == 3
    assert summary["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 3,
        "added": 3,
        "changed": 0,
        "unchanged": 0,
        "removed": 0,
        "groups_reactivated": 0,
    }

    # Ledger persisted with schema version + immutable broker facts.
    rows = list(csv.DictReader(config.snaptrade_holdings_csv().open(encoding="utf-8")))
    assert len(rows) == 3
    assert {r["schema_version"] for r in rows} == {"1"}
    assert {r["source"] for r in rows} == {"SNAPTRADE"}
    assert all(r["imported_at"] for r in rows)


def test_sync_reports_unchanged_and_removed_positions(holdings_env):
    snaptrade_service.sync(provider=_provider)
    unchanged = snaptrade_service.sync(provider=_provider)
    assert unchanged["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 3,
        "added": 0,
        "changed": 0,
        "unchanged": 3,
        "removed": 0,
        "groups_reactivated": 0,
    }

    removed = snaptrade_service.sync(provider=lambda: [(_account(), {"results": []})])
    assert removed["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 0,
        "added": 0,
        "changed": 0,
        "unchanged": 0,
        "removed": 3,
        "groups_reactivated": 0,
    }


def test_sync_includes_reactivated_option_group_count(holdings_env, monkeypatch):
    from app import retirement_options

    monkeypatch.setattr(
        retirement_options, "sync_events", lambda: {"groups_reactivated": 2},
    )

    summary = snaptrade_service.sync(provider=_provider)

    assert summary["sync"]["groups_reactivated"] == 2


def test_snapshot_round_trips_written_ledger(holdings_env):
    written = snaptrade_service.sync(provider=_provider)
    read_back = snaptrade_service.snapshot()
    assert read_back["totalValue"] == pytest.approx(written["totalValue"])
    assert len(read_back["holdings"]) == len(written["holdings"])


def test_snapshot_empty_when_no_ledger(holdings_env):
    summary = snaptrade_service.snapshot()
    assert summary["holdings"] == []
    assert summary["totalValue"] == 0.0




















# --------------------------------------------------------------------------- #
# gain/loss trend tracking                                                      #
# --------------------------------------------------------------------------- #

def _ledger_row(symbol, pct, account_id="acct-1", asset_class="STOCK"):
    return {
        "account_id": account_id, "account_name": "BrokerageLink",
        "symbol": symbol, "asset_class": asset_class, "open_pnl_pct": str(pct),
    }


def _advance(rows_by_pct):
    """Run consecutive syncs; return the final trend state keyed (account, symbol)."""
    state = {}
    for i, rows in enumerate(rows_by_pct):
        state = snaptrade_service._update_trend(rows, now=f"t{i}")
    return state


def test_trend_first_sight_sets_peak_no_alert(holdings_env):
    state = snaptrade_service._update_trend([_ledger_row("PANW", 82)], now="t0")
    row = state[("acct-1", "PANW")]
    assert row["peak_pct"] == "82" and row["alert"] == ""


def test_trend_gainer_relative_drop_triggers_after_holding_peak(holdings_env):
    # 82 -> 76 is 7.3% relative: no alert, peak held at 82.
    _advance([[_ledger_row("PANW", 82)]])
    s1 = snaptrade_service._update_trend([_ledger_row("PANW", 76)], now="t1")
    assert s1[("acct-1", "PANW")]["alert"] == ""
    assert s1[("acct-1", "PANW")]["peak_pct"] == "82"
    # 82 -> 53 is 35.4% relative: alert, and the peak re-baselines to 53.
    s2 = snaptrade_service._update_trend([_ledger_row("PANW", 53)], now="t2")
    r = s2[("acct-1", "PANW")]
    assert r["alert"] == "true"
    assert r["alert_from_pct"] == "82" and r["alert_to_pct"] == "53"
    assert float(r["alert_drop_pct"]) == pytest.approx(35.37, abs=0.02)
    assert r["peak_pct"] == "53"


def test_trend_favorable_move_clears_alert_and_ratchets_peak(holdings_env):
    _advance([[_ledger_row("PANW", 82)], [_ledger_row("PANW", 53)]])  # alert at 53
    s = snaptrade_service._update_trend([_ledger_row("PANW", 60)], now="t2")  # 60 > peak 53
    r = s[("acct-1", "PANW")]
    assert r["alert"] == "" and r["peak_pct"] == "60"


def test_trend_gradual_bleed_accumulates_against_held_peak(holdings_env):
    _advance([[_ledger_row("PANW", 82)]])
    # Each step is a small dip, none 10% relative alone; the peak stays at 82.
    for pct, now in [(78, "t1"), (75, "t2")]:
        s = snaptrade_service._update_trend([_ledger_row("PANW", pct)], now=now)
        assert s[("acct-1", "PANW")]["peak_pct"] == "82"
        assert s[("acct-1", "PANW")]["alert"] == ""
    # 82 -> 73 crosses 10% relative cumulatively: alert.
    s = snaptrade_service._update_trend([_ledger_row("PANW", 73)], now="t3")
    assert s[("acct-1", "PANW")]["alert"] == "true"


def test_trend_loser_deepening_triggers(holdings_env):
    _advance([[_ledger_row("MRVL", -30)]])
    # -30 -> -35 deepens the loss by 16.7% relative: alert.
    s = snaptrade_service._update_trend([_ledger_row("MRVL", -35)], now="t1")
    r = s[("acct-1", "MRVL")]
    assert r["alert"] == "true"
    assert float(r["alert_drop_pct"]) == pytest.approx(16.67, abs=0.02)


def test_trend_loss_shrinking_is_favorable(holdings_env):
    _advance([[_ledger_row("MRVL", -30)]])
    s = snaptrade_service._update_trend([_ledger_row("MRVL", -20)], now="t1")  # loss shrank
    r = s[("acct-1", "MRVL")]
    assert r["alert"] == "" and r["peak_pct"] == "-20"


def test_trend_materiality_floor_skips_near_breakeven(holdings_env):
    # Peak +3% is within the ±5% floor: a further dip is treated as flat noise.
    _advance([[_ledger_row("NEAR", 3)]])
    s = snaptrade_service._update_trend([_ledger_row("NEAR", 1)], now="t1")
    assert s[("acct-1", "NEAR")]["alert"] == ""


def test_trend_skips_option_rows(holdings_env):
    s = snaptrade_service._update_trend(
        [_ledger_row("CLX", -50, asset_class="OPTION")], now="t0")
    assert ("acct-1", "CLX") not in s


def test_read_ledger_rejects_unknown_schema(holdings_env):
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=snaptrade_service.HOLDINGS_HEADERS)
        writer.writeheader()
        writer.writerow({"schema_version": "999", "source": "SNAPTRADE", "symbol": "X"})
    with pytest.raises(snaptrade_service.SnapTradeValidationError) as exc:
        snaptrade_service.snapshot()
    assert exc.value.status_code == 409
