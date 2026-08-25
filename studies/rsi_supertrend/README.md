# RSI/SuperTrend Pine replication

Operational notes for `rsi-supertrend-pine-v1`. Methodology lives in
[`rsi_supertrend_study_spec.md`](rsi_supertrend_study_spec.md) and must not be
edited.

**This study is closed with no verdict.** The owner reviewed the completed
1999–2021 development evidence on 2026-08-25 and declined to run the 2022–2025
holdout. The holdout remains unclaimed and must never be executed under this
study ID. TradingView parity was not completed, and no result was published to
the Research Studies catalog.

The verified creation-only development evidence is under
`$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/development/20260825T205046Z-22ef9f8/`.
The primary mean daily excess return was `-0.0002687539`, with a 95% interval of
`[-0.0004224153, -0.0000917301]`. Pine and shared-TA produced identical primary
fills and outcomes. These are unfavorable development diagnostics, not a
confirmatory verdict.

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

`manifest.json` hashes every file and records providers `pine` and `shared_ta`.

The top-level per-symbol indicator differences cover only the registered
evaluation window. Causal history loaded before that window is retained in the
JSON under `causal_pre_window_indicator_diagnostics` and in explicitly prefixed
CSV columns; it is never silently mixed into the evaluation-window counts.

The command that produced the retained development evidence was:

```bash
./commands.sh rsi-supertrend-study --window development --include-stocks
```

## Archived runner

The runner and verification helpers remain for reproducibility and code history.
Do not start another development run, TradingView comparison, or holdout run for
this closed study ID.

The coverage command inspects file presence and OHLCV validity only. It does
not compute signals, trades, equity, or inference.

Runs write creation-only directories under `$SFP_DATA_DIR/studies/rsi-supertrend-pine-v1/`
(gitignored). Do not commit prices or stock-level position artifacts.

This study is not in the published catalog. `stock-app/` must not import it.

## Source

[`source.pine`](source.pine) is the pasted executable Pine. Do not retune its
inputs.

A TradingView development export is not in the repository. Pine parity was not
claimed from self-consistency alone and is no longer planned for this closed
study.
