# TradingView export slot

Place a daily TradingView strategy export here as `tradingview_export.csv`
using the same symbol, daily timeframe, price adjustment, session, and the
frozen inputs (RSI 10, SMA 10, SuperTrend ATR 10 factor 2.5).

Required columns:

`date,open,high,low,close,rsi,rsi_signal,st_direction,special_buy`

Optional fill columns: `entry_fill`, `exit_fill`.

Compare with:

```bash
./commands.sh rsi-supertrend-study --compare-tradingview
```

No export is committed. A missing file fails closed; Python self-consistency
is not TradingView parity. Stage 2 cannot proceed until a real export matches.
