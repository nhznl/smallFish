# Market Event Calendar agent loop

This file is the coordination lock and durable mailbox for the automated
designer/implementer/reviewer loop. Both agents must re-read it immediately
before doing work and immediately before editing it.

## Machine-readable state

```text
PROTOCOL_VERSION: 1
LOOP_STATUS: ACTIVE
CURRENT_MILESTONE: M1
CURRENT_ROUND: M1-R4
NEXT_ACTOR: REVIEWER
LAST_HANDOFF_ID: I-003
M1_STATUS: READY_FOR_REVIEW
M2_STATUS: PLANNED
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
HANDOFF_ID: I-003
MILESTONE: M1
STATE: READY_FOR_REVIEW
BASE_COMMIT: 29227ed
IMPLEMENTATION_COMMITS: bf88bbb
FINDINGS_RESOLVED: M1-R2-F1, M1-R2-F2
CHANGED_FILES: docs/DATA.md, utilities/README.md,
  utilities/market_calendar/database.py,
  utilities/market_calendar/migrations/003_provider_snapshot.sql,
  utilities/market_calendar/sync.py, utilities/tests/test_market_calendar.py.
TESTS_AND_RESULTS:
  - utilities/.venv/bin/python -m pytest -q utilities/tests: 778 passed.
  - stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests:
    598 passed.
  - PATH=/opt/homebrew/opt/node@24/bin:$PATH npm run test:ci: 200 passed.
  - PATH=/opt/homebrew/opt/node@24/bin:$PATH npm run build: passed.
  - targeted calendar/service tests: 22 passed.
  - python3 tools/check_docs.py: 57 Markdown files passed.
  - python3 tools/scan_secrets.py: 601 tracked working-tree objects passed.
  - git diff --check: passed before commit.
VISUAL_VERIFICATION: Loaded /market-calendar from a dedicated port against a
  synthetic v003 SQLite artifact after publishing measurements and then
  republishing the same schedule without a measurement file. Desktop and
  390x844 views passed; headline/core previous and actual values remained
  visible after the omitted update. Confirmed GET /api/market-events came from
  the dedicated verification server before trusting the route.
DESIGN_DEVIATIONS: None.
KNOWN_GAPS: None for M1-R2-F1 or M1-R2-F2. Milestones 2 and 3 remain planned.
```

## Reviewer outbox

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

Prior review record:

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
