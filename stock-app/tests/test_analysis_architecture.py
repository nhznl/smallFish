"""Enforce the ``analysis/`` dependency allowlist.

``analysis/`` is shared by both Python runtimes, so it must stay dependency
light: standard library, NumPy, and ``models`` only, plus intra-package
imports. It must not import FastAPI, pandas, study code, the FastAPI ``app``
package, or ``utilities``. See the dependency-direction rules in
``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import analysis

PACKAGE_DIR = Path(analysis.__file__).resolve().parent

ALLOWED_ROOTS = (
    set(sys.stdlib_module_names)
    | {"__future__", "numpy", "models", "analysis"}
)

FORBIDDEN_ROOTS = {"fastapi", "pandas", "app", "utilities", "studies", "requests", "httpx"}


def _import_roots(source: str) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                roots.add("analysis")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _analysis_modules() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def test_analysis_has_modules_to_check():
    names = {path.name for path in _analysis_modules()}
    assert {"__init__.py", "numeric.py", "trend.py", "ema_crossover.py", "stock.py"} <= names


def test_analysis_imports_only_allowed_roots():
    offenders: dict[str, set[str]] = {}
    for path in _analysis_modules():
        roots = _import_roots(path.read_text(encoding="utf-8"))
        disallowed = roots - ALLOWED_ROOTS
        if disallowed:
            offenders[path.name] = disallowed
    assert not offenders, f"analysis modules import disallowed roots: {offenders}"


def test_analysis_imports_no_forbidden_roots():
    offenders: dict[str, set[str]] = {}
    for path in _analysis_modules():
        roots = _import_roots(path.read_text(encoding="utf-8"))
        hit = roots & FORBIDDEN_ROOTS
        if hit:
            offenders[path.name] = hit
    assert not offenders, f"analysis modules import forbidden roots: {offenders}"


def test_importing_analysis_does_not_load_heavy_frameworks():
    # A subprocess import proves analysis pulls in neither FastAPI nor pandas.
    import subprocess

    code = (
        "import importlib, sys\n"
        "for name in ('analysis.numeric', 'analysis.trend', "
        "'analysis.ema_crossover', 'analysis.stock'):\n"
        "    importlib.import_module(name)\n"
        "loaded = set(sys.modules)\n"
        "assert 'fastapi' not in loaded, 'analysis pulled in fastapi'\n"
        "assert 'pandas' not in loaded, 'analysis pulled in pandas'\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(PACKAGE_DIR.parent),
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
