from pathlib import Path

from app import config


def test_runtime_roots_derive_artifact_paths(tmp_path: Path, monkeypatch):
    data = tmp_path / "cache"
    logs = tmp_path / "logs"
    monkeypatch.setenv("SFP_DATA_DIR", str(data))
    monkeypatch.setenv("SFP_LOG_DIR", str(logs))

    assert config.price_cache_root() == data
    assert config.logs_dir() == logs
    assert config.universe_csv() == data / "universe.csv"
    assert config.retired_symbols_csv() == data / "retired_symbols.csv"
    options_dir = data / "ledger_options"
    assert config.options_activity_csv() == options_dir / "options_activity.csv"
    assert config.options_position_marks_csv() == options_dir / "options_position_marks.csv"
    assert config.options_greeks_csv() == options_dir / "options_greeks.csv"
    assert config.options_betas_csv() == options_dir / "options_betas.csv"
    assert config.reports_dir() == data / "reports" / "pre_earnings_momentum"
    assert config.wheel_dir() == data / "wheel"
    assert config.premiums_dir() == data / "premiums"
    assert config.events_csv() == data / "events.csv"


def test_individual_fixture_override_wins(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path / "data"))
    override = tmp_path / "fixture-universe.csv"
    monkeypatch.setenv("SFP_UNIVERSE_CSV", str(override))
    assert config.universe_csv() == override
    retired_override = tmp_path / "fixture-retired.csv"
    monkeypatch.setenv("SFP_RETIRED_SYMBOLS_CSV", str(retired_override))
    assert config.retired_symbols_csv() == retired_override


def test_options_activity_excluded_symbols_are_normalized(monkeypatch):
    monkeypatch.setenv("SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS", " btu,JOBY, btu ")
    assert config.options_activity_excluded_symbols() == {"BTU", "JOBY"}
