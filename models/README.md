# Shared models

This package owns data contracts shared by the batch utilities and FastAPI.
Models may define standard-library dataclasses, enums, field ordering, parsing,
serialization, and basic validation.

Models must not contain:

- pandas, numpy, requests, yfinance, FastAPI, or Pydantic dependencies
- network or filesystem discovery
- strategy, trend, or risk algorithms
- imports from `utilities` or `stock-app/app`

Each model is added only after its existing file and API consumers have been
inventoried and its exact fields, units, optionality, and compatibility behavior
have been reviewed.

## Why the restriction

`models/` is the one package every other component may import — the batch
pipeline, the API, and the study runtime all depend on it. Keeping it
standard-library-only is what allows `utilities/` and `stock-app/` to run in
separate virtual environments. A single pandas import here would force both
environments to converge. See
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## Compatibility

These are wire and file formats with live consumers: generated artifacts on
disk, and JSON the Angular client already parses.

- Adding an optional field is safe.
- Renaming or removing a field, changing a unit, or changing a type is a
  breaking change. It needs an issue first, and every reader and writer updated
  in the same change.
- Where a format carries an explicit schema name and version (for example
  `smallfish.options-activity`), bump the version and keep the reader accepting
  the versions it supports. Do not change a payload's meaning while leaving its
  version alone.

## Tests

Every model needs coverage for parsing, serialization round-trips, validation
failures, and optional-field handling. Model tests live with the suite that owns
the consumer, so a change here means running **both** Python suites:

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
```
# Materialized research-study contract

`study.py` defines the standard-library-only validation boundary for JSON
records published under `data/studies/`.  The materialized JSON is the
authoritative API contract: readers accept unknown fields for additive,
forward-compatible changes, while a breaking semantic or required-field change
requires a new schema version and compatible readers for every version still
published.
