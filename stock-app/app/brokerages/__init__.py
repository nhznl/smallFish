"""Brokerage-agnostic read layer.

```
router -> registry.resolve(brokerage_id) -> provider adapter -> canonical facts
                                         -> common projections -> API response
```

Adapters translate; projections calculate; routers serialize. Nothing above the
registry may branch on which brokerage it is looking at.
"""

from .contracts import (ACTIONS, COVERAGE_STATUSES, INSTRUMENTS,
                        MISSING_REASONS, OPTION_TYPES, AccountCapitalFact,
                        AccountRef, ActivityFact,
                        BrokerageCapabilities, BrokerageCoverage,
                        BrokerageDescriptor, BrokerageSnapshot,
                        MarketObservation, OptionContract, PositionFact,
                        Provenance)
from .registry import (REGISTRY, UnknownBrokerageError, brokerage_ids,
                       descriptors, registration, resolve)

__all__ = [
    "ACTIONS", "COVERAGE_STATUSES", "INSTRUMENTS", "MISSING_REASONS",
    "OPTION_TYPES", "REGISTRY", "AccountCapitalFact", "AccountRef", "ActivityFact",
    "BrokerageCapabilities", "BrokerageCoverage", "BrokerageDescriptor",
    "BrokerageSnapshot", "MarketObservation", "OptionContract", "PositionFact",
    "Provenance", "UnknownBrokerageError", "brokerage_ids", "descriptors",
    "registration", "resolve",
]
