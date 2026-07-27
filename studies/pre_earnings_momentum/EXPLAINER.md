# Understanding the pre-earnings momentum backtest

A plain-language explainer of what the historical strategy study is, why it is
built the way it is, and how its verdict gets decided. Distilled from a design
discussion on 2026-07-18. The binding technical version of everything here is
`backtest_spec.md`; this document explains the *why*. See `README.md` for the
current package status, architecture, and remediation summary.

---

## 1. The problem: you have the outcomes, not the knowledge

The frozen strategy enters a stock 2–5 weeks before its earnings date and exits the
session before the report ("T-1"). To backtest it you need to know, for every
historical Friday, **what a trader believed the earnings date was on that
Friday** — not what it eventually turned out to be.

All the cheap data sources (Yahoo, Finnhub history) record the *final* date.
Earnings dates move: companies confirm, reschedule, and delay them — and
delays correlate with bad news. Example: in March, Acme's report is scheduled
for May 2. Your scanner enters in March planning to exit May 1. In April, Acme
delays to May 20. If you backtest with today's data, you see only "May 20" and
simulate a trader who serenely entered relative to May 20 and exited May 19 —
a trader who never existed. The real trader entered on different days and got
surprised mid-trade. Backtesting on final dates therefore quietly assumes
clairvoyance, and the assumption is *biased in your favor*, because "the date
held steady" is itself good news.

The same problem applies to the universe: today's `universe.csv` contains
today's survivors. Stocks that were delisted since 2021 — disproportionately
the losers — are invisible, which flatters every historical measurement
(**survivorship bias**). We cannot fix this without buying effective-dated
membership data, so the study instead *labels* it: every result carries the
qualifier "on the surviving universe."

## 2. The workaround: forecast the date the way you could have back then

The key realization: **past realized dates are legitimate point-in-time
knowledge.** On any historical Friday, everything the company had already
reported was public. What you may not use is anything at or after that Friday.

So the study forecasts each upcoming earnings date from the stock's own past
cadence — the "anniversary rule": most US companies report in the same week of
the quarter, often the same weekday, year after year. The prediction is
`(the event ~4 quarters ago) + 364 days` (364 = 52 weeks, preserving the
weekday).

Then the simulation **trades on the forecast and lets the realized date decide
the outcome**. The switch is an exact date comparison — there is no "close
enough" tolerance. Let `planned_exit` be the last session strictly before the
*predicted* date (your intended T-1). Then:

- Realized report **on or before `planned_exit`** → you were still holding when
  the report hit; the simulation makes you eat the post-report gap (exit at the
  close of the first session after the report). Deliberately pessimistic.
- Realized report **after `planned_exit`** — which includes it landing exactly
  on the predicted date, arriving later, or there being no realized event at
  all → you reach the planned T-1 exit as intended, harmlessly missing any
  run-up past it.

So a `T1_PLANNED` exit does not mean "the forecast was right"; it means "the
report did not arrive on or before your planned exit." A report that came
*later* than predicted is indistinguishable from a perfect forecast at the exit.

Forecast error is now *inside* the results instead of assumed away. And since
the live system uses actual Finnhub estimates (better than an anniversary
rule), the simulation understates the live system's information — a
conservative bias, which is the direction you want.

### Where a trade's prices come from

Nothing fills at the Friday close you decided on — that would be look-ahead.
Every fill is one bar later or at a later session:

- **Entry** = a limit-on-open for the next session, capped at the exact
  decision-session close plus 3%. If the next open is at or below the limit,
  the fill is that opening price. If it is above the limit, the order is
  cancelled immediately with no retry or replacement; the cash waits for the
  next weekly scan. A ticker missing either the decision-session bar or the
  next-session bar is skipped.
- **Planned exit (`T1_PLANNED`)** = the *close* of `planned_exit`.
- **Report beat your exit (`EARLY_REPORT`)** = the *close* of the first session
  after the realized report.

The final configuration has **no protective stop**. `STOP` and `STOP_GAP`
remain historical simulator paths from the rejected 2.5×ATR experiment, not
paths expected in the frozen holdout.

Transaction costs of 10 bps per side are applied to the *return*, not baked
into these prices — which is why every trade row carries both `ret_gross` and
`ret_net`, about 20 bps apart.

### The date-consistency gate

