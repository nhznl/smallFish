# Archived TradingView export contract

This study was closed on 2026-08-25 without TradingView parity or a holdout.
Do not obtain a new export, run the comparator, or create parity evidence for
`rsi-supertrend-pine-v1`. The contract below is retained only to document the
uncompleted preregistered gate.

Use an **external** daily TradingView strategy export CSV with the same symbol,
daily timeframe, price adjustment, session, and frozen inputs (RSI 10, SMA 10,
SuperTrend ATR 10 factor 2.5).

Required OHLC/indicator columns:

`date,open,high,low,close,rsi,rsi_signal,st_direction,special_buy`

Holdout also requires fill columns:

`entry_fill,exit_fill`

Required chart identity (CLI, sidecar, or constant CSV columns):

`symbol`, `timeframe` (must be daily / `1D`), `adjustment`, `session`

Sidecar example (`tradingview_export.meta.json` next to the CSV):

```json
{
  "symbol": "SPY",
  "timeframe": "1D",
  "adjustment": "adjusted",
  "session": "NYSE"
}
```

Dates must be sorted and unique. The series must be long enough for RSI, SMA,
and SuperTrend warm-up. The comparator checks identical defined/undefined masks,
bidirectional entry/exit date sets, and fill-price differences.

Prefer keeping the CSV outside the repository. The default path
`tradingview_export.csv` in this directory is gitignored so it does not dirty
the worktree.

The original compare-only command would have written a creation-only,
content-addressed parity report, but it was never executed:

```bash
./commands.sh rsi-supertrend-study \
  --compare-tradingview \
  --tradingview-export /absolute/path/to/export.csv \
  --tv-symbol SPY --tv-timeframe 1D \
  --tv-adjustment adjusted --tv-session NYSE
```

The planned holdout would have sealed an approved report inside the
authoritative claim directory. Neither artifact exists. Python
self-consistency was not represented as TradingView parity.
