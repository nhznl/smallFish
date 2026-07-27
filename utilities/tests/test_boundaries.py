"""Architectural gates for the split Python runtimes."""

from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_ROOT = ROOT / "models"
UTILITIES_ROOT = ROOT / "utilities"
FASTAPI_ROOT = ROOT / "stock-app" / "app"


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.partition(".")[0])
    return roots


def python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if ".venv" not in path.parts)


class PackageBoundaryTests(unittest.TestCase):
    def test_root_packages_import_without_install_or_pythonpath(self) -> None:
        self.assertEqual(importlib.import_module("models").__file__,
                         str(MODELS_ROOT / "__init__.py"))
        self.assertEqual(importlib.import_module("utilities").__file__,
                         str(UTILITIES_ROOT / "__init__.py"))

    def test_models_use_only_the_standard_library(self) -> None:
        allowed = sys.stdlib_module_names | {"models"}
        for path in python_files(MODELS_ROOT):
            unexpected = imported_roots(path) - allowed
            self.assertFalse(unexpected, f"{path}: non-stdlib imports {unexpected}")

    def test_utilities_do_not_import_fastapi_application(self) -> None:
        for path in python_files(UTILITIES_ROOT):
            self.assertNotIn("app", imported_roots(path), str(path))

    def test_fastapi_does_not_import_utilities(self) -> None:
        for path in python_files(FASTAPI_ROOT):
            self.assertNotIn("utilities", imported_roots(path), str(path))


if __name__ == "__main__":
    unittest.main()
