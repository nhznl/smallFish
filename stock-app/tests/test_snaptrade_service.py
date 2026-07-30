"""The ``snaptrade_service`` compatibility facade.

Three things are covered: the legacy all-resource ``sync`` orchestrator it owns,
the documented ``python -m app.snaptrade_service`` CLI it delegates to
``app.snaptrade_setup``, and a structural guard proving normalization, artifact
schemas, provider transport, and financial policy have not returned to it.

Setup behavior itself lives in ``test_snaptrade_setup.py``; the SnapTrade
artifacts live in ``test_importer_snaptrade.py``.
"""

from __future__ import annotations

import ast
import csv
from pathlib import Path

import pytest

from app import config, snaptrade_service, snaptrade_setup


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
# legacy sync orchestrator / snapshot                                          #
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
    from app.brokerages.importers import snaptrade as importer

    monkeypatch.setattr(
        importer, "sync_events",
        lambda *args, **kwargs: {"groups_reactivated": 2},
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
# CLI compatibility: the documented module path delegates to the setup owner    #
# --------------------------------------------------------------------------- #

def test_cli_delegates_to_the_setup_owner_with_the_legacy_commands(monkeypatch):
    seen: dict[str, object] = {}

    def fake_main(argv=None, *, sync, snapshot):
        seen.update(argv=argv, sync=sync, snapshot=snapshot)
        return 0

    monkeypatch.setattr(snaptrade_setup, "main", fake_main)

    assert snaptrade_service._main(["accounts"]) == 0
    assert seen["argv"] == ["accounts"]
    assert seen["sync"] is snaptrade_service.sync
    assert seen["snapshot"] is snaptrade_service.snapshot


def test_cli_sync_runs_the_facade_orchestrator(monkeypatch, capsys):
    """The injected seam is the facade's own module attribute, so patches apply."""
    monkeypatch.setattr(
        snaptrade_service, "sync", lambda: {"source": "SNAPTRADE", "holdings": []}
    )

    assert snaptrade_service._main(["sync"]) == 0
    assert "SNAPTRADE" in capsys.readouterr().out


def test_cli_register_saves_credentials_without_printing_them(
        tmp_path, monkeypatch, capsys):
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "SNAPTRADE_CLIENT_ID=client\n"
        "SNAPTRADE_USER_ID=\n"
        "SNAPTRADE_USER_SECRET=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        snaptrade_setup, "register_user",
        lambda: {"userId": "registered-user", "userSecret": "generated-secret"},
    )
    monkeypatch.setattr(snaptrade_setup.config, "repo_root", lambda: tmp_path)

    assert snaptrade_service._main(["register"]) == 0

    output = capsys.readouterr().out
    assert "registered-user" not in output
    assert "generated-secret" not in output
    assert "saved securely" in output


def test_cli_validation_error_exits_two_without_leaking_detail(monkeypatch, capsys):
    monkeypatch.setattr(
        snaptrade_setup, "list_accounts",
        lambda: (_ for _ in ()).throw(
            snaptrade_service.SnapTradeValidationError("missing credentials", 503)
        ),
    )

    with pytest.raises(SystemExit) as exc:
        snaptrade_service._main(["accounts"])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "missing credentials" in err


# --------------------------------------------------------------------------- #
# structural guard: the facade must stay a facade                              #
# --------------------------------------------------------------------------- #

FACADE_SOURCE = Path(snaptrade_service.__file__).read_text(encoding="utf-8")
FACADE_TREE = ast.parse(FACADE_SOURCE)

#: The only modules the facade may alias names from.
RE_EXPORT_OWNERS = {"snaptrade_setup", "importer", "held_option_market_data"}

#: Imports the facade needs to re-export and orchestrate, and nothing more. A
#: provider SDK, artifact IO, arithmetic, argparse, or configuration import here
#: means an implementation moved back into the facade.
ALLOWED_IMPORTS = {"__future__", "typing", ".", ".brokerages.importers"}

#: Vocabulary that only appears when normalization, an artifact schema, provider
#: transport, or CLI presentation is implemented rather than delegated.
FORBIDDEN_MARKERS = (
    "csv", "DictReader", "DictWriter", "HEADERS = [",
    "Decimal", "def _normalize", "atomic_write(",
    "tempfile", "os.replace", "config.",
    "snaptrade_io", "services.", "argparse",
)


def _facade_function(name: str) -> ast.FunctionDef:
    for node in FACADE_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in the facade")


def _facade_code_without_prose() -> str:
    """The facade's executable statements, with comments and docstrings dropped."""
    body = [
        node for node in FACADE_TREE.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def test_facade_defines_only_the_orchestrator_and_the_cli_delegate():
    defined = {
        node.name for node in FACADE_TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert defined == {"sync", "_main"}
    assert [node for node in FACADE_TREE.body if isinstance(node, ast.ClassDef)] == []


def test_facade_imports_nothing_beyond_its_re_export_owners():
    imported: set[str] = set()
    for node in ast.walk(FACADE_TREE):
        assert not isinstance(node, ast.Import), ast.unparse(node)
        if isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS


def test_facade_module_level_names_are_plain_re_exports():
    for node in FACADE_TREE.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        assert isinstance(value, ast.Attribute), ast.unparse(node)
        assert isinstance(value.value, ast.Name), ast.unparse(node)
        assert value.value.id in RE_EXPORT_OWNERS, ast.unparse(node)


def test_facade_sync_only_orchestrates_the_resource_commands():
    called = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(_facade_function("sync"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in RE_EXPORT_OWNERS
    }
    assert called == {
        "importer.sync_holdings",
        "importer.read_holdings_ledger",
        "importer.sync_activity",
        "held_option_market_data.sync_held_option_market_data",
    }


def test_facade_main_only_delegates_to_the_setup_owner():
    calls = [node for node in ast.walk(_facade_function("_main"))
             if isinstance(node, ast.Call)]
    assert len(calls) == 1
    assert ast.unparse(calls[0].func) == "snaptrade_setup.main"
    assert {keyword.arg for keyword in calls[0].keywords} == {"sync", "snapshot"}


def test_facade_contains_no_normalization_schema_transport_or_cli_body():
    code = _facade_code_without_prose()
    present = [marker for marker in FORBIDDEN_MARKERS if marker in code]
    assert present == []


def test_facade_keeps_the_documented_module_entry_point():
    assert any(
        isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
        for node in FACADE_TREE.body
    )


# --------------------------------------------------------------------------- #
# gain/loss trend tracking, reached through the facade's compatibility alias    #
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
