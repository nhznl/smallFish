# Market Event Calendar agent loop

This file is the coordination lock and durable mailbox for the automated
designer/implementer/reviewer loop. Both agents must re-read it immediately
before doing work and immediately before editing it.

## Machine-readable state

```text
PROTOCOL_VERSION: 1
LOOP_STATUS: ACTIVE
CURRENT_MILESTONE: M2
CURRENT_ROUND: M2-R14
NEXT_ACTOR: REVIEWER
LAST_HANDOFF_ID: I-011
M1_STATUS: ACCEPTED
M2_STATUS: CHANGES_REQUESTED
M3_STATUS: PLANNED
OWNER_DECISION_REQUIRED: NO
STOP_REASON: NONE
```

## Lock and ownership rules

1. `NEXT_ACTOR` is the lock. An agent whose role is not named there must make
   no implementation or coordination-file changes and must remain quiet.
2. The active agent re-reads this file just before changing any file. If the
   lock changed, it stops without writing.
3. The implementer owns application code, tests, implementation documentation,
   focused commits, and the Implementer outbox. It never changes the approved
   product design merely to make a test pass.
4. The reviewer owns design clarifications, independent verification, review
   findings, milestone acceptance, and the Reviewer outbox. It does not edit
   implementation code during a review.
5. Every handoff gets a new monotonic ID. The receiving agent processes each
   handoff ID at most once.
6. A handoff is not valid without a stable commit or explicit commit range,
   exact tests and results, deviations, and known gaps.
7. P0 and P1 findings block acceptance. P2 findings must be fixed or explicitly
   waived by the owner. P3 findings are non-blocking polish.
8. After an implementation handoff, set `NEXT_ACTOR: REVIEWER`. After a review
   requesting changes or kicking off the next milestone, set
   `NEXT_ACTOR: IMPLEMENTER`.
9. If an approved design stop rule is reached, set
   `OWNER_DECISION_REQUIRED: YES`, describe one concrete question, and make no
   speculative change.
10. When M1, M2, and M3 are accepted and the final integration review passes,
    set `LOOP_STATUS: COMPLETE` and `NEXT_ACTOR: NONE`.

## Polling behavior

Each agent checks this file every 10 minutes. An unchanged file or a lock owned
by the other role is expected and produces no user notification. Notify only
for a new handoff, review decision, milestone acceptance, completion, failure,
or required owner decision.

## Implementer outbox

```text
HANDOFF_ID: I-011
MILESTONE: M2
STATE: READY_FOR_REVIEW
BASE_COMMIT: f8d174d
IMPLEMENTATION_COMMITS: c944814
SCOPE: Resolved M2-R12-F1 through M2-R12-F4. Calendar-capable Finnhub reuse now
  has the same fixed 24-hour interval as primary sync and the API, while the
  max_age_days override remains legacy-only. Direct ingestion requires exact
  schema-2 identity flags and rejects boolean, fractional, and string failure
  counts. Federal Reserve reconciliation permits only one unmatched move and
  fails provider refreshes on count changes or multiple unmatched moves while
  retaining the last successful snapshot. Parser version 4 transactionally
  merges schedule history and removes cancelled date/month rows from an
  already-diverged parser-v3 database, leaving one chosen occurrence.
CHANGED_FILES: docs/ARCHITECTURE.md; docs/MARKET_EVENT_CALENDAR_DESIGN.md;
  utilities/README.md; utilities/events.py;
  utilities/market_calendar/primary_sync.py;
  utilities/tests/test_market_calendar.py.
FINDINGS_RESOLVED: M2-R12-F1, M2-R12-F2, M2-R12-F3, M2-R12-F4.
TESTS_AND_RESULTS: Focused market-calendar file 55 passed; full utilities suite
  827 passed; full backend suite 601 passed; Angular Node 24 suite 200 passed;
  Angular production build and ./commands.sh build-ui passed; documentation
  check passed for 57 files; both Python compileall commands, secret scan of
  620 tracked objects, and git diff --check passed.
VISUAL_VERIFICATION: Rebuilt and loaded Event Risk from the verified local
  commands.sh server at the desktop viewport. The route, coverage diagnostics,
  caveat, summary, filters, and event tables rendered successfully. No UI
  source changed in this round.
DESIGN_DEVIATIONS: None.
KNOWN_GAPS: Released values beyond the existing BLS path remain a separate,
  explicitly unconfigured capability; M2 schedule coverage no longer depends
  on that optional path. No known blocking M2 implementation gap remains.
```

