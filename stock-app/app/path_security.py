"""Validated construction of paths below trusted application roots."""

from __future__ import annotations

import os
from pathlib import Path

from models.universe import normalize_symbol


class UnsafePathError(ValueError):
    """A requested path would escape its configured root."""


def contained_path(root: Path, *parts: str) -> Path:
    """Return a normalized child of ``root`` or reject a traversal attempt.

    ``realpath`` runs before the prefix check so ``..`` segments and symlinks
    cannot escape. The prefix keeps its trailing separator so a sibling whose
    name merely begins with the root name is rejected.
    """
    normalized_root = os.path.realpath(os.fspath(root))
    prefix = normalized_root if normalized_root.endswith(os.sep) else normalized_root + os.sep
    candidate = os.path.realpath(os.path.join(normalized_root, *parts))
    if not candidate.startswith(prefix):
        raise UnsafePathError("Path is outside the configured root.")
    try:
        contained = os.path.commonpath((normalized_root, candidate)) == normalized_root
    except ValueError as exc:
        raise UnsafePathError("Path is outside the configured root.") from exc
    if not contained:
        raise UnsafePathError("Path is outside the configured root.")
    return Path(candidate)


def symbol_year_path(cache_root: Path, symbol: str, year: int) -> Path:
    """Return the cache file for a validated registry symbol and calendar year."""
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise UnsafePathError("Symbol is invalid.")
    if not isinstance(year, int) or not 1900 <= year <= 9999:
        raise UnsafePathError("Year is invalid.")
    return contained_path(cache_root, str(year), f"{normalized}.txt")
