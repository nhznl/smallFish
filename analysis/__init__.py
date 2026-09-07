"""Shared, dependency-light stock-analysis calculations.

Both Python runtimes import this package. It depends only on the standard
library, NumPy, and ``models``; it must not read files, configuration, the
network, or the wall clock without an explicit injected argument, and it must
not import FastAPI, pandas, or study code. See the dependency-direction rules
in ``docs/ARCHITECTURE.md``.
"""
