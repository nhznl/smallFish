# TradingView export slot

Use an **external** daily TradingView strategy export CSV with the same symbol,
daily timeframe, price adjustment, session, and frozen inputs (RSI 10, SMA 10,
SuperTrend ATR 10 factor 2.5).

Required columns:

`date,open,high,low,close,rsi,rsi_signal,st_direction,special_buy`

Holdout also requires fill columns:

`entry_fill,exit_fill`

Dates must be sorted and unique. The series must be long enough for RSI, SMA,
and SuperTrend warm-up. The comparator checks identical defined/undefined masks,
bidirectional entry/exit date sets, and fill-price differences.

Prefer keeping the CSV outside the repository. The default path
`tradingview_export.csv` in this directory is gitignored so it does not dirty
the worktree.

Compare and write a durable parity report:

```bash
./commands.sh rsi-supertrend-study \
  --compare-tradingview \
  --tradingview-export /absolute/path/to/export.csv
```

Holdout will not run without a passing report from `--tradingview-export`.
Python self-consistency is not TradingView parity.
