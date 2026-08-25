# Why we keep full price history (2020→) and where each year is consumed

**Context:** the price cache under `data/{year}/{SYMBOL}.txt` goes back to
2020. Because many stocks pay dividends / split, the audit/backfill
(`utilities/audit_price_cache.py`) re-pulls and rewrites a symbol's whole history
whenever its adjustment vintage drifts — which raised a fair question: do we even
need 2020 anymore, and how many years are actually required?

**Short answer:** keep everything from 2020. Trimming saves megabytes and one
slightly-smaller HTTP response per rewrite, while deleting exactly the data that
makes the wheel's assignment-risk numbers honest.

---

## Where each year of history is actually consumed

| Consumer | What it computes | History it needs |
|---|---|---|
| Trend analysis | SMA20/50, RSI14, momentum | ~100 sessions (~5 months) |
| `YearlySlope` heatmap (stock detail page) | weekly/monthly slope per year | **every year kept** — the heatmap shrinks if you trim |
| Strategy swing scan | indicator warmup (SMA50, MACD) | current + prior year |
| Wheel: RV7/21/37, 1σ moves | rolling vol windows | ≤ 37 sessions |
| Wheel: `rv_percentile_252` | today's vol vs its own trailing year | ~273 sessions (~1.3 years) |
| Wheel: discontinuity guard | ≥ 300 clean sessions to be scannable | ~1.2 years |
| Wheel: **expiry-ITM & touch frequencies** | how often a cushion got breached over N sessions | **every year available** — the big one |
| Phase 3: beta, risk dashboard | 252 aligned sessions vs SPY | ~1 year |
| Backtests (`backtest.py`, `event_backtest.py`) | research | multi-year, one-off |

The strict **mechanical floor is ~2 years** — with current + prior year plus a bit
more, every indicator, `rv_percentile_252`, and the ≥300-clean-session guard all
run. But the mechanical floor is not the *useful* floor.

## Why the frequencies are the deciding factor

The wheel's core risk estimates — **expiry-ITM frequency** and **touch frequency**
per cushion — are computed over *every historical N-session window in the cache*.
They answer "if I'd sold a put 5% out, how often did it finish (or trade) in the
money over the next ~26 sessions?" More history = more windows = a distribution
that spans more market regimes.

**2020 and 2022 are the only bear/crash regimes in the cache** (COVID crash,
2022 bear). If you trim to 2024+, every frequency is estimated from bull-market
data only. Puts would look far safer than they are — which is precisely the
failure mode for a put seller, whose whole risk is the downside tail. The
Wheel artifacts deliberately ship `sample_count` and `history_start` alongside
every frequency *because mixed regimes are the point*.

The `YearlySlope` heatmap on the stock-detail page also literally renders one
column per year of data, so trimming visibly shortens it.

## Why keeping the history is cheap (the frequent-rewrite premise, corrected)

The worry was that dividends/splits force frequent full-history rewrites, so fewer
years = less churn. That premise doesn't actually scale with years kept:

- **Requests:** a full-history yfinance pull is **one HTTP call per symbol**
  whether it covers 2 years or 6 — the whole daily range comes back in a single
  request. Rewriting 2020→2026 costs the same one request as 2024→2026; the
  payload is just a few hundred KB larger.
- **Frequency:** with event-driven repair, a symbol rewrites only when *it* pays
  a dividend or splits — for a
  typical dividend payer, ~4 single-request rewrites a year. Across ~3,000
  symbols that's a light, self-spreading trickle, not a bulk job.
- **Disk:** the whole ~7-year cache is on the order of a few hundred MB. Trimming
  saves megabytes.

So retention costs one slightly-larger response per corporate action plus some
disk; the benefit is regime-complete risk statistics and a full heatmap.

## If a trim is ever forced

Floor is **~2 years** to keep everything running mechanically. Treat that as
*degrading the risk stats* (frequencies estimated from a single regime), not as a
free cleanup. Prefer archiving old years offline over deleting them.

---

## Related: what the rewrite actually changes (the adjusted-price gotcha)

The audit rewrites prices, not because "the price that day" changed, but because
yfinance with `auto_adjust=True` returns **split- and dividend-adjusted** prices
expressed in *today's* basis. Every dividend re-scales all prior history slightly
downward, so an incrementally-written cache ends up with rows frozen at different
adjustment vintages ("mixed vintage"). Example seen in practice:

- **NVDA** 2020–21 rows were stuck pre-split — off by exactly **10×** (cached
  close 59.98 vs correctly-adjusted 5.96) after the June-2024 10:1 split.
- **AAPL** (no split since 2020) still drifted **−3.7%** on its Jan-2020 rows
  from ~6 years of accumulated quarterly dividends; even a Jan-2026 row was
  −0.18% out of basis once two 2026 dividends landed.

The one-time backfill (2026-07-16) rewrote **~48% of the cache** (1,494 of 3,112
symbols) for exactly this reason. Whole-history rewrite (not just the offending
year) is deliberate: patching only some years leaves a seam where two adjustment
bases meet, and a log return computed across that seam is a phantom jump that
corrupts RV, touch, and ITM math — which are built almost entirely out of
day-to-day returns and high/low ranges.

**Caveat:** adjusted-basis history means the cache never shows the *nominal*
traded price for old dates. That's correct for analytics (the whole system
assumes it), but "what did I actually pay in 2020" is the broker's number, not
this cache's.

## Ongoing protection (so drift doesn't re-accumulate)

- `auto_adjust=True` is pinned explicitly in the price-retrieval workflow,
  so every newly-appended row shares one convention at write time.
- **Event-driven repair:** the scraper's daily fetch returns `Dividends` /
  `Stock Splits` fields; when nonzero for a symbol, it invokes
  `audit_price_cache` to rewrite just that symbol. There is no extra Yahoo
  traffic on normal days and repairs occur when a corporate action happens.
- The wheel scan's per-scan discontinuity guard (§4.2) is the backstop: any
  residual > 0.30 single-day log jump excludes the symbol with a reason rather
  than scanning corrupt data.