A forecast like this only works for metronomic reporters. So there is a gate:
using only pre-decision history, compute how far each past event landed from
its own anniversary prediction; require at least 2 measurable errors among the
last 4, all ≤ 7 days. Erratic reporters, semi-annual reporters, and short
histories fail and are simply never candidates. This gate is also now in the
**live** scanner, on its own merits: a stock whose earnings date you cannot
predict is a stock whose T-1 exit you cannot plan.

## 3. How a Friday scan decides what to pick

The frozen picker has three stages: **gates** decide eligibility,
**days-to-event** supplies a deterministic order, and portfolio allocation
spreads available slots toward the currently least-represented sectors.
Scores are still calculated and reported as diagnostics, but they neither
filter nor order the frozen candidate set. No score can rescue a gate failure.

**Gates:**

- **Price range** $10–$300.
- **Liquidity floors**: 20-day average volume ≥ 4M shares AND 20-day average
  dollar volume ≥ $10M. Below these, your own orders move the price and the
  planned "exit at a specific close" is fiction.
- **Data freshness**: the stock's latest cached bar must be within 3 trading
  sessions of the current session. A stale bar means delisted/halted/broken
  feed; before this gate existed, a stock with a months-old bar competed in
  the ranking on fictional prices (the FCNCA incident).
- **Upcoming event** 2–5 weeks out (in the study: the *predicted* date).
- **Date consistency** (above).

**Diagnostic scores** — five capped groups summing to `score_total` (max 95):

| Group | Max | What it rewards |
|---|---|---|
| trend | 25 | price above rising moving averages, clean stack |
| momentum | 20 | MACD histogram genuinely turning up, healthy RSI, volume |
| extension | 10 | **not** being stretched (see below) |
| event | 30 | more lead time before the (predicted) earnings date |
| tradability | 10 | liquidity headroom (+5) and relative strength (+5) |

**Extension** is the anti-chasing component: it measures how far the price
sits above its own 20-day average and where it sits between the Bollinger
bands, and pays for having *room to run* — near or mildly below the average,
lower half of the bands. A stock 12% above its average, pinned to the upper
band, already made its move; buying it is chasing. Important audit history:
these checks used to be one-sided, so a stock in free fall scored *maximum*
"room." Now more than 10% below the average, or below the lower band, is
classified as a breakdown and earns zero — a falling knife is not a pullback.

**Relative strength** (the 5-point piece of tradability) is frequently
misunderstood, so precisely: at decision time, compare the stock's trailing
**63-session** return with SPY's over the same 63 sessions, both ending at
the decision date. Beat SPY → +5, flat bonus regardless of margin. Lag SPY or
missing data → +0 (nothing is ever subtracted). It is now a *backward-looking
diagnostic feature* — it has nothing to do with the trade's holding period,
which doesn't exist yet at scoring time. The 63 is a hard-coded ~3-month
momentum convention, honestly labeled a heuristic; whether the scoring earns
its points at all is one of the things the study measures.

**Frozen selection:** all gate-passers bypass the score-band filter and are
ordered by `days_to_event` descending, so the longest lead inside the 2–5 week
window comes first. When slots are scarce, the allocator repeatedly takes the
highest-ranked candidate from the sector currently least represented among
open and pending positions. Rank is preserved within a sector, the report cap
remains 10 names per sector, and the portfolio hard cap remains 3 per sector.
This is why a report grouped Tech → Healthcare → Energy cannot fill every slot
from its first one or two industries.

## 4. What SPY is for (four separate jobs)

1. **Market regime** (part of the strategy): SPY vs its 50-day SMA classifies
   Risk-On/Neutral/Risk-Off, which throttles shortlist size and position
   sizing. Fails closed: missing SPY data → treated as risk-off, never
   risk-on.
2. **Relative strength** (part of the strategy): the 63-session comparison
   above.
3. **The trading calendar** (plumbing): SPY's bar dates define which sessions
   existed — needed by the freshness gate and the weekly decision schedule.
   No effect on how success is judged.
4. **The measuring stick** (evaluation): excess return vs SPY is what
   separates "the strategy has an edge" from "stocks went up and anything
   long made money."

Roles 1–2 are *inside* the strategy; a faithful replay must reproduce them
whether or not we like them. Changing them is a strategy redesign, done in the
live scanner first and then re-tested — never quietly in the backtest.

