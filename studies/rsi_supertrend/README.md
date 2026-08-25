# RSI/SuperTrend Pine replication

Operational notes for `rsi-supertrend-pine-v1`. Methodology lives in
[`rsi_supertrend_study_spec.md`](rsi_supertrend_study_spec.md) and must not be
edited.

**Holdout is not authorized and is currently blocked in code.** The paired
Pine/shared-TA outcome runner is implemented and must be independently reviewed,
and a real TradingView development export must pass strict parity, before
holdout authorization. The eventual holdout command, **not executed**, is:

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

The development runner calculates `pine` and `shared_ta` from the same validated
histories in one invocation. Shared-TA calls `utilities.indicators.ta` directly
with the same RSI(10), RSI-SMA(10), and ATR(10) parameters, then supplies the
shared ATR to the same SuperTrend recurrence and the same order emulator. Pine
remains the sole primary implementation and the only result eligible for the
primary verdict. Shared-TA outputs are labeled `IMPLEMENTATION_SENSITIVITY` with
`primary_verdict_eligible: false`. Stock outputs retain the additional
`EXPLORATORY` and survivorship-bias labels and are never pooled into the 14-ETF
endpoint.

Pine artifact names are unchanged. Creation-only shared-TA and comparison files:

| File | Contents |
|---|---|
| `shared_ta_instrument_summary.csv` | Shared-TA primary-cohort instrument summary, including absolute exposure |
| `shared_ta_daily_equity.csv` | Shared-TA primary-cohort daily equity |
| `shared_ta_trades.csv` | Shared-TA primary-cohort trades |
| `shared_ta_summary.json` | Shared-TA primary-cohort summary; not verdict-eligible |
| `implementation_comparison.json` | Per-symbol evaluation-window indicator/fill/outcome diffs, separately labeled causal pre-window diagnostics, and cohort deltas |
| `implementation_comparison_by_symbol.csv` | Flattened per-symbol comparison with explicit evaluation and pre-window scopes |
| `shared_ta_stock_instrument_summary.csv` | Shared-TA stock instrument summary, including absolute exposure (`--include-stocks`) |
| `shared_ta_stock_daily_equity.csv` | Shared-TA stock daily equity |
| `shared_ta_stock_trades.csv` | Shared-TA stock trades |
| `shared_ta_stock_summary.json` | Shared-TA stock summary; `IMPLEMENTATION_SENSITIVITY` and `EXPLORATORY` |
| `stock_implementation_comparison.json` | Stock-only comparison; never mixed with the 14 ETFs |
| `stock_implementation_comparison_by_symbol.csv` | Flattened stock comparison |

`manifest.json` hashes every new file and records providers `pine` and
`shared_ta`. Holdout remains refused before any claim or parity evidence is
created.

The top-level per-symbol indicator differences cover only the registered
evaluation window. Causal history loaded before that window is retained in the
JSON under `causal_pre_window_indicator_diagnostics` and in explicitly prefixed
CSV columns; it is never silently mixed into the evaluation-window counts.

The proposed development command, **not executed** in the implementation pass:

```bash
./commands.sh rsi-supertrend-study --window development --include-stocks
```

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
