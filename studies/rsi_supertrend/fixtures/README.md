# TradingView export slot

Place a daily TradingView strategy export here as `tradingview_export.csv`
when one is available: same symbol, daily timeframe, price adjustment, session,
and the frozen inputs (RSI 10, SMA 10, SuperTrend ATR 10 factor 2.5).

No export is committed. Tests skip this comparison with an explicit
"missing external fixture" message and must not treat Python self-consistency
as TradingView parity.
