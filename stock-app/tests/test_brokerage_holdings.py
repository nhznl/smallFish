from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import brokerage_holdings, config, options_activity
from app.main import app


client = TestClient(app)


def _position(*, symbol: str = "DEMO", instrument: str = "Equity",
              quantity: str = "10", mark: str = "90", average: str = "100",
              account: str = "TRADING", retrieved_at: str = "2026-07-28T16:00:00+00:00") -> dict[str, str]:
    return {
        "schema_version": "1", "source": "TASTYTRADE", "account": account,
        "instrument_type": instrument, "contract_symbol": symbol,
        "contract_key": symbol, "underlying_symbol": symbol, "quantity": quantity,
        "direction": "Long", "signed_quantity": quantity, "multiplier": "1",
        "mark": mark, "mark_price": mark, "updated_at": retrieved_at,
        "retrieved_at": retrieved_at, "average_open_price": average,
    }


@pytest.fixture
def holdings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    paths = {
        "SFP_TASTYTRADE_POSITIONS": "positions.csv",
        "SFP_TRADING_HOLDINGS_ENRICHMENT": "enrichment.csv",
        "SFP_TRADING_HOLDINGS_TREND": "trend.csv",
        "SFP_TRADING_HOLDINGS_GL_SNAPSHOTS": "snapshots.csv",
    }
    for name, filename in paths.items():
        monkeypatch.setenv(name, str(tmp_path / filename))
    return tmp_path


def test_trading_holdings_match_retirement_shape_and_keep_metadata_separate(holdings_env):
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [
            _position(),
            _position(symbol="OTHER", quantity="5", mark="20", average="20"),
            _position(symbol="DEMO  260821C00100000", instrument="Equity Option",
                      quantity="-1", mark="1", average="2"),
        ],
    )

    before = brokerage_holdings.portfolio("trading")
    assert [row["symbol"] for row in before["holdings"]] == ["DEMO", "OTHER"]
    assert before["holdings"][0]["category"] == "UNCLASSIFIED"
    assert before["totalInitial"] == pytest.approx(1100)
    assert before["totalCurrent"] == pytest.approx(1000)
    assert before["totalGainLoss"] == pytest.approx(-100)

    brokerage_holdings.update_enrichment(
        "trading", "demo", {"category": "growth", "industry": "software", "note": "Review"}
    )
    after = brokerage_holdings.portfolio("trading")
    demo = after["holdings"][0]
    assert demo["category"] == "GROWTH"
    assert demo["industry"] == "SOFTWARE"
    assert demo["note"] == "Review"
    assert config.trading_holdings_enrichment_csv().is_file()
    assert config.trading_holdings_enrichment_csv() != config.holdings_enrichment_csv()


def test_trading_snapshots_replace_same_sync_date_and_surface_dynamic_column(holdings_env):
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [_position()],
    )

    first = brokerage_holdings.capture_gain_loss_snapshot("trading")
    second = brokerage_holdings.capture_gain_loss_snapshot("trading")
    portfolio = brokerage_holdings.portfolio("trading")

    assert first["replaced"] is False
    assert second["replaced"] is True
    assert second["snapshotCount"] == 1
    assert portfolio["gainLossSnapshots"][0]["syncDate"] == "2026-07-28"
    assert portfolio["holdings"][0]["gainLossSnapshots"]["2026-07-28"] == pytest.approx(-10)


def test_trading_trend_marks_material_adverse_move_as_declining(holdings_env):
    first = [_position(mark="90")]
    second = [_position(mark="80", retrieved_at="2026-07-29T16:00:00+00:00")]

    brokerage_holdings.update_trading_trend(first, now="2026-07-28T16:00:00+00:00")
    brokerage_holdings.update_trading_trend(second, now="2026-07-29T16:00:00+00:00")
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS, second,
    )

    trend = brokerage_holdings.portfolio("trading")["holdings"][0]["trend"]
    assert trend["alert"] is True
    assert trend["direction"] == "LOSS"
    assert trend["dropPct"] == pytest.approx(100)


def test_unknown_holdings_portfolio_is_404(holdings_env):
    with pytest.raises(brokerage_holdings.HoldingsValidationError) as exc:
        brokerage_holdings.portfolio("taxable")
    assert exc.value.status_code == 404


def test_holdings_routes_share_one_contract(holdings_env):
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [_position()],
    )

    response = client.get("/brokerage-ledgers/trading/holdings")
    assert response.status_code == 200
    assert response.json()["holdings"][0]["symbol"] == "DEMO"

    updated = client.put(
        "/brokerage-ledgers/trading/holdings/DEMO/enrichment", json={"note": "Review"}
    )
    assert updated.status_code == 200
    assert updated.json()["note"] == "Review"

    captured = client.post("/brokerage-ledgers/trading/holdings/gain-loss-snapshots")
    assert captured.status_code == 200
    assert captured.json()["portfolio"]["gainLossSnapshots"][0]["syncDate"] == "2026-07-28"

    missing = client.get("/brokerage-ledgers/taxable/holdings")
    assert missing.status_code == 404
