from __future__ import annotations

from decimal import Decimal

import pandas as pd
from fastapi.testclient import TestClient

from app.brokerages.contracts import (
    AccountCapitalFact,
    AccountRef,
    BrokerageCapabilities,
    BrokerageCoverage,
    BrokerageDescriptor,
    BrokerageSnapshot,
    OptionContract,
    PortfolioAnalysisPolicy,
    PositionFact,
    Provenance,
)
from app.brokerages.portfolio_analysis_profile import (
    read_profile,
    set_classification,
    update_profile,
)
from app.brokerages.projections import holdings as holdings_projection
from app.brokerages.projections import portfolio_analysis, portfolio_preview
from app.main import app


POLICY = PortfolioAnalysisPolicy(
    objective="LONG_TERM_AGGRESSIVE_GROWTH",
    required_fields=(
        "max_single_issuer_pct", "max_speculative_pct",
        "max_put_assignment_commitment_pct", "max_stress_loss_pct",
        "minimum_liquid_pct", "growth_min_pct", "growth_max_pct",
        "cash_min_pct", "cash_max_pct", "max_sector_pct",
        "max_top_five_pct", "first_expected_withdrawal_date",
    ),
    assesses_growth_range=True,
    assesses_top_five=True,
)
EXPOSURE_POLICY = PortfolioAnalysisPolicy(
    objective="SPECULATIVE_ACCOUNT",
    required_fields=(
        "max_single_issuer_pct", "max_speculative_pct",
        "max_put_assignment_commitment_pct", "max_stress_loss_pct",
        "minimum_liquid_pct", "max_gross_exposure_pct",
    ),
    optional_fields=("deployment_min_pct", "deployment_max_pct", "max_sector_pct"),
    assesses_gross_exposure=True,
)


def profile_values(**overrides):
    values = {
        "max_single_issuer_pct": 50,
        "max_speculative_pct": 10,
        "max_put_assignment_commitment_pct": 100,
        "max_stress_loss_pct": 100,
        "minimum_liquid_pct": 5,
        "growth_min_pct": 70,
        "growth_max_pct": 95,
        "cash_min_pct": 5,
        "cash_max_pct": 30,
        "max_sector_pct": 100,
        "max_top_five_pct": 100,
        "first_expected_withdrawal_date": "2050-01-01",
    }
    return {**values, **overrides}


def equity(symbol: str, quantity: str, price: str, *, account_id: str = "acct-1"):
    q, p = Decimal(quantity), Decimal(price)
    return PositionFact(
        brokerage_id="demo",
        account=AccountRef(account_id=account_id, label="Synthetic Account"),
        instrument="EQUITY", symbol=symbol, signed_quantity=q,
        multiplier=Decimal("1"), mark_per_unit=p, market_value=q * p,
        provenance=Provenance(
            source="SYNTHETIC", retrieved_at="2026-08-29T20:00:00+00:00",
            observed_at="2026-08-29T19:59:00+00:00",
        ),
    )


def snapshot(*positions: PositionFact, capital: bool = True) -> BrokerageSnapshot:
    capital_facts = () if not capital else (
        AccountCapitalFact(
            brokerage_id="demo",
            account=AccountRef(account_id="acct-1", label="Synthetic Account"),
            currency="USD", net_liquidating_value=Decimal("1000"),
            cash_balance=Decimal("200"), buying_power=Decimal("200"),
            maintenance_requirement=Decimal("0"),
            provenance=Provenance(
                source="SYNTHETIC", retrieved_at="2026-08-29T20:00:00+00:00"
            ),
        ),
    )
    return BrokerageSnapshot(
        descriptor=BrokerageDescriptor(
            id="demo", label="Synthetic", institution="SYNTHETIC",
            portfolio_role="LONG_TERM", adapter="ARTIFACT",
            analysis_policy=POLICY,
        ),
        capabilities=BrokerageCapabilities(), coverage=BrokerageCoverage(),
        positions=positions, account_capital=capital_facts,
    )


def configure(monkeypatch, tmp_path):
    profile_path = tmp_path / "profiles.json"
    classifications_path = tmp_path / "classifications.csv"
    universe = tmp_path / "universe.csv"
    universe.write_text(
        "symbol,name,type,memberships,source,pinned,last_seen,sector\n"
        "AAA,Synthetic A,STOCK,test,test,false,2026-08-29,Technology\n"
        "BBB,Synthetic B,ETF,test,test,false,2026-08-29,Industrials\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SFP_UNIVERSE_CSV", str(universe))
    update_profile(profile_path, "demo", POLICY, profile_values())
    return profile_path, classifications_path


def test_profile_is_progressive_and_supplies_no_numeric_defaults(tmp_path):
    path = tmp_path / "profiles.json"
    empty = read_profile(path, "demo", POLICY)
    assert empty["status"] == "UNCONFIGURED"
    assert empty["objective"] == POLICY.objective
    assert "max_single_issuer_pct" not in empty

    partial = update_profile(path, "demo", POLICY, {"max_single_issuer_pct": 25})
    assert partial["status"] == "PARTIAL"
    complete = update_profile(path, "demo", POLICY, profile_values())
    assert complete["status"] == "COMPLETE"


