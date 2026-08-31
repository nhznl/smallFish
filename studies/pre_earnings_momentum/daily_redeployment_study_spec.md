# Pre-earnings daily redeployment — study design (draft)

**Proposed study ID:** `pre-earnings-daily-redeployment-v1`

**Status:** 2021 DEVELOPMENT PILOT COMPLETE. The owner authorized and completed
the baseline 2021 pilot and the $500/$1,000 price-cap sensitivity replays on
2026-08-31. No later historical year is authorized.

**Prepared:** 2026-08-30

**Predecessor:** [`backtest_spec.md`](backtest_spec.md) and
[`backtest_spec_2.md`](backtest_spec_2.md). Their published evidence, frozen
configuration, artifacts, and verdicts remain unchanged. This is a new method
and study identity housed in the same strategy package; it is not a rerun or
amendment of either predecessor.

## 1. Question and claim boundary

Starting with $50,000, does a daily, event-driven portfolio of liquid
pre-earnings stocks selected from smallFish's bullish Momentum Scanner setup
add value relative to holding SPY, after whole-share constraints and modeled
transaction costs?

Two allocation arms answer different parts of that question:

1. **Primary — equal allocation.** Allocate deployable capital equally among
   newly selected stocks, subject to the position and sector constraints.
2. **Secondary — score-proportional allocation.** Allocate deployable capital
   in proportion to the candidates' Momentum Scanner `setupScore`, subject to
   the same constraints.

The score-proportional arm is secondary because the predecessor study found
adverse historical evidence for its different pre-earnings composite score.
That finding does not test Momentum Scanner `setupScore`, but it is enough to
avoid making score weighting the sole or primary claim.

This study may describe simulated returns, risk, turnover, execution costs,
capital deployment, and differences from SPY. It may not present candidates as
predictions or advice. Historical results use today's surviving universe and
therefore remain survivorship-biased.

## 2. Evidence phases and amendment discipline

### 2.1 2021 pilot

The first implementation run covers 2021 only. Its purposes are to verify the
daily state machine, inspect every order and accounting transition, measure
turnover, and evaluate the capital-scaled close-decline rule.

The origin decision is the first valid SPY session of 2021 and its orders are
eligible to execute no earlier than the following SPY session. The portfolio
begins entirely in cash; origin allocation is the explicit initialization
exception to the conditional redeployment trigger in section 7.

The pilot is development evidence. After inspecting it, the owner may amend
the method. Every amendment must be dated in section 17. An amendment requires
rerunning 2021 from the beginning; results from superseded pilot definitions
must remain labeled as superseded and cannot be mixed with the accepted run.

### 2.2 Later historical development

After the owner accepts the pilot mechanics, the accepted rules are frozen for
the remaining requested historical years. Those years are still development
or exploratory evidence: the predecessor work already observed overlapping
market history, the current universe is survivorship-biased, and historical
earnings estimates are unavailable point in time.

The runner must support one calendar year at a time and stop after materializing
that year's report. A later year does not run automatically. Portfolio state,
open positions, pending orders, pins, cost basis, SPY shares, and cash carry
across year boundaries when the next year is authorized.

### 2.3 Future validation

No historical result under this document is confirmatory. A future validation
claim requires a separately dated prospective protocol naming its first unseen
decision session, horizon, primary endpoint, and frozen implementation commit
before that first session is observed.

## 3. Capital and portfolio constraints

- Initial capital: **$50,000**.
- No deposits, withdrawals, leverage, margin, or short positions.
- All stock and SPY orders use **whole shares**.
- Minimum stock position at entry: **$1,000 target market value**.
- Maximum stock position at entry: **$5,000 target market value**.
- Appreciation above $5,000 never causes a partial sale.
- Maximum simultaneous positions per sector: **3**, counting open and pending
  stock positions. Missing sectors form one `Unknown` sector.
- There is no routine resizing of surviving stock positions.
- SPY is a cash-sweep sleeve, not a candidate. It is exempt from the stock
  position count, sector cap, earnings gates, setup gates, score rules, and pin.
- Whole-share constraints make exact 100% investment impossible. After buying
  all permissible whole stock and SPY shares, the irreducible residue remains
  cash and must be reported rather than hidden.

The $1,000 and $5,000 limits are sizing targets fixed at the decision close.
Actual next-open market value can differ because of an overnight price gap, but
the entry guard in section 9 prevents the filled principal from exceeding the
$5,000 cap.

## 4. Inputs and point-in-time rules

### 4.1 Prices and calendar

- Use the repository's validated OHLCV reader. A hard validation defect
  quarantines the entire symbol for the affected run.