## 5. Portfolio verdict vs per-trade diagnostic

The **verdict** is portfolio-level and matches intuition: start with $100k,
run the strategy with real constraints (max 25 positions, max 3 per sector,
$4,000 fixed base nominal scaled by regime, costs, no leverage), and compare
the resulting equity curve with parking the same $100k in SPY over the same
window. Risk-On entries target $4,000, Neutral $2,400, and Risk-Off/Unknown
$1,200; Risk-Off entries are reduced, not blocked. Whoever ends higher wins
the terminal-return comparison.

Two refinements over the "check once a year" version of that idea:

- **Annual checkpoints are statistically empty.** Four or five yearly
  observations cannot distinguish skill from coin flips. Comparing the daily
  equity curves over the whole window is the same idea using all the
  information.
- **Per-trade matched comparison is a diagnostic, not a verdict.** For each
  trade, the study also computes SPY's return over the *identical
  entry-to-exit sessions* (exactly the holding period — 49 sessions held,
  49 sessions compared). This never decides success. Its job is decomposition
  when the portfolio loses: were the *picks* bad (per-trade excess negative),
  or were picks fine but the strategy sat in cash during rallies (per-trade
  excess positive, portfolio still behind)? The first says fix the scoring;
  the second says fix capital deployment. One portfolio number cannot tell
  you which.

## 6. How success is decided

**Two phases.** Development (2021-07 → 2024-12): look at everything, learn,
compare candidate rules, then freeze. A ~3-month
embargo separates the windows so no trade straddles them. Holdout (2025-04 →
2026-06): run **once** with the frozen configuration; report whatever comes
out. Development results can never declare success — anything tuned on data
looks good on that data.

**Primary bar, both parts required, on the holdout:**

1. **Beat SPY** — terminal portfolio equity above SPY buy-and-hold.
2. **Margin too big to be luck** — resample the daily strategy-minus-SPY
   series in ~month-long blocks 10,000 times; the 95% range of the average
   margin must stay above zero. One 15-month window is a single path; this
   asks how easily the margin could wobble away.

The 100 random-eligible portfolios are an important **diagnostic**, not a
third pass/fail requirement. A high percentile supports the deterministic
selection/allocation rule; a mid-pack result suggests the gated category did
the work. Maximum drawdown, volatility, and return-to-drawdown versus SPY are
important secondary measurements, but cannot rescue a failed primary bar.

**What the random controls actually are.** A common misread is that the
controls reshuffle only positions already opened. They don't. Each Friday the
engine builds the full gate-passing control pool, shuffles it uniformly for
each of 100 seeds, applies the same candidate budget and sector constraint,
then runs the identical entry, sizing, slot, and exit machinery. The control's
terminal figure quoted against the real run is the **median terminal equity
across the 100 seeds**; the percentile is how many of the 100 the real
portfolio beat.

**Why control trade counts can differ.** Only the weekly candidate budget is
matched, never the final number of completed trades. Different ordering changes
which event leads occupy slots, how long those slots remain locked, and what is
available at later decisions. That path dependence can change total trades
even though every portfolio uses identical mechanics.

**Outcome playbook:**

| Holdout result | Reading | Next step |
|---|---|---|
| Beats SPY, CI clear of zero | Primary edge supported (within limits) | inspect risk/control diagnostics; keep prospective archive |
| Beats SPY, CI includes zero | Could be luck | don't size up; let live data accumulate |
| Beats SPY, mid-pack vs controls | Gates worked, ranking didn't | see the retry trap below |
| Loses to SPY, per-trade excess positive | Picks fine, deployment wrong | fix capital usage, not scoring |
| Loses to SPY, per-trade excess negative | Core doesn't work | retire performance claims |

**Even full success is capped**: the claim is "positive excess return in a
survivorship-limited simulation with a naive causal date forecaster" — grounds
for continued live use and prospective tracking, not a "validated edge" banner.

## 7. The retry trap (multiple testing)

Suppose the result is "beats SPY, mid-pack vs random controls" — the ranking
added nothing. Retrying with new ranking logic is legitimate, **but the
holdout is a one-shot instrument and that result spent it.** Its power came
from never influencing your choices; the moment you react to it and test
ranking #2 against the same window, you selected #2 partly because of what
that window said. Try enough rankings against one window and one passes by
chance — this exact disease (tuning and validating on the same data) is what
invalidated the original artifacts.

