"""The shared adverse-move rule.

Both brokerages used to carry their own copy of this; the copies agreed, and
this is now the single implementation both feed. The cases below are the ones
the rule exists to get right — a slow slide must still trip, a recovery must
clear, and a holding sitting near breakeven must not alert on noise.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.brokerages import trend

NOW = "2026-07-28T16:00:00+00:00"
LATER = "2026-07-29T16:00:00+00:00"
LATEST = "2026-07-30T16:00:00+00:00"


def observed(pct: str, *, symbol: str = "DEMO",
             account: str = "TRADING") -> trend.Observation:
    return trend.Observation(
        account_id=account, account_name=account, symbol=symbol,
        gain_loss_pct=Decimal(pct),
    )


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "holdings_trend.csv"


def test_a_first_sighting_records_a_peak_without_alerting(path):
    state = trend.advance([observed("12")], path=path, now=NOW)

    row = state[("TRADING", "DEMO")]
    assert row["peak_pct"] == "12"
    assert row["alert"] == ""
    assert trend.display(row, 12.0)["alert"] is False


def test_a_material_adverse_move_trips_and_re_baselines(path):
    trend.advance([observed("20")], path=path, now=NOW)
    trend.advance([observed("5")], path=path, now=LATER)

    row = trend.read(path)[("TRADING", "DEMO")]
    block = trend.display(row, 5.0)
    assert block["alert"] is True
    assert block["from_pct"] == pytest.approx(20)
    assert block["to_pct"] == pytest.approx(5)
    assert block["drop_pct"] == pytest.approx(75)
    assert block["alert_at"] == LATER
    # Re-baselined, so a further leg down can alert again rather than going quiet.
    assert row["peak_pct"] == "5"


def test_a_favorable_move_clears_the_alert(path):
    trend.advance([observed("20")], path=path, now=NOW)
    trend.advance([observed("5")], path=path, now=LATER)
    trend.advance([observed("25")], path=path, now=LATEST)

    block = trend.display(trend.read(path)[("TRADING", "DEMO")], 25.0)
    assert block["alert"] is False
    assert block["drop_pct"] is None
    assert block["peak_pct"] == pytest.approx(25)


def test_a_slow_slide_keeps_accumulating_toward_the_threshold(path):
    """Each step is under the threshold; against a held peak they add up.

    Re-baselining on every sub-threshold move would let a position bleed out
    indefinitely without ever alerting.
    """
    trend.advance([observed("20")], path=path, now=NOW)
    trend.advance([observed("19")], path=path, now=LATER)      # 5%, under 10%
    assert trend.read(path)[("TRADING", "DEMO")]["alert"] == ""
    assert trend.read(path)[("TRADING", "DEMO")]["peak_pct"] == "20"

    trend.advance([observed("17")], path=path, now=LATEST)     # 15% against 20
    assert trend.display(trend.read(path)[("TRADING", "DEMO")], 17.0)["alert"] is True


def test_a_holding_near_breakeven_never_alerts(path):
    """Within the materiality floor a relative move is noise, and dividing by a
    near-zero peak would manufacture an enormous percentage."""
    trend.advance([observed("2")], path=path, now=NOW)
    trend.advance([observed("-2")], path=path, now=LATER)

    assert trend.display(trend.read(path)[("TRADING", "DEMO")], -2.0)["alert"] is False


def test_the_same_symbol_in_two_accounts_trends_independently(path):
    trend.advance(
        [observed("20", account="A"), observed("20", account="B")], path=path, now=NOW
    )
    trend.advance(
        [observed("5", account="A"), observed("21", account="B")], path=path, now=LATER
    )

    state = trend.read(path)
    assert trend.display(state[("A", "DEMO")], 5.0)["alert"] is True
    assert trend.display(state[("B", "DEMO")], 21.0)["alert"] is False


def test_a_holding_that_is_no_longer_held_is_dropped(path):
    """Keeping its peak would alert against a position that does not exist."""
    trend.advance([observed("20"), observed("8", symbol="GONE")], path=path, now=NOW)
    trend.advance([observed("20")], path=path, now=LATER)

    assert set(trend.read(path)) == {("TRADING", "DEMO")}


def test_display_reports_no_recorded_state_as_quiet(path):
    block = trend.display(None, -4.0)
    assert block == {
        "alert": False, "peak_pct": None, "peak_at": "", "drop_pct": None,
        "from_pct": None, "to_pct": None, "alert_at": None, "direction": "LOSS",
    }
