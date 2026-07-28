from pathlib import Path

import pytest

from app.path_security import UnsafePathError, contained_path, symbol_year_path


def test_contained_path_accepts_a_child(tmp_path: Path):
    assert contained_path(tmp_path, "2026", "AAPL.txt") == (
        tmp_path / "2026" / "AAPL.txt"
    )


@pytest.mark.parametrize("relative", ("../outside.txt", "/tmp/outside.txt"))
def test_contained_path_rejects_escape_attempts(tmp_path: Path, relative: str):
    with pytest.raises(UnsafePathError):
        contained_path(tmp_path, relative)


def test_contained_path_rejects_a_symlink_escape(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError):
        contained_path(root, "link", "secret.txt")


def test_symbol_year_path_normalizes_symbols_and_rejects_traversal(tmp_path: Path):
    assert symbol_year_path(tmp_path, "brk.b", 2026) == tmp_path / "2026" / "BRK-B.txt"
    with pytest.raises(UnsafePathError):
        symbol_year_path(tmp_path, "../../etc/passwd", 2026)