Correct retry procedure:

1. Design ranking #2 on the **development** window only. Iterate freely there.
2. Freeze it with a dated amendment in the spec.
3. Validate on genuinely fresh data — either the old holdout *explicitly
   downgraded* ("second hypothesis against this window," acceptable once), or
   better, **prospectively**: the weekly Finnhub snapshot archive accumulates
   truly untouched future data that nothing you do today can contaminate.

Also take seriously the null option: if random picks inside the gates perform
as well as ranked picks, the simplest deployable strategy is *gates + random
or equal-weight selection* — fewer parameters, fewer ways to be wrong.

And "mid-pack" is a spectrum: beating 60/100 controls is noise; 90/100 is
suggestive but short; 40/100 means the ranking systematically preferred
*worse* stocks — which is also information.

## 8. Data quirks worth remembering

- **Yahoo's earnings history is deep but rate-limited.** `get_earnings_dates`
  reaches back a decade or more, but sweeping ~1,800 tickers at 0.15s spacing
  trips "Too Many Requests" partway through; fetch in slow, resumable,
  append-mode batches.
- **Finnhub's free calendar has no history at all** (verified empirically:
  past-window queries return empty). Upgrading it would only buy a second
  copy of realized dates — never the revision history the full validation
  needs. What unlocks the real thing is either a vendor selling
  estimate-revision vintages or the prospective archive maturing.
- **SPY had to be backfilled into the price cache** (2020→present, one
  adjustment vintage) because regime, relative strength, calendar, and
  benchmarks all read it; the old workaround file was close-only and
  unadjusted — a documented live/backtest parity defect (P2.3).
- **ETFs have no earnings dates.** "No earnings found" for them during a
  fetch is expected, not an error.
- **ATR14 is the Wilder-smoothed True Range.** True Range per bar is
  `max( high−low, |high−prev_close|, |low−prev_close| )`; ATR14 averages it with
  Wilder smoothing (SMA-seeded). The three terms are not redundant — each is the
  binding one in a different regime: `high−low` when the prior close sits inside
  today's range, `|high−prev_close|` on gap-**ups**, `|low−prev_close|` on
  gap-**downs** (prev close above the whole bar, so the low is the farther
  extreme). Dropping the low term would understate exactly the gap-down
  volatility that delayed / bad-news earnings tend to produce.
- **Why no stop was frozen.** A fixed 10% stop is a
  quiet day for a volatile name and a huge cushion for a sleepy one; scaling by
  ATR would give every position the same *statistical* room. Empirically the
  tested 2.5× band was a median 6.7%
  of entry (82% of trades tighter than 10%), and even so it was destructive at
  this horizon — so a hard 10% would mostly sit *looser* than what was tested
  and would not overturn the verdict that any tight downside stop harvests
  normal pre-earnings noise into losses (spec Finding 3). The study's conclusion
  is *no stop*, not a better-shaped one.

## 9. What development concluded and what is frozen

Development is now concluded. The one-shot holdout configuration is:

- $100,000 starting equity;
- $4,000 base nominal per stock and at most 25 open positions;
- 2–5 weeks before the predicted event;
- longest-event-first ordering (`days_to_event` descending), with score bands
  bypassed;
- least-represented-sector allocation, with at most 3 open/pending positions
  per sector;
- reduced-size Risk-Off/Unknown entries allowed;
- next-session limit-on-open at the exact decision close +3%, with no retry or
  replacement after an opening gap rejection;
- predicted T-1 exit with no protective stop.

This combination is called **LD**: `L` for longest-event-first and `D` for the
diversifying least-represented-sector allocator. With the final 3%
limit-on-open rule, at 2–5 weeks and $4,000/25 it returned 43.28% in development
versus SPY's 35.94%. Its maximum drawdown was 6.62% versus SPY's 24.50%,
annualized volatility was 7.69% versus 18.30%, and return-to-drawdown was 6.53
versus 1.47. It beat 93 of its 100 random controls.

The entry amendment was intentionally narrow: only the user-specified 3%
buffer was evaluated. The selected 2–5 run rejected ten opening orders and
completed 687 trades; every fill was at or below its recorded limit, and the
largest accepted opening gap was 2.97%. The prior unconditional-next-open
version had returned 42.14%, with 6.98% drawdown and a 6.04
return-to-drawdown ratio.

