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

Importers may call ``services/`` for transport, but never a provider-specific
transport package: provider selection and provider symbol syntax stay behind the
neutral service API.
"""