- Load contiguous history including sufficient prior-year warm-up for every
  indicator used by Momentum Scanner `momentum-v3`.
- SPY's validated sessions define the trading calendar.
- A decision on session `D` may use only bars with `date <= D`.
- Entry and ordinary exit fills occur no earlier than the next SPY session
  after `D`.

### 4.2 Universe and classifications

Use `universe.csv - retired_symbols.csv` and the repository sector registry as
of the run. Record their hashes and explicitly label the resulting history as
survivorship-biased. Stocks, ETFs, and mutual funds may exist in Momentum
Scanner, but a symbol without a qualifying issuer earnings event cannot pass
this study's event gate.

### 4.3 Earnings knowledge

Historical selection must not use final realized earnings dates as if they were
known in advance. Reuse the predecessor's causal anniversary forecaster:

- use only realized events strictly before decision date `D`;
- forecast the next event from the prior comparable event plus 364 days;
- retain the predecessor's date-consistency gate;
- use the forecast for eligibility and the planned T-1 exit;
- use the realized event only to determine whether the forecast missed an
  earlier report and therefore affected the outcome.

The event forecast recorded at entry, `predicted_event_date_at_entry`, controls
that position's planned exit. Later outcome knowledge never rewrites it.

## 5. Momentum Scanner parity contract

The study uses the Momentum Scanner calculation currently identified as
`momentum-v3`, not the pre-earnings package's older `score_total`.

The implementation must freeze a study-local, causal replay of the exact
Momentum Scanner behavior without importing `stock-app/` into the studies
runtime. Characterization fixtures must prove parity for:

- raw trend direction;
- scanner setup classification;
- `setupScore` and every score component;
- preliminary reversal status and penalty;
- freshness and SPY-relative-strength context; and
- insufficient-history behavior.

Every artifact records `setup_score_version: momentum-v3`. If the live scanner
later changes versions, this study remains on `momentum-v3` unless a new study
is pre-registered. Silent drift is prohibited.

## 6. Daily candidate eligibility

At decision close `D`, a new long candidate must pass every condition below:

1. Symbol is not already open or pending in that portfolio arm.
2. Symbol is not pinned on `D`.
3. Decision close is from $10 through $300, inclusive.
4. Twenty-session average volume is at least 4,000,000 shares.
5. Twenty-session average dollar volume is at least $10,000,000.
6. The latest bar is no more than three SPY sessions stale.
7. A causally predicted earnings event is 2–5 weeks from `D`, inclusive.
8. The predecessor date-consistency gate passes.
9. Momentum Scanner setup is exactly `BULLISH_CONTINUATION`.
10. The penalized Momentum Scanner `setupScore` is strictly greater than 50.
11. Adding the order would not exceed three open-plus-pending positions in the
    candidate's sector.

A `BULLISH_CONTINUATION` candidate with preliminary bearish-reversal evidence
remains eligible when its penalty-adjusted `setupScore` is still greater than
50. The warning and penalty must be recorded in the candidate audit row.

Eligible candidates are ordered by:

1. `setupScore` descending;
2. ticker ascending as the stable tie-break.

The candidate report may retain at most ten candidates per sector before
portfolio allocation. No score can rescue a failed hard gate.

## 7. When the daily candidate scan may deploy capital

Held positions must be evaluated after every completed session because exits
depend on their latest trend, score, price, and event schedule.

The full unheld-candidate selection and allocation pass is actionable only when
at least one of these conditions holds:

- this is the origin allocation of the all-cash $50,000 portfolio;
- one or more existing stocks are scheduled to exit at the next open; or
- free cash plus the close-marked value of whole SPY shares available for sale
  can fund at least one $1,000 stock target after estimated costs.

If neither condition holds, the portfolio submits no new stock or SPY order and
does not resize any surviving position. The implementation may compute
diagnostics, but computation alone must never create turnover.

When the condition holds but no eligible unheld candidate exists, all sale
proceeds remain destined for the SPY sleeve.

## 8. Exit signals

For every open stock, evaluate all exit signals from completed information at
decision close `D`. Record every simultaneously true trigger. Setup score is an
entry-selection input only; any score recorded after entry is diagnostic and
can never trigger an exit.

### 8.1 Bearish trend

Trigger `TREND_BEARISH` only when Momentum Scanner's underlying advanced trend
direction is `DOWN`/bearish.

`BULLISH_REVERSAL` alone does **not** trigger this exit. Sideways, neutral,
`WATCH`, and preliminary bearish-reversal evidence do not trigger it either.

### 8.2 Capital-scaled close-triggered price decline

