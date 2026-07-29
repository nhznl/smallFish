"""Common projections: every brokerage resource, computed once.

A projection takes canonical facts and returns a response body. It never learns
which brokerage produced the facts, and no formula in here has a second copy in
an adapter, a router, or Angular.
"""

from . import components, envelope, holdings, option_adjusted_basis, options

__all__ = ["components", "envelope", "holdings", "option_adjusted_basis", "options"]
