# RSI/SuperTrend Pine replication

Operational notes for `rsi-supertrend-pine-v1`. Methodology lives in
[`rsi_supertrend_study_spec.md`](rsi_supertrend_study_spec.md) and must not be
edited.

**Holdout is not authorized and is currently blocked in code.** The paired
Pine/shared-TA outcome runner and artifacts must be implemented and independently
reviewed, and a real TradingView development export must pass strict parity.
The eventual holdout command, **not executed**, is:

```bash
./commands.sh rsi-supertrend-study \
  --window holdout --confirm-holdout --include-stocks \
  --tradingview-export /absolute/path/to/tradingview_export.csv \
  --tv-symbol SPY --tv-timeframe 1D \
  --tv-adjustment adjusted --tv-session NYSE
```

`--include-stocks`, `--tradingview-export`, and TradingView identity
(`--tv-symbol`, `--tv-timeframe`, `--tv-adjustment`, `--tv-session`, or a
sidecar / constant CSV columns) are required for holdout. Keep the export
outside the git worktree so the clean-worktree guard still passes.

Holdout flow: refuse if the claim already exists, compare the export in memory,
atomically create
`$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/holdout/.authoritative-claim`,
and write a creation-only `tradingview_parity.json` **inside that claim**. A
later attempt cannot overwrite the sealed report. The claim and run manifest
hash it.

Compare-only runs write a content-addressed report under
`$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/parity/<fixture_sha256>.json`
(also creation-only).

The stock cohort is labeled `EXPLORATORY` and survivorship-biased; it is never
pooled into the primary verdict.

## Implementation sensitivity

The indicator layer supports `pine` and `shared_ta`. The latter calls
`utilities.indicators.ta` directly with the same RSI(10), RSI-SMA(10), and
ATR(10) parameters, then supplies the shared ATR to the same SuperTrend
recurrence. Pine remains primary; shared-TA results will be labeled
`IMPLEMENTATION_SENSITIVITY` and cannot change the primary verdict.

Only indicator calculation is implemented in this phase. Paired cohort outcome
artifacts are not yet implemented, so the runner refuses every holdout attempt
before creating a claim or parity evidence.

## Runner

```bash
./commands.sh rsi-supertrend-study --window development
./commands.sh rsi-supertrend-study --validate-coverage
./commands.sh rsi-supertrend-study --validate-coverage --coverage-start 2022-01-01 --coverage-end 2025-12-31
./commands.sh rsi-supertrend-study \
  --compare-tradingview \
  --tradingview-export /path/to/export.csv \
  --tv-symbol SPY --tv-timeframe 1D \
  --tv-adjustment adjusted --tv-session NYSE
```

The coverage command inspects file presence and OHLCV validity only. It does
not compute signals, trades, equity, or inference.

Runs write creation-only directories under `$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/`
(gitignored). Do not commit prices or stock-level position artifacts.

This study is not in the published catalog. `stock-app/` must not import it.

## Source

[`source.pine`](source.pine) is the pasted executable Pine. Do not retune its
inputs.

A TradingView development export is not in the repository. See
[`fixtures/README.md`](fixtures/README.md). Pine parity cannot be claimed from
self-consistency alone.