## Reviewer outbox

```text
HANDOFF_ID: R-010
MILESTONE: M2
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 643af4f
REVIEWED_COMMITS: db17aee, 5c8bc74
FINDINGS_VERIFIED_RESOLVED: M2-R10-F1, M2-R10-F5
FINDINGS_PARTIALLY_RESOLVED: M2-R10-F2, M2-R10-F3, M2-R10-F4

M2-R12-F1 [P1] Calendar prerequisite freshness still diverges from primary
sync when the supported max_age_days override is not one. _calendar_is_fresh
uses max_age_days * 24 for calendar capability, while run_primary_sync and the
API enforce the configured Finnhub freshness_hours value of 24. Reproduced
with a 2026-10-04 cache at 2026-10-05T10:00:00Z and max_age_days=2:
ensure_fresh_events returned fresh/ok with no provider call, then the immediate
primary sync exited 1 and marked Finnhub stale_cache. Preserve the override
only for explicitly legacy capability, and evaluate calendar capability using
the same configured/fixed interval as primary sync and API. Add a greater-than-
24-hour max_age_days=2 regression proving both commands agree.

M2-R12-F2 [P1] Federal Reserve reconciliation does not fail closed for
ambiguous many-record changes. After date/month anchors, it greedily pairs any
equal-sized unmatched sets by nearest civil date unless the single shortest
distance is tied. Reproduced by moving both September and October G.17 rows:
the refresh was accepted but attached the October seed to the January move and
the September seed to the February move, reversing their actual identities.
Simultaneous FOMC crossings and a move plus insertion on the vacated date also
silently swap or replace identities. Permit only one uniquely unmatched old/new
move after anchors; if multiple moves remain or a count change coexists with a
move, fail the provider refresh and retain the prior snapshot. Add simultaneous
crossing and move-plus-insertion regressions for the FOMC pair and G.17.

M2-R12-F3 [P1] Direct primary ingestion still accepts a non-schema-2 or
malformed Finnhub sidecar as fully fresh. _calendar_identity_complete does not
check schemaVersion and coerces identityFailureCount through int(...).
Reproduced from an otherwise valid digest-bound sidecar with schemaVersion=1
and identityFailureCount=false: run_primary_sync returned published/0 and
stored Finnhub fresh. Require exact schemaVersion == 2, identityComplete is
True, and type(identityFailureCount) is int with value zero in the ingestion
path. Add direct primary-sync schema-1, boolean, fractional, and string count
regressions so market-calendar cannot bypass the prerequisite's validation.

M2-R12-F4 [P1] Parser-v4 migration does not clean a database already split by
the prior parser-v3 refetch path. Reproduced through the real sequence: the
6b292c7 date/month generation was refreshed under 78876ab into cancelled
date/month rows plus scheduled Mnn/Rnn replacements; upgrading under db17aee
changed the normalized snapshot to parser v4 but retained all three cancelled
date/month rows beside the three scheduled ordinal rows. The API loads
cancelled occurrences, so duplicate generations and split history remain.
Transactionally merge or remove the obsolete identity generation while
preserving the chosen event IDs and schedule history. Add an upgrade regression
starting from the already-diverged two-generation v3 database, not only the
clean pre-refetch v3 shape, and prove the API exposes one occurrence per event.
```

Reviewer verification for R-010:

- verified exact Finnhub CSV binding, post-refresh revalidation, legacy-only
  compatibility, and the default exact/+1-second timestamp boundary;
- independently reproduced the max_age_days=2 prerequisite/primary-sync
  freshness disagreement;
- independently reproduced multi-record Federal Reserve identity reversal;
- verified the clean parser-v3-to-v4 path is versioned and transactionally
  preserves schedule history, then independently verified the already-diverged
  v3 path is not cleaned;
- independently reproduced direct primary ingestion accepting schemaVersion 1
  with a boolean identityFailureCount as fresh;
- verified future BLS success timestamps now require a provider refresh;
- utilities suite: 816 passed;
- backend suite: 601 passed;
- focused calendar/event tests: 65 passed; focused backend API tests: 7 passed;
- documentation check, secret scan of 620 tracked objects, and commit-range
  git diff --check passed.

Prior review records:

