# Understanding backtest statistics — from the equity curve to the verdict

A ground-up walkthrough of how the pre-earnings momentum study's statistics
work, written from a teaching session (2026-07-18) that started with one
question: *"my portfolio changes every week — how can it even have a daily
return?"* Companion to
`studies/pre_earnings_momentum/EXPLAINER.md` (which explains the
strategy itself) and the decision record
`studies/pre_earnings_momentum/backtest_spec.md`.

All concrete numbers below are from the real artifacts under
`data/backtest/pre_earnings_momentum/strategy_study/` (frozen config
`development_ld_loo3` and `holdout`).

---

## 1. The portfolio is a pot of money, not a list of stocks

The backtest never measures "the return of a basket of stocks." It measures
one pot of money — like a brokerage account balance:

```
equity(day) = cash + Σ (shares held × that day's closing price)
```

That daily number is the **equity curve** (`equity.csv`, one row per trading
day). "Equity" here means *account value* (cash + positions), not "equities"
as in stocks.

Key consequences:

- **Trades don't create or destroy value at the moment of trading.** Buying
  converts cash into shares at market price; selling converts back. The pot's
  total is unchanged (minus the ~10 bps transaction cost). Only *price moves*
  change the total.
- So the equity curve is one continuous series even though the holdings churn
  completely over time. Composition is irrelevant to the measurement.
- **Cash drag is measured automatically.** On a day the pot is 50% cash and
  the market moves +2%, the pot moves ~+1%. This is not a footnote — it turned
  out to be the whole story of the holdout failure.

Two different cadences, easy to blur:

- **Decisions are weekly** (183 Fridays in dev, 65 in holdout) — when the
  strategy picks and schedules orders.
- **Measurement is daily** (~965 dev marks, ~322 holdout marks) — the pot has
  a value every market day whether or not anything traded.

`trades.csv` (687 dev rows, 281 holdout rows) is the plumbing — one row per
completed round trip. The statistics do NOT run on trades; they run on the
daily equity curve. A trade held 13 sessions influences 13 daily marks, and
~16 positions are typically open at once, all blending into each single daily
number.

## 2. Daily excess return — the list everything else is computed from

```
daily excess(d) = pot's return on day d − SPY's return on day d
```

SPY is the buy-and-hold benchmark ("what if the same $100k just sat in SPY").
The dev window gives ~964 daily excess numbers; the holdout ~320. Everything
statistical operates on that one list.

## 3. The observed mean, and the true edge it estimates

- **Observed mean** = sum of the list ÷ length of the list. For the frozen dev
  config: the 964 values summed to +0.000038 (four *thousandths* of one
  percent, total, over 3.8 years) → mean **+0.0000000391 per day**. Positives
  and negatives cancelled almost perfectly.
- **True edge** = the average that would emerge over infinite data. It is a
  hidden property of the strategy, *never observable*. The observed mean is a
  luck-contaminated estimate of it.

Inference therefore runs backwards: you can never verify "the true edge is X";
you can only ask **"if the true edge were zero (the null hypothesis), could
luck alone have produced what I observed?"**

## 4. Why detection is hard: the scale of signal vs noise

- A *great* edge is ~5%/year over SPY ≈ **+0.02% per day**.
- Daily excess noise is **σ ≈ 1% per day** (measured: 0.986% for the dev
  config) — the noise is 25–50× the signal.
- Luck-wobble of an observed mean ≈ σ/√n. With n≈960: ≈0.03%/day, so the
  ~95% luck band is roughly **±0.06%/day ≈ ±12–15% per year**. Pure luck can
  make a worthless strategy "beat SPY by 8%/yr" over 3.5 years.
- The coin version: observed heads% wobbles ±√(p(1−p)/n). Fair coin, 960
  flips → ±1.6 pp per standard error; 53% heads proves nothing. Valid only for
  independent trials with constant p — conditions markets violate, which is
  why the study uses a bootstrap instead of the formula.

Also note: the excess series (σ≈0.99%) is *noisier than the portfolio itself*
(σ≈0.48%) because the half-cash portfolio doesn't track SPY, so the difference
inherits volatility from both sides. Measuring against a benchmark the
strategy doesn't resemble makes the edge harder to detect.

## 5. The stationary block bootstrap, in plain terms

To measure the luck band without assuming independence:

1. Build a fake full-length history by gluing together randomly chosen
   ~21-day *blocks* of the real excess series (with replacement; blocks
   preserve streakiness/volatility clustering that day-by-day resampling
   would destroy).
2. Compute that fake history's mean. That is ONE bootstrap value.
3. Repeat 10,000×; sort; drop the lowest and highest 2.5%. The middle 95% is
   the **CI95** — the range of true edges consistent with the data.

Properties worth remembering:

- The CI is always centered near *your observed mean* — it never decomposes
  the result into "real part" and "luck part"; it only bounds what's
  plausible.
- No bell curve is assumed anywhere; only the actual data is reshuffled.
- Dependence can widen OR narrow the band vs the naive σ/√n formula. (Here it
  narrowed it slightly: ±0.00049 vs naive ±0.00064 — the half-cash portfolio's
  excess mean-reverts day to day.) The bootstrap measures whatever is true
  instead of assuming.

## 6. The verdict rule: does the whole bar clear zero?

Picture the CI as a bar on a number line. **To claim an edge, the entire bar
must sit above zero** — even the unluckiest reshuffle still positive. The bar
passes or fails at its *left edge*, not its center.

