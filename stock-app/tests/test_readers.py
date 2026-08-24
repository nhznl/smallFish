import pytest

from app import readers
from models.wheel import WHEEL_COLUMNS, WHEEL_SCHEMA_VERSION


def test_strategy_report_full_row(fixtures_dir):
    rows = readers.read_latest_strategy_report(fixtures_dir / "reports")
    by_ticker = {r["ticker"]: r["report"] for r in rows}
    assert set(by_ticker) == {"TESTA", "TESTB"}
    a = by_ticker["TESTA"]
    # Types and values follow the published API shape.
    assert a["smaTwenty"] == 34.893
    assert a["volSpike"] is False
    assert a["higherLow"] is True
    assert a["daysInBand"] == 9 and isinstance(a["daysInBand"], int)
    assert a["daysToEvent"] == 61.0
    assert a["sector"] == "Consumer Staples"
    assert a["scoreTotal"] == 79.0


def test_strategy_report_nullable_and_blank_semantics(fixtures_dir):
    rows = readers.read_latest_strategy_report(fixtures_dir / "reports")
    b = next(r["report"] for r in rows if r["ticker"] == "TESTB")
    # Nullable numeric blanks become None.
    assert b["daysToEvent"] is None
    assert b["relStrengthSpy"] is None
    assert b["daysSinceMacdCross"] is None
    # nullable String blanks -> None
    assert b["eventDate"] is None
    assert b["eventType"] is None
    # blank boolean -> False (Boolean.parseBoolean)
    assert b["higherLow"] is False
    assert b["volSpike"] is True
    # score_persistence "0.0" primitive double stays 0.0 (not None)
    assert b["scorePersistence"] == 0.0


def test_strategy_report_field_names_match_java(fixtures_dir):
    rows = readers.read_latest_strategy_report(fixtures_dir / "reports")
    keys = set(rows[0]["report"])
    # a representative slice of the exact Jackson camelCase names
    for k in ("smaTwenty", "smaFifty", "rsi14", "macdSignal", "avgDollarVol20",
              "daysToEvent", "relStrengthSpy", "scoreShiftRaw", "signalBand",
              "reasonSummary"):
        assert k in keys


def test_wheel_report_row_shape(fixtures_dir):
    rows = readers.read_latest_wheel_report(fixtures_dir / "wheel")
    assert len(rows) == 2
    a = rows[0]
    assert len(a) == len(WHEEL_COLUMNS)       # full versioned contract
    assert a["schemaVersion"] == WHEEL_SCHEMA_VERSION
    assert a["runMode"] == "CURRENT_CONTEXT_ONLY"
    assert a["nonoverlapSampleCount"] is None
    assert a["symbol"] == "A"
    assert isinstance(a["horizonDte"], int) and a["horizonDte"] == 7
    assert isinstance(a["sampleCount"], int)
    assert a["lastClose"] == 134.04
    assert a["minCushion20pctItm"] == "5%"    # display string preserved


def test_wheel_report_nullable_blanks(fixtures_dir):
    rows = readers.read_latest_wheel_report(fixtures_dir / "wheel")
    b = next(r for r in rows if r["symbol"] == "BBB")
    # WheelReportReader.dbl / str: blank -> None
    assert b["daysToEvent"] is None
    assert b["scoreTotal"] is None
    assert b["signalBand"] is None
    assert b["sector"] is None
    assert b["horizonDte"] == 37


def test_latest_dated_csv_respects_today(fixtures_dir, tmp_path):
    # a future-dated file must be ignored
    d = tmp_path / "wheel"
    d.mkdir()
    (d / "2099-01-01.csv").write_text("symbol\nX\n")
    src = (fixtures_dir / "wheel" / "2026-07-16.csv").read_text()
    (d / "2026-07-16.csv").write_text(src)
    picked = readers._latest_dated_csv(d, today="2026-07-16")
    assert picked.name == "2026-07-16.csv"


def test_wheel_reader_rejects_unsupported_future_schema(tmp_path):
    path = tmp_path / "future.csv"
    row = {column: "" for column in WHEEL_COLUMNS}
    row.update({"schema_version": "999", "run_mode": "CURRENT_CONTEXT_ONLY", "symbol": "X"})
    path.write_text(
        ",".join(WHEEL_COLUMNS) + "\n" +
        ",".join(row[column] for column in WHEEL_COLUMNS) + "\n"
    )

    with pytest.raises(ValueError, match="unsupported wheel schema version"):
        readers.read_wheel_report_rows(path)


def test_wheel_reader_rejects_previous_schema(tmp_path):
    path = tmp_path / "previous.csv"
    row = {column: "" for column in WHEEL_COLUMNS}
    row.update({"schema_version": "2", "run_mode": "CURRENT_CONTEXT_ONLY", "symbol": "X"})
    path.write_text(
        ",".join(WHEEL_COLUMNS) + "\n" +
        ",".join(row[column] for column in WHEEL_COLUMNS) + "\n"
    )

    with pytest.raises(ValueError, match="unsupported wheel schema version"):
        readers.read_wheel_report_rows(path)