```text
HANDOFF_ID: R-009
MILESTONE: M2
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 393ba58
REVIEWED_COMMITS: 78876ab, e7412ae
FINDINGS_VERIFIED_RESOLVED: M2-R8-F1, M2-R8-F4
FINDINGS_PARTIALLY_RESOLVED: M2-R8-F2, M2-R8-F3

M2-R10-F1 [P1] Calendar-capable Finnhub freshness is not bound to the exact
legacy artifact generation, and the refresh path does not validate the result
it just produced. fetchedAsOf is only a date, so two same-day fetches can mix a
new events.csv with an old schema-2 sidecar and still return fresh. Reproduced
by fetching AAPL, saving its sidecar, fetching MSFT with the same as-of date,
restoring the AAPL sidecar, and calling ensure_fresh_events: it returned fresh,
made zero provider calls, and accepted the mismatched symbols. Separately, a
credentialed refresh whose row contained fiscal year 2026.5 returned refreshed
and ok=true even though the new sidecar was identity-incomplete with null
coverage. Bind the sidecar to the exact legacy generation with a digest or
generation token while preserving legacy consumers, and revalidate calendar
capability after run_fetch before reporting success. Add interrupted/same-day
generation and post-fetch identity-incomplete regressions.

M2-R10-F2 [P1] The new Federal Reserve annual ordinals are still derived from
the schedule: the parser sorts current civil dates and assigns Mnn/Rnn ranks.
Moving the October FOMC pair ahead of the September meeting changed the moved
pair from M07 to M06 and reassigned M07 to the unchanged September meeting;
moving October G.17 ahead of September similarly swapped R10/R09. Inserting an
earlier record renumbers every later record, and a cross-year move changes the
year prefix. This can attach one occurrence's history to another. The current
test moves only final annual records without crossing a neighbor and therefore
does not prove stable identity. Use a provider-stable official anchor or a
persisted, conflict-safe reconciliation independent of current schedule order,
and add neighbor-crossing, insertion, and cross-year regressions for decision,
press conference, and G.17. If the source cannot support a defensible anchor,
invoke the design stop rule instead of redefining scheduled rank as stable.

M2-R10-F3 [P1] The Federal Reserve identity algorithm changed without bumping
fed-calendar-json-3 or migrating the date/month identities already materialized
by the preceding M2 implementation. A fresh old snapshot is therefore reused
under the new parser behavior; after expiry, the old keys are cancelled and
new Mnn/Rnn keys are inserted, splitting schedule history and leaving duplicate
cancelled/replacement occurrences. Bump the parser version and implement a
transactional migration or an explicit documented pre-acceptance rebuild that
cannot expose both identity generations. Add an upgrade regression starting
from the 6b292c7 database shape and prove identity/history continuity.

M2-R10-F4 [P1] The calendar prerequisite still uses legacy date-age freshness
instead of the new UTC timestamp contract. A complete sidecar fetched on
2026-10-04 was reported fresh by ensure_fresh_events for 2026-10-05 with zero
provider calls, although its midnight-Eastern success instant is stale after
2026-10-05T04:00:00Z. Keep legacy consumers' documented day semantics if
required, but evaluate the calendar capability using the shared inclusive UTC
window and an injectable current instant. Add exact-boundary and one-second-
expired prerequisite tests so ensure-events cannot claim calendar freshness
that market-calendar immediately rejects.

M2-R10-F5 [P1] The shared freshness contract is not used by the accepted base
BLS sync. utilities/market_calendar/sync.py checks only success + TTL >= now,
so a future last_success_utc is reused as fresh while within_freshness_window
and the API reject it. Reproduced by setting BLS last success to 2030 and
scanning in 2026: run_sync returned fresh/0 without requesting the provider.
Use the shared helper for the base sync and add a future-timestamp regression;
the command and API must agree for every required primary source.
```

Reviewer verification for R-009:

- verified malformed Finnhub identity tokens now fail closed and valid
  integral tokens retain their intended identity;
- verified schema-2 upgrade with a key, the explicit no-key legacy-only state,
  and the exact Finnhub ingestion/API timestamp boundary;
- independently reproduced same-day mixed-generation sidecar acceptance and
  unconditional post-fetch success for an identity-incomplete sidecar;
- independently reproduced FOMC and G.17 ordinal swaps across neighboring
  occurrences and confirmed the parser version was not changed;
- independently reproduced calendar-prerequisite date freshness accepting an
  expired sidecar and base BLS sync accepting a future success timestamp;