The study's two real cases:

| | Observed mean | CI95 | Reading |
|---|---:|---|---|
| Development (frozen config) | +0.0000000391 | [−0.000478, +0.000504] | Bar centered on zero, dot ON the line: textbook "no detectable edge." Not "almost passed" — maximally agnostic. |
| Holdout | −0.000647 (≈ −16%/yr) | [−0.001337, +0.000041] | Bar 97% below zero. Did not "almost pass" — it almost proved **harm**. The sliver above zero is the last doubt protecting the strategy, not evidence for it. |

Asymmetry of conclusions: a CI spanning zero means "no evidence of an edge,"
NOT "proven worthless." A negative observed mean is grounds to discard
practically (burden of proof is on the strategy) but doesn't prove badness —
luck cuts both ways.

## 7. The selection effect (multiple testing) — best-of-many is not evidence

Build 100 *fair* coin robots, flip each 960 times, show only the champion: it
will show ~53–54% heads **by construction**. The identical number carries
completely different evidence depending on how many candidates it was
selected from.

The study ran ~116 development configurations (2 stop variants + 6
ordering/regime variants + 8 post-fix grid + 32 event-window grid + 64
position-size grid + 4 entry-rule reruns) and froze the best-looking one
(+43.28% vs SPY +35.9%, 93rd control percentile). With a ±12%/yr luck band, a
+2%/yr terminal edge from the champion of ~116 correlated tries is exactly the
champion robot. The fresh-flips acid test is the holdout — and the champion
reverted on schedule (+22.4% vs SPY +49.2%).

(Softener: 116 variants sharing most trades are far fewer than 116
*independent* tries — but selecting the best of correlated tries still
inflates the winner.)

## 8. What survives development, and what doesn't

Test: a conclusion survives if it came from **comparing runs against each
other with a large effect**, and dies if it came from **admiring the
champion's own score**.

Survives:

- The 2.5×ATR stop was destructive (paired on/off comparison; huge effect —
  53% of trades harvested at −7.5% while T-1 finishers averaged +9%).
- The technical score ranked *inversely* (real portfolio at the 2nd–3rd
  percentile of its own 100 random controls — random beat it 97/100 times).
- The point-in-time earnings-date forecaster works (5–6% miss rate, misses
  benign) — directly measured infrastructure.
- Cash drag is structural: ~50% average deployment mathematically caps
  relative performance in rallies (holdout: 47% deployed × SPY +49.2% ≈
  +23.1% predicted; actual +22.4% — the picks were ~neutral, the construction
  did the damage).

Does not survive:

- The champion's +43.28%, its 93rd percentile, and the specific frozen knobs
  (2–5 weeks, longest-first, $4k×25) as evidence of edge.

## 9. Statistical power — the lesson that should precede any future holdout

**Power** = a test's probability of detecting an effect that is really there.

- The dev CI test could only detect edges beyond ~±12%/yr → a *true* +5%/yr
  strategy would fail it almost every time. So "dev CI spans zero" was
  near-inevitable regardless of truth — weak evidence of absence.
- The holdout as pre-registered (~320 days, σ≈1%/day) required an observed
  mean ≈ +0.11%/day ≈ **+28%/yr over SPY** to pass. Nobody has that. The
  endpoint was structurally almost unpassable *before it was run* — and that
  was computable in five minutes from n and σ.

Verdict on "should the holdout have been run at all": stop-after-dev was the
better move — not because the dev CI spanned zero (underpowered tests always
span zero), but because there was no positive evidence to confirm, cash drag
made rally underperformance mechanically likely, and the power calculation
showed the holdout couldn't pass under any realistic scenario. The one-shot
window was spent confirming a near-certainty (though it did yield real
information: near-proof of harm, out-of-sample confirmation of the cash-drag
mechanism, clean discipline throughout).

**The rule going forward: before pre-registering any pass/fail endpoint,
compute the minimum effect size it can detect. An endpoint that can't detect
any realistic signal has failed before the experiment starts, no matter how
honestly it is run.** Practical fixes for a future study: longer windows,
trade-level endpoints that use all ~687 trades (with ticker-clustered
bootstraps) instead of ~960 noisy daily marks, deployment-matched benchmarks
(e.g., 50% SPY + 50% cash) so the test measures picking skill rather than
cash allocation, and point-in-time universe data so survivorship doesn't
inflate the "category" baseline.

---

## One-paragraph summary

A backtest is one pot of money marked daily; subtracting SPY's daily return
gives a list of daily excess numbers whose plain average (the observed mean)
estimates an unobservable true edge. Daily noise is 25–50× larger than any
realistic edge, so the observed mean must be judged against a luck band — the
bootstrap CI95, built by reshuffling month-long chunks of the real history.
An edge claim requires the entire bar to clear zero; the study's development
bar was centered exactly on zero (no evidence), and its holdout bar sat
almost entirely below zero (near-evidence of harm). The champion
configuration's +43% development return was the expected outcome of picking
the best of ~116 tries inside a ±12%/yr luck band, and it reverted in the
holdout exactly as a fair coin's "winning robot" reverts on fresh flips. What
the study genuinely established came from controlled comparisons — the stop
destroys value, the score ranks inversely, the date forecaster works, cash
drag is structural — and the meta-lesson is to compute a test's power before
spending a one-shot holdout on it.