Why this configuration, rather than the single highest backtest cell:

- LD remained stable under the final entry rule as the event-window lower
  bound moved: 43.11%, 43.28%, 40.78%, and 37.59% for 1–5 through 4–5 weeks.
  It beat SPY in all four definitions.
- The 2–5 window had the highest return and control percentile, with shallower
  drawdown than 1–5 and 3–5. The 4–5 window had the shallowest drawdown and
  highest return-to-drawdown ratio, but only a 37.59% return, 63rd control
  percentile, and about 13 names per weekly report. It did not displace 2–5.
- Allowing reduced-size Risk-Off entries beat blocking them for the selected
  configuration and was less path-dependent across adjacent windows.
- $2,500/40 looked diversified on paper but never filled 40 slots; it averaged
  only 15.4 open positions in the selected run, left too much cash idle, and
  lagged SPY in every tested variant.

Two $5,000/20 observations are deliberately retained as **future research
notes**, not competing frozen configurations:

1. In the earlier like-for-like unconditional-next-open sizing grid, the
   $5,000/20 LD version returned more, 44.75%, but its drawdown (8.41%),
   volatility (9.10%), return-to-drawdown (5.32), and control percentile (76)
   were all worse than the corresponding $4,000/25 LD result. The extra return
   did not win the combined risk-adjusted decision. It has not been rerun under
   the later 3% limit rule, so 44.75% is not directly comparable with the final
   43.28% result.
2. At $5,000/20, LR—longest-event-first with simple ranked allocation—had the
   strongest robust absolute-return pattern across event windows: 45.3%,
   42.9%, 48.3%, and 43.6%. That suggests the best allocation can interact
   with position size and slot scarcity. It is worth revisiting with the entry
   rule held constant in a separate future study or with fresh prospective
   data, but not by changing the frozen rule after seeing the one-shot holdout.

Finally, none of the development configurations—including the 3% entry
verification—had a daily excess-return 95% bootstrap interval wholly above
zero. In plain language, the selected strategy beat SPY and did so with much
lower risk in this historical path, but the data cannot yet distinguish a
repeatable excess-return edge from luck. That is exactly why the untouched
holdout existed. Its one-shot result follows.

## 10. What the one-shot holdout said

The committed frozen strategy was run once over 2025-04-04 through 2026-06-26.
It finished at **$122,416 (+22.42%)**, while a fully invested $100,000 in SPY
finished at **$149,174 (+49.17%)**. The strategy therefore trailed by 26.76
percentage points. Its mean daily strategy-minus-SPY return was negative, and
the 95% bootstrap interval was [−0.001337, +0.000041]—not wholly above zero.

That is a clear failure of the study's primary question: this frozen strategy
did not demonstrate repeatable excess return over SPY in fresh data.

There were still useful secondary results:

- Maximum drawdown was 6.31%, versus SPY's 8.88%.
- Annualized volatility was 10.92%, versus SPY's 16.65%.
- Return-to-drawdown was only 3.55, versus SPY's 5.54, because SPY's return was
  so much larger.
- The strategy beat 85 of 100 random-eligible controls. Their median return was
  17.74%, so the deterministic selection was useful inside the category—but
  the entire category remained far behind SPY.

### Why good trades still produced a losing portfolio comparison

The individual trades were respectable: 60.14% won, mean net return was 4.06%,
and mean return versus SPY over the exact same holding sessions was +1.09%.
The problem was deployment. The portfolio was only 47.0% invested on average
and was below 50% invested on 180 of 322 sessions. Its average marked stock
exposure was about $54,800 while SPY was effectively 100% invested throughout
a 49% rally.

So the most defensible diagnosis is:

> The selected stocks generally held their own while owned, but the strategy
> held too much cash to compete with an unusually strong fully invested
> benchmark.

That diagnosis is **not permission to increase sizing and rerun the same
holdout**. Doing so would tune directly to the answer the holdout revealed.
The window is now spent. Any redesigned deployment policy must be developed
under a new pre-registration and judged with genuinely fresh prospective data,
or else be labeled exploratory rather than validated.

The lower drawdown and volatility may still describe a useful conservative
portfolio behavior, but they cannot be marketed as proof of an excess-return
edge. Development returns such as 43.28% are historical research observations,
not validated expectations after this failed holdout.
