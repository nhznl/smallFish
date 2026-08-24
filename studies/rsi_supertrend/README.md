# RSI/SuperTrend Pine replication

Operational notes for `rsi-supertrend-pine-v1`. Methodology lives in
[`rsi_supertrend_study_spec.md`](rsi_supertrend_study_spec.md) and must not be
edited.

**Holdout is not authorized.** Do not run the holdout command until independent
Stage 1 review is accepted. The official holdout command, **not executed**, is:

```bash
./commands.sh rsi-supertrend-study --window holdout --confirm-holdout --include-stocks
```

`--include-stocks` is required for holdout. The stock cohort is labeled
`EXPLORATORY` and survivorship-biased; it is never pooled into the primary
verdict. The holdout is reserved atomically under
`$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/holdout/.authoritative-claim`
before any holdout calculation, independent of `--output-root`.

## Runner

```bash
./commands.sh rsi-supertrend-study --window development
./commands.sh rsi-supertrend-study --validate-coverage
./commands.sh rsi-supertrend-study --validate-coverage --coverage-start 2022-01-01 --coverage-end 2025-12-31
```

The coverage command inspects file presence and OHLCV validity only. It does
not compute signals, trades, equity, or inference.

Runs write creation-only directories under `$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/`
(gitignored). Do not commit prices or stock-level position artifacts.

This study is not in the published catalog. `stock-app/` must not import it.

## Source

[`source.pine`](source.pine) is the pasted executable Pine. Config and
`PINE_SHA256` hash that file. The spec freeze lists a different digest; the
owner waived that gate for Stage 1. Do not retune inputs.

A TradingView development export is not in the repository. See
[`fixtures/README.md`](fixtures/README.md). Pine parity cannot be claimed from
self-consistency alone.
