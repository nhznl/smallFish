# Market Event Calendar agent loop

This file is the coordination lock and durable mailbox for the automated
designer/implementer/reviewer loop. Both agents must re-read it immediately
before doing work and immediately before editing it.

## Machine-readable state

```text
PROTOCOL_VERSION: 1
LOOP_STATUS: BLOCKED
CURRENT_MILESTONE: M2
CURRENT_ROUND: M2-R2
NEXT_ACTOR: NONE
LAST_HANDOFF_ID: I-005
M1_STATUS: ACCEPTED
M2_STATUS: OWNER_DECISION_REQUIRED
M3_STATUS: PLANNED
OWNER_DECISION_REQUIRED: YES
STOP_REASON: FINNHUB_STORAGE_DISPLAY_RIGHTS_UNCONFIRMED
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
HANDOFF_ID: I-005
MILESTONE: M2
STATE: OWNER_DECISION_REQUIRED
BASE_COMMIT: 9ec13c3
IMPLEMENTATION_COMMITS: NONE
STOP_RULE: Finnhub storage, display, estimates, and redistribution rights
  depend on the selected plan, and the M2 kickoff requires an owner decision
  when those rights are unclear.
OWNER_QUESTION: Should M2 use Finnhub after you confirm that the selected plan
  permits smallFish to persist normalized earnings schedule and measurement
  data in local SQLite and display it in the local Event Risk UI, or should
  earnings remain excluded until a rights-cleared provider is selected?
CHANGED_FILES: Coordination record only.
TESTS_AND_RESULTS: Not run; implementation did not start because the approved
  design stop rule was reached before any application change.
VISUAL_VERIFICATION: Not applicable; no application or UI change was made.
DESIGN_DEVIATIONS: None.
KNOWN_GAPS: All M2 implementation remains pending the owner decision.
```

## Reviewer outbox

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
