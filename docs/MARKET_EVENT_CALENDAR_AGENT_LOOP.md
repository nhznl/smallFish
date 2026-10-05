# Market Event Calendar agent loop

This file is the coordination lock and durable mailbox for the automated
designer/implementer/reviewer loop. Both agents must re-read it immediately
before doing work and immediately before editing it.

## Machine-readable state

```text
PROTOCOL_VERSION: 1
LOOP_STATUS: ACTIVE
CURRENT_MILESTONE: M2
CURRENT_ROUND: M2-R7
NEXT_ACTOR: IMPLEMENTER
LAST_HANDOFF_ID: R-007
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
HANDOFF_ID: I-007
MILESTONE: M2
STATE: READY_FOR_REVIEW
BASE_COMMIT: d22b04a
IMPLEMENTATION_COMMITS: 05f7250
SCOPE: Resolved M2-R4-F1 through M2-R4-F7. Added provider-specific official
  Federal Reserve, Treasury, BEA, Census, and DOL schedule adapters with
  defensible coverage proof; covered-empty cancellation; immutable network
  provenance across local rematerialization; cache-date Finnhub freshness;
  stable fiscal-period earnings identity and dated index-membership relevance;
  actual strategy-exposure overlap clustering with named rules; live-shape
  offline fixtures, regressions, documentation, and plain-text provider labels.
CHANGED_FILES: docs/ARCHITECTURE.md; docs/DATA.md;
  docs/MARKET_EVENT_CALENDAR_DESIGN.md; services/README.md;
  stock-app/app/market_events_read.py; utilities/README.md;
  utilities/config/README.md; utilities/config/market_calendar_sources.yaml;
  utilities/events.py; utilities/market_calendar/__main__.py;
  utilities/market_calendar/etf.py; utilities/market_calendar/primary_sync.py;
  utilities/market_calendar/providers/primary.py;
  utilities/market_calendar/sync.py; earnings, universe, and official-source
  fixtures; utilities/tests/test_events.py; utilities/tests/test_market_calendar.py.
FINDINGS_RESOLVED: M2-R4-F1, M2-R4-F2, M2-R4-F3, M2-R4-F4, M2-R4-F5,
  M2-R4-F6, M2-R4-F7.
TESTS_AND_RESULTS: Final focused official-source and overlap regressions 2
  passed; full utilities suite 791 passed; full backend suite 599 passed;
  Angular Node 24 suite 200 passed; Angular production build and
  ./commands.sh build-ui passed; documentation check passed for 57 files;
  Python compileall, secret scan of 613 pre-commit tracked objects, and
  git diff --check passed.
LIVE_SMOKE: Private manual live scan for 2026-10-05 through 2026-11-05 fetched
  all five official sources without persisting raw payloads: Federal Reserve 5
  rows, Treasury 10, BEA 2, Census 2, and DOL 5. All required sources proved
  horizon coverage and the command exited 0. BLS and Finnhub inputs were local
  fixtures for this acquisition-specific smoke test.
VISUAL_VERIFICATION: Served the normalized live-smoke database from a dedicated
  commands.sh process on port 8012 and inspected Event Risk at desktop and
  390x844. Coverage diagnostics, official-provider events, fiscal-period
  earnings, Treasury refunding, and named overlap warnings rendered; the only
  UI source change remains the corrected overlap-warning wording.
DESIGN_DEVIATIONS: None.
KNOWN_GAPS: Released values beyond the existing BLS path remain a separate,
  explicitly unconfigured capability; M2 schedule coverage no longer depends
  on that optional path. No known blocking M2 implementation gap remains.
```

## Reviewer outbox

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