The allowed close decline is fixed at entry from the actual stock principal,
excluding transaction cost:

```text
entry_principal = shares * entry_fill_price

allowed_drawdown = clamp(
    20% - ((entry_principal - $1,000) / $4,000) * 10%,
    minimum=10%,
    maximum=20%,
)
```

This produces the following reference points:

| Entry principal | Allowed close decline | Approximate principal loss |
|---:|---:|---:|
| $1,000 | 20.0% | $200 |
| $2,000 | 17.5% | $350 |
| $3,000 | 15.0% | $450 |
| $4,000 | 12.5% | $500 |
| $5,000 | 10.0% | $500 |

Trigger `CAPITAL_SCALED_CLOSE_DECLINE` when:

```text
decision_close <= entry_fill_price * (1 - allowed_drawdown)
```

Record `entry_principal` and `allowed_drawdown` with the position and never
recalculate the percentage from its changing market value. This is a
close-triggered next-open exit, not a stop-loss. Gaps and transaction costs can
make the realized loss larger than the reference amount.

### 8.3 Planned T-1 exit

Let `P_entry` be the predicted earnings date fixed at entry. Let
`T1_session` be the last SPY session strictly before `P_entry`. Schedule the
sale in time to execute at the **open of `T1_session`**. The preceding completed
session is therefore the final decision point for that exit.

If the realized report unexpectedly occurs on or before the planned exit, use
the predecessor's pessimistic forecast-miss handling: the position absorbs the
event and exits at the first available session after the realized report. The
realized date affects the outcome only; it never changes prior selection.

### 8.4 Missing data and end of data

Missing or stale bars cannot create a bearish or price-decline signal. A
scheduled T-1 exit uses the next available valid opening bar and is flagged as
delayed. At an authorized run cutoff, open positions remain open and are marked
using the last valid close; they are not liquidated solely because a calendar
year ended.

## 9. Orders and execution

### 9.1 Decision-close stock orders

Whole-share quantities are determined from information available at decision
close. Reuse the predecessor's 3% limit-on-open guard:

```text
entry_limit = decision_close * 1.03
```

The allocator reserves enough cash for the share quantity at the limit plus
the entry cost, and never reserves more than $5,000 of stock principal for one
candidate. On the next session:

- if `entry_open <= entry_limit`, fill all reserved whole shares at the open;
- if `entry_open > entry_limit`, cancel the order with no retry;
- if the required bar is missing, cancel and report the reason; and
- never substitute a different candidate using entry-session information.

The $1,000 minimum is a decision-time target. A downward gap may make the filled
market value slightly less than $1,000; this is reported and does not cause an
after-the-fact share increase.

### 9.2 Next-open sequence

At the next session's open, process in this order independently for each arm:

1. sell every scheduled stock position;
2. credit net stock proceeds;
3. sell the minimum reserved whole SPY shares needed for accepted stock orders;
4. process stock entry orders in decision rank order; and
5. if actual gaps make all orders unaffordable, reduce or cancel the
   lowest-ranked order first until cash remains non-negative.

An exit and entry may share an opening auction. No proceeds are assumed before
the exit fill that creates them.

### 9.3 SPY sweep-in

After opening orders are resolved, buy as many whole SPY shares as the remaining
cash can support at that session's close, including transaction cost. Keep the
sub-share residue as cash. If there is no valid SPY bar, retain cash and flag
the sweep failure; never use a stale or substituted price.

## 10. Transaction costs

Apply **10 basis points (0.10%) per side** to every stock and SPY transaction.
This is an execution-friction model, not a broker commission claim.

Examples at an unchanged $100 price:

- one share: $0.10 on purchase and $0.10 on sale;
- ten shares / $1,000 principal: $1.00 on purchase and $1.00 on sale;
- fifty shares / $5,000 principal: $5.00 on purchase and $5.00 on sale.

Costs are separate cash ledger entries; they do not alter the recorded market
fill price. Report gross and net results and total costs by stocks and SPY. A
zero-cost diagnostic uses the exact executed orders, fills, and share quantities
from the cost-bearing arm in a shadow ledger with cost entries set to zero. It
does not feed capital back into allocation or change any trading decision.

## 11. Pinning

A stock exit triggered by `TREND_BEARISH` or
`CAPITAL_SCALED_CLOSE_DECLINE` pins that symbol for 30 calendar days from the
execution date. Set:

```text
eligible_again_date = exit_execution_date + 30 calendar days
```

The symbol is ineligible before that date and becomes eligible on or after it.
The pin is portfolio-arm-specific because the two arms can exit on different
dates.

