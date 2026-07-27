"""Shared strategy-report CSV contract tests."""

from models.strategy_report import parse_strategy_report


def test_legacy_report_parser_accepts_shorter_headers_and_ignores_blank_tickers() -> None:
    rows = parse_strategy_report(
        "date,ticker,sma_20,vol_spike\n"
        "2026-07-13,testa,34.5,True\n"
        "2026-07-13,,12,False\n"
    )

    assert len(rows) == 1
    assert rows[0].ticker == "TESTA"
    assert rows[0].value("sma_20") == "34.5"
    assert rows[0].value("missing") is None


def test_legacy_report_parser_keeps_current_extra_columns_by_name() -> None:
    row = parse_strategy_report(
        "date,ticker,open,score_total,reason_summary\n"
        "2026-07-13,AAPL,210.5,79.0,earnings expected\n"
    )[0]

    assert row.value("open") == "210.5"
    assert row.value("score_total") == "79.0"