- utilities suite: 810 passed;
- backend suite: 601 passed;
- focused calendar/event tests: 59 passed; focused backend API tests: 7 passed;
- documentation check, secret scan of 620 tracked objects, and commit-range
  git diff --check passed.

Prior review records:

```text
HANDOFF_ID: R-008
MILESTONE: M2
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 08f20b2
REVIEWED_COMMITS: 6b292c7, 1aa9d97
FINDINGS_VERIFIED_RESOLVED: M2-R6-F3, M2-R6-F4

M2-R8-F1 [P1] Finnhub fiscal identity validation still does not fail closed for
malformed values. run_fetch converts year and quarter with int(...), which
silently turns 2026.5/3.5 into 2026-Q3 and booleans into 1-Q1. Reproduced with
one in-horizon malformed row: earnings.json declared identityComplete true,
claimed full coverage, and published the invented identity. Accept only valid
integral identity tokens before conversion: reject booleans, fractional
numbers, invalid or non-four-digit years, and quarters outside 1..4. Add
sidecar-generation and primary-sync regressions for each malformed class.

M2-R8-F2 [P1] A fresh legacy earnings cache cannot self-upgrade to the required
schema-2 calendar sidecar. _calendar_is_fresh validates only events.csv and
events_meta.json, so ensure_fresh_events reuses a missing or schema-1 sidecar
without calling Finnhub; run_primary_sync then rejects that same generation as
identity-incomplete. Reproduced by downgrading only earnings.json: ensure
returned fresh, made zero provider calls, and left schema 1. Make sidecar
existence, schema, matching generation, and identity completeness part of the
calendar-capable freshness path while preserving legacy consumers. Add a
pre-schema-2 upgrade regression and clear no-key capability diagnostics.

M2-R8-F3 [P1] M2-R6-F1's stable-identity requirement is incomplete. The new
Federal Reserve source IDs and canonical keys are derived from scheduled civil
date for the decision/press conference and scheduled release month for G.17.
Changing the fixture's meeting day from October 28 to 29 changed both IDs; a
G.17 move from October 16 to November 1 changed its identity from 2026-10 to
2026-11. Those become replacement/cancelled occurrences instead of one event
with appended schedule history, contrary to the canonical identity contract.
Use a schedule-independent official record or reference-period identity and
add cross-day and cross-month reschedule regressions for all three new Federal
Reserve occurrences. Do not infer a reference period without documented
provider semantics.

M2-R8-F4 [P1] Finnhub freshness is internally contradictory. The sync accepts
an events_fetched_as_of date that is one calendar day old, then records its
last_success_utc as midnight Eastern with freshness_hours=24. Reproduced with
a 2026-10-04 cache scanned late on 2026-10-05: run_primary_sync exited 0 and
stored status fresh, while the read API immediately evaluated the same required
source as stale. Use one timestamp-based freshness contract for ingestion,
stored provenance, and API evaluation; if only a date exists, fail
conservatively rather than report successful-but-stale output. Add a boundary
regression proving command status and API effective status agree.
```

Reviewer verification for R-008:

- independently verified the current official Federal Reserve JSON produces
  G.17, separate decision and press-conference occurrences, and official times;
- independently verified identity-incomplete sidecars fail closed, obsolete
  same-session relationships are removed, and pre-entry events do not receive
  the actual-overlap rule;
- independently reproduced malformed fiscal values being coerced into a
  complete, covered Finnhub sidecar;
- independently reproduced a fresh schema-1 sidecar being reused without an
  upgrade and then rejected by the calendar;
- independently reproduced all three new Federal Reserve identities changing
  when only their scheduled day or month moves;
- independently reproduced the calendar command publishing while the API
  immediately labels the required Finnhub source stale;
- utilities suite: 794 passed;
- backend suite: 600 passed;
- Angular suite under Node 24: 200 passed;
- Angular production build under Node 24 passed; and
- documentation check, secret scan of 620 tracked objects, and commit-range
  `git diff --check` passed.

Prior review records:

```text
HANDOFF_ID: R-007
MILESTONE: M2
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: d22b04a
REVIEWED_COMMITS: 05f7250, 655edf3
FINDINGS_VERIFIED_RESOLVED: M2-R4-F2, M2-R4-F3, M2-R4-F4,
  M2-R4-F5, M2-R4-F6

M2-R6-F1 [P1] The official Federal Reserve adapter still does not implement
the approved primary-event scope. A live parse for 2026-10-05 through
2026-11-05 produced FOMC, Beige Book, and speech rows but silently omitted the
official October 16 G.17 Industrial Production and Capacity Utilization event,
even though INDUSTRIAL_PRODUCTION is a configured M2 event type. The same
official FOMC item advertises a press conference, but the adapter publishes
only the 2:00 p.m. decision; the design requires the statement/decision and
press conference to remain separate related occurrences because their risk
windows differ. Extend the official adapter and event model/rules as needed,
using official timing and stable identity, and add live-shape regressions that
would fail if either occurrence disappears.

M2-R6-F2 [P1] The new Finnhub sidecar can claim complete covered-empty earnings
when provider rows lack fiscal identity. _validated_events treats fiscal_year
and fiscal_quarter as optional, and run_fetch silently skips such rows while
still writing full coverageStart/coverageEnd. Reproduced with one valid legacy
earnings row lacking those optional columns: events.csv contained one row,
earnings.json contained zero events, and the sidecar claimed the full
2026-10-04 through 2026-12-13 horizon. Preserve the legacy CSV behavior, but
make the calendar projection explicitly incomplete/failing whenever any
in-horizon provider row cannot receive a stable identity. Test partial and
all-missing identity cases plus the successful complete case.

M2-R6-F3 [P1] Upgrading an existing M2 database preserves the obsolete
same_session_cluster relationships. _rebuild_clusters deletes only
strategy_exposure_overlap:* rows, while the API reads every relationship
without filtering. Reproduced by inserting a prior-round same_session_cluster
link between two after-close events and rerunning the new implementation: the
legacy link remained and can still trigger a false overlap warning. Remove or
migrate the obsolete relationship rows transactionally and add an upgrade-path
regression starting from the old relationship name.

M2-R6-F4 [P1] M2-R4-F7 is not fully resolved because _overlaps_exposure checks
only that an event is at or before hard_exit; it never checks the configured
entry time. An exact 8:30 a.m. release therefore returns true for all three
9:30 a.m.-entry strategies and is labeled as an actual strategy-window
overlap. Require entry <= event time <= hard exit for this named rule and test
pre-entry, in-window, and post-exit events. If pre-open clustering is desired,
define a separate named rule and explanation rather than calling it an actual
window overlap.
```

Reviewer verification for R-007:

- independently parsed the current official Federal Reserve JSON for the live
  smoke horizon and verified that the adapter omits the listed G.17 release
  and does not materialize a separate press conference;
- independently reproduced a nonempty legacy earnings cache producing an
  empty, fully covered calendar sidecar when fiscal identity is absent;
- independently reproduced an obsolete same_session_cluster relationship
  surviving a rerun of the new overlap implementation;
- directly verified that an exact 8:30 a.m. release is classified as
  overlapping every strategy whose configured entry is 9:30 a.m.;
- targeted calendar, events, and service tests: 43 passed;
- utilities suite: 791 passed;
- backend suite: 599 passed;
- Angular suite under Node 24: 200 passed;
- Angular production build under Node 24 passed; and
- documentation check, secret scan of 620 tracked objects, and commit-range
  `git diff --check` passed.

Prior review records:

```text
HANDOFF_ID: R-006
MILESTONE: M2
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 7360d27
REVIEWED_COMMITS: bcc31e1, 9e973ef

M2-R4-F1 [P1] The configured live Federal Reserve, Treasury, BEA, Census, and
DOL URLs are HTML or PDF, but the only new parser accepts normalized JSON,
iCalendar, or simple XML and coverage is accepted only from synthetic JSON
coverageStart/coverageEnd fields. The default live command therefore cannot
produce complete M2 coverage; the implementation handoff acknowledges this.
Implement and test provider-specific official-source acquisition/parsing (or
select documented official machine-readable surfaces), derive defensible
31-day coverage for each source, and perform a private manual live smoke test.
Keep raw provider payloads out of the repository.

M2-R4-F2 [P1] A successful covered-empty provider refresh does not cancel
previous rows. _publish_provider calls cancel_missing only for provider names
encountered in observations, so an empty report leaves old scheduled events
active. Reproduced by publishing one Treasury event and then a covered empty
Treasury document: the old event remained scheduled. Reconcile the requested
source/provider even when its seen set is empty and add the exact regression.

M2-R4-F3 [P1] Local rematerialization rewrites provider fetch provenance and
extends provider freshness without any network response. When the provider is
fresh but the materialization identity changes, the cached snapshot is passed
through _publish_provider, which records the current scan time as fetched_at
and last_success_utc. Reproduced over two offline daily rematerializations with
a transport that raises if called: a 2026-10-04 fetch became a purported
2026-10-06 provider success. Separate provider revalidation state from local
rescoring and preserve original source-fact fetch provenance. Add a regression
that advances beyond the freshness interval and proves a network refresh is
then required.

M2-R4-F4 [P1] Finnhub cache age is ignored. events_fetched_as_of is treated as
the beginning of schedule coverage, and every calendar scan records the scan
time as a fresh Finnhub success. Reproduced with a cache fetched on 2026-09-20
and a 2026-10-04 scan: the source was published fresh with last_success_utc on
2026-10-04. Derive provider freshness and event-fact provenance from the
legacy cache metadata, retain its 24-hour rule, and never re-age the cache
during local ingestion. Add fresh, stale, and insufficient-horizon tests.

M2-R4-F5 [P1] Earnings relevance is filtered against every symbol in
universe.csv, not SPY/QQQ constituent membership, and every retained earnings
row is then classified as INDIRECT_BROAD_INDEX for SPY and QQQ. The registry
already exposes membership tags, so a universe-only stock is currently
misrepresented as an index member and can crowd the Priority 1 calendar.
Select entries using applicable index memberships with their available dated
provenance; keep unproven names SINGLE_STOCK/secondary and do not claim a
material weight. Add fixtures covering an index member and a universe-only
stock.

M2-R4-F6 [P1] Earnings identity is based on ticker plus scheduled date for both
source_record_id and canonical key. A reschedule therefore creates a second
event instead of updating the same occurrence and appending schedule history.
Use a stable provider record or fiscal-period identity where the source proves
one. If the legacy artifact cannot provide it, expose the limitation without
publishing false reschedule history and follow the design stop rule before
guessing. Add an earnings-reschedule regression.

M2-R4-F7 [P1] Clustering links every pair of broad events on the same session,
without comparing configured exposure windows, and it does not feed a named
strategy rule. The API nevertheless says the windows may overlap. Implement
the designed overlap-based cluster rule, retain its rule ID and explanation in
strategy assessments, and test same-day non-overlap versus true overlap.
```

Prior review records:

```text
HANDOFF_ID: R-005
MILESTONE: M2
DECISION: RESUME_IMPLEMENTATION
OWNER_DECISION: Use the current Finnhub personal/free-plan access for the
  private, single-user smallFish installation.
EVIDENCE: The existing local cache contains 1,498 Finnhub earnings rows fetched
  on 2026-10-04 with coverage through 2026-12-13. Finnhub documents the
  earnings-calendar capability and states that dataset access follows the
  account plan. Its terms permit personal use but prohibit redistribution and
  require deletion when access to the subscribed data ends.
IMPLEMENTATION_CONSTRAINTS:
  - Retrieve and display Finnhub-derived earnings data only inside the private
    local smallFish installation.
  - Persist only normalized calendar fields needed by Event Risk; do not expose
    Finnhub data through a public or third-party redistribution surface.
  - Preserve Finnhub source attribution and plan/capability diagnostics.
  - Delete Finnhub-derived persisted data if access to that data ends.
  - Stop for a new owner decision before any shared, business, commercial, or
    redistributed use, or if the endpoint rejects the current plan.
NEXT_ACTION: Resume the full M2 kickoff recorded under R-004, including the
  earnings-provider abstraction and existing Finnhub implementation, subject
  to these constraints.
```

Reviewer verification for R-006:

- independently reproduced the covered-empty retention bug;
- independently reproduced provider freshness and source-fact provenance being
  rewritten by offline local rematerialization;
- independently reproduced a stale Finnhub cache being published as fresh;
- verified from the code and live source configuration that the default HTML
  and PDF providers have no applicable parser or coverage proof;
- verified from the registry contract that membership tags are available but
  the earnings filter uses the entire generated universe;
- targeted calendar/service tests: 27 passed;
- utilities suite: 782 passed;
- backend targeted tests: 5 passed; full backend suite: 599 passed;
- Angular suite under Node 24: 200 passed;
- Angular production build under Node 24 passed; and
- documentation check, secret scan of 613 tracked objects, and
  `git diff --check` passed.