def test_analysis_uses_net_liquidation_and_reports_traceable_trim_math(monkeypatch, tmp_path):
    profile_path, classifications_path = configure(monkeypatch, tmp_path)
    set_classification(
        classifications_path, brokerage_id="demo", account_id="acct-1",
        symbol="BBB", bucket="SPECULATIVE",
    )
    result = portfolio_analysis.build(
        snapshot(equity("AAA", "60", "10"), equity("BBB", "20", "10")),
        profile_path=profile_path, classifications_path=classifications_path,
        include_historical=False,
    )
    assert result["summary"]["capital"]["analyzed_capital"] == 1000.0
    assert result["summary"]["allocation"]["growth_pct"] == 80.0
    assert result["summary"]["allocation"]["liquid_pct"] == 20.0
    assert result["summary"]["allocation"]["buckets"]["SPECULATIVE"]["pct_of_capital"] == 20.0
    issuer = next(row for row in result["summary"]["findings"]
                  if row["code"] == "SINGLE_ISSUER_LIMIT")
    assert issuer["symbol"] == "AAA"
    assert issuer["title"] == "AAA exceeds the selected issuer limit"
    assert issuer["excess_amount"] == 100.0
    assert issuer["remediation"]["approximate_units"] == 10.0
    assert issuer["remediation"]["new_outside_capital_to_dilute"] == 200.0


def test_issuer_finding_uses_holdings_display_name(monkeypatch, tmp_path):
    profile_path, classifications_path = configure(monkeypatch, tmp_path)
    metadata_path = tmp_path / "holdings_enrichment.csv"
    holdings_projection.write_metadata(
        metadata_path, "AAA", {"display_name": "Example Target Date Fund"},
    )
    result = portfolio_analysis.build(
        snapshot(equity("AAA", "60", "10"), equity("BBB", "20", "10")),
        profile_path=profile_path, classifications_path=classifications_path,
        metadata_path=metadata_path, include_historical=False,
    )
    issuer = next(row for row in result["summary"]["findings"]
                  if row["code"] == "SINGLE_ISSUER_LIMIT")
    assert issuer["symbol"] == "AAA"
    assert issuer["title"] == "Example Target Date Fund exceeds the selected issuer limit"
    named = next(row for row in result["items"] if row["symbol"] == "AAA")
    assert named["display_name"] == "Example Target Date Fund"
    other = next(row for row in result["items"] if row["symbol"] == "BBB")
    assert other["display_name"] == ""


def test_missing_capital_fails_closed_without_hiding_known_critical_option_risk(
        monkeypatch, tmp_path):
    profile_path, classifications_path = configure(monkeypatch, tmp_path)
    call = PositionFact(
        brokerage_id="demo", account=AccountRef("acct-1", "Synthetic Account"),
        instrument="OPTION", symbol="AAA", signed_quantity=Decimal("-2"),
        multiplier=Decimal("100"), mark_per_unit=Decimal("1"),
        market_value=Decimal("-200"),
        contract=OptionContract(
            occ_symbol="AAA  270115C00020000", underlying="AAA",
            option_type="CALL", strike=Decimal("20"), expiry="2027-01-15",
            multiplier=Decimal("100"),
        ),
        provenance=Provenance(source="SYNTHETIC", retrieved_at="2026-08-29T20:00:00+00:00"),
    )
    result = portfolio_analysis.build(
        snapshot(equity("AAA", "100", "10"), call, capital=False),
        profile_path=profile_path, classifications_path=classifications_path,
        include_historical=False,
    )
    findings = {row["code"] for row in result["summary"]["findings"]}
    assert "ACCOUNT_CAPITAL_UNAVAILABLE" in findings
    assert "UNCOVERED_SHORT_CALL" in findings
    assert result["summary"]["verdicts"]["profile_fit"] == "CRITICAL_RISK"
    assert result["summary"]["verdicts"]["data_confidence"] == "UNAVAILABLE"
    assert all(row["weight_pct"] is None for row in result["items"])


def test_preview_is_non_persistent_and_distinguishes_cash_from_contribution(
        monkeypatch, tmp_path):
    profile_path, classifications_path = configure(monkeypatch, tmp_path)
    current = snapshot(equity("AAA", "40", "10"), equity("BBB", "20", "10"))
    profile_before = profile_path.read_bytes()
    result = portfolio_preview.build(
        current,
        payload={
            "account_id": "acct-1", "side": "BUY", "symbol": "AAA",
            "quantity": 10, "assumed_price": 10,
            "funding_source": "ACCOUNT_CASH",
        },
        profile_path=profile_path, classifications_path=classifications_path,
    )
    assert result["persisted"] is False
    assert result["before"]["capital"]["analyzed_capital"] == 1000.0
    assert result["after"]["capital"]["analyzed_capital"] == 1000.0
    assert result["after"]["allocation"]["liquid_pct"] == 10.0
    assert profile_path.read_bytes() == profile_before
    assert not classifications_path.exists()

    contribution = portfolio_preview.build(
        current,
        payload={
            "account_id": "acct-1", "side": "BUY", "symbol": "AAA",
            "notional": 100, "assumed_price": 10,
            "funding_source": "NEW_CONTRIBUTION",
        },
        profile_path=profile_path, classifications_path=classifications_path,
    )
    assert contribution["after"]["capital"]["analyzed_capital"] == 1100.0
    assert contribution["after"]["allocation"]["liquid_pct"] == 18.181818181818183


