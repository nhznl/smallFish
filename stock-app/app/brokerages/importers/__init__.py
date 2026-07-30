"""Brokerage importers: provider responses -> materialized artifacts.

An importer owns one resource's normalization, artifact schema, and write path
for one provider family. It is the only layer allowed to turn a provider
response into a row on disk; adapters read those rows back and never call a
provider themselves.

```
registry resource command -> importer -> artifact CSV -> adapter -> canonical facts
```

Modules:

* ``snaptrade`` — SnapTrade holdings and option-activity materialization.
* ``held_option_market_data`` — beta and Greeks for currently held option legs,
  read through the provider-neutral ``services.options_market`` API.

An importer may call ``services/`` for the account transport of its own provider
family — ``snaptrade`` reads Fidelity through ``services.snaptrade``, exactly as
``options_activity`` reads Tastytrade accounts through ``services.tastytrade``.
What no importer may hold is *market-data* transport or provider symbol syntax:
provider selection, quotes, Greeks/IV, beta, and OCC-to-dxFeed conversion stay
behind ``services.options_market``.

``tests/test_brokerage_architecture_enforcement.py`` enforces both halves.
"""
