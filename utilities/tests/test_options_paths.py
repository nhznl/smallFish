from pathlib import Path

import pytest

from utilities.options.paths import strategy_data_root


def test_strategy_data_root_preserves_default_and_relative_precedence(tmp_path):
    assert strategy_data_root(tmp_path, {}) == (tmp_path / "data").resolve()
    assert strategy_data_root(
        tmp_path, {"strategy_data_root": "nested/../artifacts"}
    ) == (tmp_path / "artifacts").resolve()


def test_strategy_data_root_preserves_absolute_precedence(tmp_path):
    configured = (tmp_path / "absolute" / "../artifacts").absolute()
    unrelated_root = tmp_path / "ignored"

    assert strategy_data_root(
        unrelated_root, {"strategy_data_root": str(configured)}
    ) == configured.resolve()


def test_strategy_data_root_preserves_invalid_value_error(tmp_path):
    with pytest.raises(TypeError):
        strategy_data_root(tmp_path, {"strategy_data_root": None})
