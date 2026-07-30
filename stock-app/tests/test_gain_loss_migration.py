"""Gain/loss snapshot migration gating (Phase 21)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import config
from app.brokerages import migration, registry


@pytest.fixture
def brokerage_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    return tmp_path


def test_legacy_files_absent_skips_sync_migration(brokerage_data):
    for entry in registry.REGISTRY.values():
        path = entry.legacy_gain_loss_snapshots_path()
        assert not path.is_file()

    assert migration.legacy_gain_loss_snapshot_files_present() is False
    assert migration.migrate_gain_loss_snapshots_on_sync() is None


def test_legacy_file_present_runs_migration(brokerage_data):
    legacy = config.holdings_gain_loss_snapshots_csv()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "sync_date,retrieved_at,captured_at,account_id,account_name,symbol,gain_loss_pct\n"
        "2026-07-01,2026-07-01T20:00:00Z,2026-07-01T20:05:00Z,acct-1,Legacy,XYZ,7.5\n",
        encoding="utf-8",
    )

    assert migration.legacy_gain_loss_snapshot_files_present() is True
    with patch.object(migration, "migrate_gain_loss_snapshots",
                      return_value={"summary": {"migrated_count": 2}}) as mocked:
        result = migration.migrate_gain_loss_snapshots_on_sync()
    mocked.assert_called_once()
    assert result["summary"]["migrated_count"] == 2