A pure planned T-1 exit does not create a pin. If T-1 and another trigger are
simultaneously true, record every trigger, classify the primary exit as T-1,
and do not pin.

## 12. Equal-allocation arm

Allocation considers only new candidates and newly deployable capital.
Surviving stock positions are never resized or included in the equalization
calculation.

1. Traverse eligible candidates in score order while enforcing the sector cap.
2. Determine the largest prefix that can receive the whole-share equivalent of
   at least a $1,000 decision-time target, including reserved entry costs.
3. If all candidates cannot receive the minimum, repeatedly remove the
   lowest-scoring candidate.
4. Assign equal dollar targets from the deployable amount, capped at $5,000 per
   candidate.
5. Convert targets to deterministic whole-share quantities that respect the
   limit-price reservation and cap.
6. Use remaining dollars to add whole shares toward the most underallocated
   target; ties follow score order.
7. Send unallocatable capital to the SPY sweep.

## 13. Score-proportional arm

Allocation uses the same candidate ordering, gates, sector cap, execution, and
capital source as the equal arm. It differs only after minimum funding:

1. Reserve a whole-share minimum lot targeting at least $1,000 for every
   retained candidate.
2. If the minimum lots cannot be funded, remove the lowest-scoring candidate
   until they can.
3. Distribute remaining deployable dollars in proportion to the candidates'
   positive penalized entry `setupScore` values.
4. When a candidate reaches the $5,000 cap, remove it from the remaining
   distribution and repeat the calculation for uncapped candidates.
5. Convert dollar targets to deterministic whole-share quantities and allocate
   residual whole shares in descending order of proportional target shortfall,
   with score then ticker as tie-breaks.
6. Send unallocatable capital to the SPY sweep.

The proportional arm never compares scores from different setup types because
every entry candidate is `BULLISH_CONTINUATION`.

## 14. Accounting and benchmark

Maintain independent ledgers for the equal and proportional arms.

At each valid session close:

```text
portfolio_equity
    = cash
    + sum(open_stock_shares * valid_close)
    + spy_shares * spy_close
```

Record stock and SPY realized P/L, unrealized P/L, costs, and market value
separately. Entry cost basis includes the buy-side modeled cost. Net realized
P/L includes both entry and exit costs.

The passive benchmark starts with the same $50,000 on the first strategy entry
session, buys the maximum whole SPY shares at that session's open, pays the same
10 bps buy cost, and retains residual cash. It does not rebalance. Its year-end
mark includes the modeled cost of liquidation for like-for-like terminal net
value, without mutating the benchmark ledger.

Adjusted-price conventions must match the validated cached data contract so
splits and distributions are not inconsistently treated between strategy and
benchmark.

## 15. Required artifacts and annual report

Every authorized year writes to a new study-specific directory and never
overwrites predecessor artifacts. At minimum, produce:

### 15.1 `daily_equity.csv`

One row per SPY session and arm:

- date;
- cash;
- stock market value;
- SPY shares and market value;
- total equity;
- realized and unrealized stock P/L;
- realized and unrealized SPY P/L;
- cumulative stock costs and SPY costs;
- strategy return;
- passive-SPY benchmark value and return;
- running excess return;
- drawdown; and
- stock, SPY, and cash exposure percentages.

### 15.2 `decisions.csv`

One row per evaluated symbol/decision with:

- decision and intended execution dates;
- arm and ticker;
- held, pinned, eligible, selected, or rejected state;
- every gate and rejection reason;
- predicted and realized event dates where causally permitted;
- raw trend direction and displayed setup;
- displayed setup score/version/components;
- entry principal and fixed allowed drawdown percentage;
- entry price and current close decline percentage;
- every active exit trigger;
- sector counts;
- intended dollar target, shares, limit, and reservation; and
- execution outcome or cancellation reason.

### 15.3 `orders.csv`

One row per stock or SPY order with decision time, execution session, side,
shares, reference price, limit where applicable, fill price, principal, cost,
status, and reason.

### 15.4 `trades.csv`

One row per completed stock position with entry/exit decisions and fills,
shares, entry setup score, entry principal, allowed drawdown,
predicted/realized event dates, all exit triggers, pin dates, holding sessions,
gross return, net return, realized P/L, costs, and matched-session SPY
return/excess.

### 15.5 `positions_year_end.csv`

Every open stock, pending order, pin, SPY holding, and residual cash at the
calendar-year checkpoint. No artificial December liquidation.

### 15.6 `summary.json` and human-readable report

For each arm, report:

- starting and ending equity;
- total and excess return versus passive SPY;
- maximum drawdown and annualized daily volatility;
- return-to-drawdown ratio;
- realized and unrealized P/L;
- number of buys, sells, completed trades, and winning trades;
- exits by reason and simultaneous-trigger counts;
- average and median holding period;
- average number of stock positions and sector concentration;
- stock, SPY, and cash exposure distributions;
- gross stock and SPY turnover;
- total transaction costs and their return drag;
- pin counts and attempted pinned re-entries;
- unfilled/cancelled orders and whole-share residual cash;
- days with no candidates and days fully allocated to SPY; and
- every data quarantine, stale holding, and delayed exit.

The 2021 report additionally includes the zero-cost shadow-ledger diagnostic.
There are no setup-score exit sensitivities because setup-score deterioration
is not an exit rule.

Every artifact receives a reproducibility manifest containing study ID, phase,
year, arm, arguments, full effective configuration, input hashes, setup-score
version, Git revision and dirty state, dependency versions, quarantines, and
output hashes.

## 16. Implementation and verification requirements

Implementation belongs under `studies/pre_earnings_momentum/` and uses the
utilities/studies Python runtime. `stock-app/` must not import the study, and the
study must not import FastAPI application modules. Any future API or UI reads
materialized artifacts rather than executing research code.

Before the first 2021 run, tests must prove:

- `momentum-v3` parity on fixed synthetic histories;
- all features use bars at or before the decision close;
- realized future earnings dates never affect selection;
- exact 2–5 week boundary behavior;
- strict `setupScore > 50` entry behavior;
- preliminary-reversal inclusion and penalty behavior;
- bearish-only trend exit behavior;
- capital-scaled drawdown interpolation and clamping at $1,000, intermediate
  principals, and $5,000;
- the capital-scaled close-triggered next-open exit;
- T-1 scheduling and pessimistic early-report handling;
- 30-calendar-day pin boundaries and T-1 no-pin behavior;
- sector cap counting open and pending orders;
- equal and proportional whole-share allocation, floors, caps, and tie-breaks;
- no routine resizing of surviving positions;
- origin allocation and later conditional deployment from exits or deployable
  cash/SPY only;
- gap, rejected-order, affordability, and non-negative-cash behavior;
- uniform 10 bps costs on stocks and SPY;
- zero-cost shadow accounting uses identical executed orders, fills, and shares
  and never feeds back into decisions;
- whole-share SPY sweep and visible residual cash;
- daily ledger reconciliation to positions and order cash flows;
- annual checkpoint carry-forward without liquidation; and
- deterministic byte-for-byte output from identical inputs.

Required checks for an implementation change:

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

No live network access is permitted in tests.

## 17. Amendment log

- 2026-08-30: Initial draft prepared from the owner's design discussion. No
  implementation or study run authorized. Proposed rules include $50,000
  initial capital; daily event-driven redeployment; Momentum Scanner
  `BULLISH_CONTINUATION` with penalized `setupScore > 50`; equal primary and
  score-proportional secondary arms; $1,000–$5,000 whole-share entries; three
  simultaneous positions per sector; SPY sweep; bearish-direction, provisional
  20% comparable-score, 15% close-decline, and T-1 exits; 30-calendar-day pins;
  and uniform 10 bps per-side costs.
- 2026-08-30: Owner revision removes setup-score deterioration as an exit and
  removes all setup-score threshold sensitivity replays. Exit signals are now
  limited to underlying trend direction turning bearish, a close decline scaled
  linearly from 20% at $1,000 entry principal to 10% at $5,000, and planned T-1.
  The uniform-cost primary accounting and zero-cost shadow-ledger diagnostic
  remain.
- 2026-08-30: Owner directed preparation of an external-agent implementation
  handoff. The design is approved for implementation and offline verification
  only. The first 2021 pilot run remains separately gated and unauthorized.
- 2026-08-31: After accepting the independently reviewed implementation, the
  owner authorized and ran the first 2021 development pilot with the original
  $300 maximum decision-close entry price. The owner then authorized two full
  2021 sensitivity replays with otherwise identical rules and maximum entry
  prices of $500 and $1,000. The $300 result remains the baseline pending
  comparison; these price-cap replays do not authorize any later year.

## 18. Design-review approval gate

The implementation passed independent review, and the owner subsequently
authorized the baseline 2021 pilot plus the two price-cap sensitivity replays
recorded in section 17. No later year is authorized by that approval.

Design review should reject the draft if any rule permits future data in a
decision, silently changes predecessor evidence, imports across the runtime
boundary, hides whole-share cash, compares scores with different meanings, or
creates routine turnover without an exit or deployable SPY trigger.