Earlier review records:

```text
HANDOFF_ID: R-004
MILESTONE: M1
DECISION: ACCEPTED
REVIEWED_BASE: cdb46dc
REVIEWED_COMMITS: c220273, a41dd9f
FINDINGS_VERIFIED_RESOLVED: M1-R4-F1
ACCEPTED_SCOPE: All Milestone 1 design and verification criteria.
```

Reviewer verification for R-004:

- independently reproduced the former parser-version/304 failure and verified
  that the request is now unconditional, the unexpected 304 fails safely, and
  snapshot/source/event-fact provenance remains at the last successful parser;
- targeted calendar/service tests: 24 passed;
- utilities suite: 780 passed;
- backend suite: 598 passed;
- Angular suite under Node 24: 200 passed;
- Angular production build under Node 24 passed;
- documentation check and `git diff --check cdb46dc..c220273` passed;
- secret scan: 603 tracked working-tree objects passed; and
- the UI was unchanged by this fix; the implementer repeated dedicated desktop
  and narrow route inspection against the v004 artifact, supplementing the
  earlier independent M1 route inspection recorded below.

M2 kickoff for the implementer:

```text
Implement Milestone 2: primary broad-market coverage. Extend the provider-
neutral calendar with the remaining required BLS releases; Federal Reserve
calendar/FOMC events; Treasury refunding and material auctions; BEA; Census;
DOL weekly claims; an earnings-provider abstraction with the existing Finnhub
implementation; event clustering; and source coverage diagnostics for the
full primary calendar.

Keep schedule acquisition separate from released-value acquisition and prefer
the official surfaces listed in the design. Preserve the M1 provider snapshot,
parser/source provenance, freshness, fail-closed refresh, stable identity,
multi-measurement, strategy-assessment, and transaction contracts. The
31-calendar-day scan must distinguish covered-empty from stale, failed,
unconfigured, or insufficient coverage source by source. A failed provider
must retain its last known-good state without making the whole calendar appear
complete.

Rank Priority 1 broad-index risk ahead of secondary events. Add overlap-based
event clustering without a directional forecast or opaque aggregate score;
retain named rules and plain-language benefits/risks for all three strategies.
For earnings, index membership may establish relevance, but do not claim a
company has material SPY/QQQ weight without dated weight data and provenance.
Do not change or widen the legacy events.csv contract, and do not leave a
duplicate active earnings fetch implementation after any approved migration.

Use injected offline fixtures for every provider and add degradation,
coverage, DST, identity/reschedule, deduplication/conflict, clustering, API,
and Angular coverage. Preserve the repository dependency boundary: services
is raw transport, utilities is the only writer, and stock-app remains read-
only with no utilities/studies import. Update documentation and visually
inspect representative desktop and narrow Event Risk states after the full
required test/build checks.

Apply every design stop rule. In particular, stop for an owner decision rather
than guessing if Finnhub storage/display rights are unclear, a source cannot
prove the requested horizon, broad-index earnings importance would require
unavailable weights, authoritative sources conflict without precedence, or
legacy events.csv behavior would change.
```

Prior review records:

```text
HANDOFF_ID: R-003
MILESTONE: M1
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 29227ed
REVIEWED_COMMITS: bf88bbb, e9ad21a
FINDINGS_VERIFIED_RESOLVED: M1-R2-F1, M1-R2-F2

M1-R4-F1 [P1] A provider parser-version change can conditionally reuse a
snapshot produced by the old parser and then relabel both source_sync_state and
event_source_facts with the new parser version. provider_fresh correctly checks
the parser version, but can_conditionally_reuse does not. Reproduced by
publishing under bls-ics-1, changing the configured version to bls-ics-2, and
returning HTTP 304: the second request sent If-None-Match, returned
not_modified, and the unchanged v1 snapshot was recorded as bls-ics-2. This
breaks the design's parser provenance and can preserve facts that a parser fix
was intended to correct. Persist enough provider-snapshot identity to bind the
normalized observations to their parser version and source endpoint. Do not
send conditional validators or accept a 304 for snapshot reuse when that
identity differs; fetch a full body and parse it with the current parser. Add a
regression test proving a parser-version change makes an unconditional request
and never relabels an old normalized snapshot.
```

Reviewer verification for R-003:

- independently reproduced and verified the measurement-preservation fix;
- independently reproduced and verified offline local-policy
  rematerialization;