def test_profile_routes_preserve_role_owned_objective(monkeypatch, tmp_path):
    monkeypatch.setenv("SFP_PORTFOLIO_ANALYSIS_PROFILES", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("SFP_TASTYTRADE_POSITIONS", str(tmp_path / "missing-positions.csv"))
    monkeypatch.setenv("SFP_OPTIONS_ACTIVITY", str(tmp_path / "missing-activity.csv"))
    monkeypatch.setenv("SFP_TRADING_ACCOUNT_CAPITAL", str(tmp_path / "missing-capital.csv"))
    client = TestClient(app)
    response = client.patch(
        "/api/brokerages/tastytrade/portfolio-analysis/profile",
        json={"max_single_issuer_pct": 25},
    )
    assert response.status_code == 200
    assert response.json()["profile"]["objective"] == "SPECULATIVE_TRADING"
    assert response.json()["profile"]["status"] == "PARTIAL"
    fetched = client.get("/api/brokerages/tastytrade/portfolio-analysis/profile")
    assert fetched.status_code == 200
    assert fetched.json()["profile"]["max_single_issuer_pct"] == 25.0


def test_current_holdings_replay_requires_and_reports_aligned_sessions(monkeypatch, tmp_path):
    profile_path, classifications_path = configure(monkeypatch, tmp_path)
    cache = tmp_path / "cache"
    year = cache / "2025"
    year.mkdir(parents=True)
    dates = pd.bdate_range("2025-01-02", periods=260)

    def write_prices(symbol: str, base: float, step: float, cycle: int):
        lines = []
        for index, day in enumerate(dates):
            close = base + step * index + (index % cycle) * 0.17
            lines.append(
                f"{day:%m-%d-%Y},{close},{close},{close},{close},{close},1000"
            )
        (year / f"{symbol}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    write_prices("AAA", 100, 0.4, 7)
    write_prices("SPY", 200, 0.3, 5)
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache))
    result = portfolio_analysis.build(
        snapshot(equity("AAA", "80", "10")),
        profile_path=profile_path, classifications_path=classifications_path,
    )
    replay = result["summary"]["historical_risk"]
    assert replay["label"] == "Current-holdings replay"
    assert replay["status"] == "COMPLETE"
    assert replay["aligned_sessions"] >= 252
    assert replay["annualized_volatility_pct"] is not None
    assert replay["beta_vs_spy"] is not None
    assert replay["correlation_vs_spy"] is not None
    assert replay["maximum_drawdown_pct"] <= 0


def test_optional_deployment_floor_does_not_label_account_underinvested(
        monkeypatch, tmp_path):
    profile_path = tmp_path / "profiles.json"
    classifications_path = tmp_path / "classifications.csv"
    universe = tmp_path / "universe.csv"
    universe.write_text(
        "symbol,name,type,memberships,source,pinned,last_seen,sector\n"
        "AAA,Synthetic A,STOCK,test,test,false,2026-08-29,Technology\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SFP_UNIVERSE_CSV", str(universe))
    update_profile(profile_path, "demo", EXPOSURE_POLICY, {
        "max_single_issuer_pct": 100,
        "max_speculative_pct": 100,
        "max_put_assignment_commitment_pct": 100,
        "max_stress_loss_pct": 100,
        "minimum_liquid_pct": 5,
        "max_gross_exposure_pct": 200,
    })
    base = snapshot(equity("AAA", "10", "10"))
    account = BrokerageSnapshot(
        descriptor=BrokerageDescriptor(
            id="demo", label="Synthetic", institution="SYNTHETIC",
            portfolio_role="SPECULATIVE", adapter="ARTIFACT",
            analysis_policy=EXPOSURE_POLICY,
        ),
        capabilities=base.capabilities, coverage=base.coverage,
        positions=base.positions, account_capital=base.account_capital,
    )
    result = portfolio_analysis.build(
        account, profile_path=profile_path,
        classifications_path=classifications_path, include_historical=False,
    )
    assert result["summary"]["allocation"]["deployment_pct"] == 80.0
    assert result["summary"]["verdicts"]["capital_deployment"] == "NOT_ASSESSED"
    assert "DEPLOYMENT_BELOW_TARGET" not in {
        row["code"] for row in result["summary"]["findings"]
    }