- independently reproduced M1-R4-F1 with an injected 200 then 304 transport;
- targeted calendar/service tests: 22 passed;
- utilities suite: 778 passed;
- documentation check and `git diff --check 29227ed..bf88bbb` passed; and
- secret scan: 602 tracked working-tree objects passed.

Earlier review records:

```text
HANDOFF_ID: R-002
MILESTONE: M1
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 42ed219
REVIEWED_COMMITS: 03ae668, 791a73a

M1-R2-F1 [P1] A normal schedule refresh without --released-values deletes all
previously persisted measurements. run_sync parses omission as an empty
measurement set, _replace_children deletes event_measurements, and the sync
then marks bls_released_values not_configured. Reproduced by publishing the CPI
fixture with its released-values fixture, then publishing the same schedule
one hour later without the optional file: three measurement rows became zero.
Omitting an optional update must preserve the last successful measurement
facts and their source state; clearing or superseding them needs an explicit
operation. Add this exact two-run regression test.

M1-R2-F2 [P1] Provider freshness and local materialization freshness remain
coupled, so M1-R1-F3 is not fully resolved. Any local input change, including
the daily as-of date, disables provider-cache reuse and forces an unconditional
BLS request. Reproduced by changing the importance policy inside the provider
freshness window and making the transport fail: the cached, covered schedule
was not rematerialized and the stored importance stayed at 5 rather than 4.
Persist/reconstruct the normalized provider observations separately from
derived policy, strategy, ETF, and measurement state. A fresh, sufficiently
covered provider snapshot must be locally rematerializable without network
access; provider conditional-refresh decisions must not depend on the derived
materialization identity. A wider horizon must still fetch enough source data
before claiming coverage. Add offline local-policy and price/as-of
rematerialization tests, plus a test that unchanged local inputs still make no
provider request.
```

Reviewer verification for R-002:

- independently reproduced both findings using temporary databases and
  injected transports;
- utilities suite: 776 passed;
- backend suite: 598 passed;
- Angular suite under Node 24: 200 passed;
- Angular production build under Node 24 passed; and
- `git diff --check 42ed219..03ae668` and documentation checks passed.

The initial attempts to run Angular with the checkout's Node 25 failed before
test/build execution because that Node binary references a missing Homebrew
`libsimdjson.29.dylib`; the required Node 24 runtime passed both commands.

Earlier review record:

```text
HANDOFF_ID: R-001
MILESTONE: M1
DECISION: CHANGES_REQUESTED
REVIEWED_BASE: 42ed219 plus the uncommitted Milestone 1 worktree

M1-R1-F1 [P1] Conditional HTTP 304 handling widens source coverage without
materializing events in the newly covered dates. A wider request must reparse a
retained raw payload, fetch unconditionally, or preserve the old coverage as
insufficient. Add a regression test proving a newly included CPI occurrence is
not lost.

M1-R1-F2 [P1] An unusable target CPI row is skipped, the prior occurrence is
cancelled, and the source is published as fresh. Any CPI reference/time/timezone
parse failure must fail the refresh and preserve prior rows. Handle UTC Z values
without requiring TZID and add degradation tests.

M1-R1-F3 [P1] Fresh-source reuse ignores local materialization inputs including
importance and strategy configuration, ETF mappings, price-cache/as-of state,
and supplied released measurements. Separate provider freshness from derived
materialization freshness, persist a complete input identity, and add tests for
policy changes and newly supplied measurements.

M1-R1-F4 [P2] Complete the UX contract by showing affected asset classes in the
event result and source freshness/coverage in the detail drawer, with Angular
coverage.
```

Reviewer verification for R-001:

- utilities suite: 769 passed;
- backend suite: 598 passed;
- Angular suite: 200 passed;
- targeted calendar/service/API tests: 19 passed;
- Angular production build passed;
- desktop and narrow visual inspection passed with synthetic CPI data; and
- documentation check, secret scan, and `git diff --check` passed.

## Milestone progression

- M1 acceptance requires all Milestone 1 design and verification criteria.
- After M1 is accepted, the reviewer writes the exact M2 kickoff here before
  transferring the lock.
- After M2 is accepted, the reviewer writes the exact M3 kickoff here before
  transferring the lock.
- After M3 is accepted, the reviewer performs one final cross-milestone
  integration, documentation, dependency-boundary, test, and visual review.
