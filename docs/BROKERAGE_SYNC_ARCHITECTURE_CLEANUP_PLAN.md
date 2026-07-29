# Brokerage sync architecture cleanup plan

**Status:** Ready for implementation handoff.

**Owner:** smallFish owner. The implementation agent may work autonomously
within the settled boundaries below and must pause at a listed stop condition.

## Resume here

Begin with Phase 3. Work one phase and one focused commit at a time. Update the
dashboard and progress log in the same commit as each completed phase. Do not
restart the completed Symbol Ledger, common brokerage API, legacy-route
retirement, or provider-I/O extraction projects.

## Objective

Clean up the backend application layer between the provider-neutral brokerage
registry and the completed `services/` transport boundary.

The result must:

1. execute each requested Fidelity sync resource exactly once;
2. make holdings, activity, market-data, setup/CLI, and orchestration ownership
   explicit;
3. remove the circular dependency between `snaptrade_service.py` and
   `retirement_options.py`;
4. delete proven-dead group/projection/enrichment remnants;
5. retire `retirement_options.py` as an implementation module;
6. reduce `snaptrade_service.py` to a documented compatibility facade for its
   existing CLI/module entry points, unless the owner separately authorizes
   breaking that command path; and
7. preserve every public API, artifact, accounting, lifecycle, security, and
   offline-test contract.

This is application architecture cleanup. Provider transport is already
complete in `services/` and is not being redesigned.

## Why this cleanup is needed

The current filenames reflect the order in which integrations were added, not
the responsibilities the modules now own:

- `stock-app/app/retirement_options.py` imports SnapTrade activity, reads the
  SnapTrade holdings ledger, and uses Tastytrade for beta and Greeks. It is
  neither a provider module nor a general retirement-domain module.
- `stock-app/app/snaptrade_service.py` no longer owns the SnapTrade SDK boundary.
  It mixes setup CLI behavior, credential persistence, raw-payload adaptation,
  holdings materialization, summaries, trend updates, and orchestration.
- `stock-app/app/options_activity.py` is the existing Tastytrade application
  materializer. Creating empty `trading_options.py` or
  `tastytrade_service.py` counterparts would add cosmetic symmetry without
  clarifying ownership.

There is also one concrete behavior problem. The Angular client posts an empty
body to `POST /api/brokerages/fidelity/sync`, which requests all supported
resources. The registry currently executes:

```text
HOLDINGS     -> snaptrade_service.sync()
                  -> holdings
                  -> retirement_options.sync_events()
                  -> retirement_options.sync_market_data()
ACTIVITY     -> retirement_options.sync_events()
MARKET_DATA  -> retirement_options.sync_market_data()
```

Therefore a normal Fidelity sync can fetch activity twice and, when option
positions exist, fetch Tastytrade beta and Greeks twice. Idempotent artifact
writes hide the duplication, but they do not justify duplicate provider calls.

Tastytrade does not have this problem: all three registry resources map to one
callable and `brokerages.sync.run()` deduplicates it by callable identity.

## Authority and required reading

Read in this order before changing code:

1. `AGENTS.md`
2. `Requirements.md`
3. this document
4. `docs/ARCHITECTURE.md`
5. `docs/BROKERAGES.md`
6. `stock-app/README.md`
7. `services/README.md`
8. the current code and tests named in the phase being implemented

When documentation and code disagree, follow `AGENTS.md`: committed code and
configuration are authoritative, then update the documentation in the same
change.

## Settled boundaries

These are constraints, not design questions.

### Provider transport

- Production imports of `tastytrade` and `snaptrade_client` remain confined to
  `services/`.
- `services/` continues to own environment-backed provider credentials,
  sessions/clients, streaming/pagination, provider calls, and raw payload
  envelopes.
- `services/` stays standard-library-only apart from lazy provider SDK imports.
  It must not import FastAPI, pandas, numpy, `stock-app`, `utilities`, `studies`,
  project configuration, artifacts, or financial policy.
- No order-placement API may be added or exposed.

### Application materialization

- Backend materializers own raw-payload normalization, lifecycle/accounting
  policy, artifact writes, summaries, and safe application errors.
- Brokerage adapters remain read-only. They consume materialized artifacts and
  never call `services/` or a provider.
- The registry remains the only place where a public brokerage identity selects
  implementation behavior.
- A resource command must perform only its named resource. Holdings may not
  secretly fetch activity or market data.
- A deliberate all-resource compatibility command is orchestration, not a
  holdings materializer, and must be named/documented accordingly.

### Compatibility

- Do not change any `/api/brokerages` path, request body, response shape,
  schema name/version, status code, availability vocabulary, or safe-detail
  allowlist.
- Keep `POST /options/activity/sync` and manual-reconciliation routes unchanged.
- Keep all current CSV paths, headers, row meanings, sort order, timestamps,
  immutable-event identity, retain-prior-on-miss behavior, and atomic-write
  semantics unchanged.
- Preserve `snaptrade_service.sync(provider=...)`,
  `retirement_options.sync_events(provider=...)`, and market-data fetcher
  injection behavior until replacement seams have equivalent characterization
  coverage. The final internal location may change.
- Preserve the documented `python -m app.snaptrade_service` command path through
  a thin compatibility facade. Removing it requires a separate owner decision.
- Missing credentials remain capability/configuration states; optional
  integrations never block application import, startup, or navigation.

### Financial and lifecycle behavior

- Do not change cash-flow signs, option multipliers, position identity,
  account boundaries, short-call coverage, beta/Greek selection, mark handling,
  lifecycle completeness, archive behavior, or trend calculations.
- SnapTrade option events remain immutable and keyed by provider activity ID.
- Market-data refresh keeps prior beta/Greek observations when the provider
  omits a currently held symbol/contract and drops observations only when the
  holding itself is no longer current.
- Optional activity/market-data failure must not corrupt a successful holdings
  artifact. Existing API resource-level reporting remains intact.

## Target ownership

Use this target unless Phase 0 proves a dependency makes it unsafe:

```text
stock-app/app/
├── brokerages/
│   ├── registry.py                     # public identity -> resource commands
│   ├── sync.py                         # provider-neutral execution/reporting
│   ├── adapters/                       # read-only artifact -> canonical facts
│   └── importers/
│       ├── __init__.py
│       ├── snaptrade.py                # holdings + activity materialization
│       └── held_option_market_data.py  # held contracts -> beta/Greeks artifacts
├── options_activity.py                 # existing Tastytrade application flow
├── snaptrade_setup.py                  # register/connect/accounts + env persistence
└── snaptrade_service.py                # thin legacy CLI/module compatibility facade
```

Detailed ownership:

### `brokerages.importers.snaptrade`

- SnapTrade raw-value helpers used during normalization.
- Holdings and activity artifact headers, readers, atomic writers, and
  normalization.
- `sync_holdings(provider=...)` and `sync_activity(provider=..., ...)` as
  independent resource commands.
- Holdings summary/change counts and the thin provider-specific input mapping
  into the shared trend engine.
- No Tastytrade calls and no setup credential persistence.

### `brokerages.importers.held_option_market_data`

- Reads current option legs from the materialized holdings artifact.
- Determines exact underlying and contract requests.
- Calls `services.tastytrade` for beta and Greeks.
- Normalizes and atomically writes the existing retirement beta/Greek
  artifacts.
- Preserves retain-prior-on-miss and independent best-effort beta/Greek results.
- Contains no SnapTrade SDK/client operation.

### `snaptrade_setup`

- Registration, connection portal, account listing, safe errors, atomic
  mode-0600 credential persistence, and CLI presentation for setup commands.
- Delegates provider calls to `services.snaptrade`.
- Does not normalize or write holdings/activity artifacts.

### `snaptrade_service` compatibility facade

- Preserves the documented module/CLI path while callers transition.
- Re-exports only the compatibility names proven necessary by Phase 0.
- Its legacy `sync` entry point may explicitly orchestrate the three new
  resource commands once each and preserve the old CLI output/exit behavior.
- It contains no provider normalization, financial policy, artifact schema, or
  duplicate implementation.
- A facade is acceptable; a second implementation is not.

### `options_activity`

Keep it in place in this cleanup. It combines the existing Tastytrade sync,
manual reconciliation, and compatibility API behavior. Renaming or splitting
it would broaden this work and provide no benefit required to fix the Fidelity
ownership problem.

## Explicitly out of scope

- Renaming public brokerage IDs, registry resources, routes, schemas, fields,
  artifacts, or Angular concepts.
- Reworking Symbol Ledger lifecycle, archive/reset behavior, holdings
  projections, option-adjusted basis, risk formulas, or manual reconciliation.
- Creating `trading_options.py` or `tastytrade_service.py` solely for symmetry.
- Changing provider SDK versions, credentials, session/client behavior, or the
  completed `services/` boundary except for import updates required by moved
  consumers.
- Adding a database, queue, dependency-injection framework, repository pattern,
  abstract factory, or generalized plugin system.
- Live provider verification. All implementation tests remain offline; an
  optional owner-authorized smoke test happens only after automated acceptance.
- UI changes. The public API contract must remain byte-compatible enough that
  no Angular change is needed.

## Phase 0 — characterize ownership and call counts

**Changes**

- Add an application-level characterization test for an empty-body Fidelity
  sync, proving the current public response shape and recording provider/resource
  call counts.
- Add resource-specific cases:
  - `HOLDINGS` requests positions once and no activity/beta/Greeks;
  - `ACTIVITY` requests activities once and no positions/beta/Greeks;
  - `MARKET_DATA` reads the existing holdings artifact and requests each enabled
    Tastytrade market input once without a SnapTrade call;
  - all resources run in `HOLDINGS`, `ACTIVITY`, `MARKET_DATA` order, once each.
- Characterize the existing `python -m app.snaptrade_service` subcommands,
  public error/status behavior, CLI secret redaction, sync return shape, and
  injected provider seams before moving them.
- Add byte-level or parsed-row equivalence fixtures for holdings, option events,
  beta, and Greeks artifacts.
- Record every production caller of public and private names in
  `snaptrade_service.py` and `retirement_options.py`. Classify each as move,
  compatibility re-export, or dead.

The duplicate-call characterization should expose the current defect before it
is corrected. Do not make a live provider call.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_brokerage_api.py \
  stock-app/tests/test_brokerage_adapters.py \
  stock-app/tests/test_brokerage_adapter_contract.py \
  stock-app/tests/test_snaptrade_service.py \
  stock-app/tests/test_retirement_options.py
python3 tools/scan_secrets.py
git diff --check
```

## Phase 1 — remove proven-dead remnants

**Changes**

- Delete production-dead retirement group projection helpers, including
  `_group_name`, `_build_groups`, their group-only row fields, and tests whose
  only purpose is the retired projection.
- Remove unused retirement imports and empty legacy section scaffolding.
- Delete proven-dead SnapTrade enrichment/summary remnants such as
  `UNCLASSIFIED`, `_read_enrichment`, and `_round2` after a fresh reference and
  external-command audit confirms no consumer.
- Do not delete `snapshot`, CLI entry points, provider injection seams, artifact
  readers, headers, or response counters merely because only tests or a CLI use
  them.
- Update module documentation to describe only live responsibilities.

This phase is deletion only. Do not move code or alter orchestration here.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_snaptrade_service.py \
  stock-app/tests/test_retirement_options.py \
  stock-app/tests/test_brokerage_adapters.py \
  stock-app/tests/test_brokerage_adapter_contract.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

## Phase 2 — make resource commands single-purpose

**Changes**

- Separate the holdings-only implementation from the legacy all-resource
  `snaptrade_service.sync` behavior.
- Point Fidelity registry resources at independent commands:

  ```text
  HOLDINGS     -> sync_holdings
  ACTIVITY     -> sync_activity
  MARKET_DATA  -> sync_held_option_market_data
  ```

- Remove activity and market-data calls from the holdings resource command.
- Preserve the registry's existing order so a full sync materializes holdings
  before activity and market data.
- Preserve the legacy `snaptrade_service.sync(provider=...)` seam as an explicit
  compatibility orchestrator for this phase. It must call each related resource
  at most once and retain its current best-effort behavior and summary shape.
- Add regression tests proving an empty-body Fidelity API sync makes no duplicate
  activity, beta, or Greek calls.
- Keep safe error reporting and resource-level statuses unchanged.

**Acceptance checks**

- A full Fidelity sync performs one positions fetch, one activity fetch, one
  beta fetch, and one Greek stream when all are applicable.
- Requesting one resource never invokes either sibling resource.
- Tastytrade's existing one-call deduplication remains unchanged.
- Repeating a successful sync remains idempotent and produces the same ledger
  rows and change counters.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_brokerage_api.py \
  stock-app/tests/test_snaptrade_service.py \
  stock-app/tests/test_retirement_options.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

## Phase 3 — move materialization into explicit modules

**Changes**

- Create `stock-app/app/brokerages/importers/` and move the characterized code
  into the target modules.
- Move SnapTrade holdings/activity normalization and artifact ownership into
  `brokerages.importers.snaptrade`.
- Move current-held-option selection and Tastytrade beta/Greek materialization
  into `brokerages.importers.held_option_market_data`.
- Replace cross-module calls to private helpers such as
  `snaptrade_service._value`, `_text`, and `_read_ledger` with owned public
  importer functions or small local helpers. Do not introduce a generic helper
  abstraction unless at least two live materializers genuinely share the same
  semantics.
- Update `SnapTradeAdapter` and the registry to import artifact contracts and
  resource commands from their new owners.
- Rename tests to match their new subjects while preserving characterization
  coverage and fake-provider injection.
- Delete `retirement_options.py` once production and test reference sweeps are
  empty.

**Compatibility checks**

- Artifact headers and normalized rows are identical before and after the move.
- Adapter snapshots and every `/api/brokerages/fidelity/*` response remain
  unchanged for the same fixture artifacts.
- Importing the FastAPI application with no brokerage SDK or credentials remains
  network-free and successful.
- No adapter or projection imports `services/`.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q services/tests/test_snaptrade_io.py \
  services/tests/test_tastytrade_io.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_brokerage_api.py \
  stock-app/tests/test_brokerage_adapters.py \
  stock-app/tests/test_brokerage_adapter_contract.py \
  stock-app/tests/test_snaptrade_service.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

## Phase 4 — isolate setup/CLI and finish compatibility facade

**Changes**

- Move registration, portal, accounts, environment persistence, and CLI
  presentation to `snaptrade_setup.py`.
- Keep `python -m app.snaptrade_service register|connect|accounts|sync|snapshot`
  working through the thin facade.
- Update `tools/brokerages.py` and first-party documentation to use the new
  setup owner where doing so does not change user-facing commands. The old
  module path remains accepted for compatibility.
- Limit the facade to imports/re-exports and `_main` delegation. Add a structural
  test that rejects normalization, artifact schemas, provider calls, or
  financial policy returning to it.
- Remove obsolete test/module names and update `stock-app/README.md`,
  `services/README.md`, `docs/ARCHITECTURE.md`, and `docs/BROKERAGES.md` to the
  final ownership model.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_snaptrade_service.py \
  stock-app/tests/test_brokerage_api.py \
  stock-app/tests/test_capabilities.py
utilities/.venv/bin/python -m pytest -q utilities/tests/test_brokerages.py
./setup-brokerages.sh status
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

`./setup-brokerages.sh status` must remain network-free and must not print an
unmasked credential or identifier.

## Phase 5 — final enforcement and regression

**Changes**

- Add or extend structural tests that enforce:
  - provider SDK imports only under `services/`;
  - read adapters do not import/call provider transport;
  - holdings resource commands do not invoke activity or market-data commands;
  - no production reference to `retirement_options` remains;
  - `snaptrade_service` stays a thin compatibility facade;
  - duplicated provider pins and existing service tests remain intact.
- Run a final caller/reference sweep for all moved names and deleted helpers.
- Update this document's dashboard and progress log with exact test counts and
  any deliberate deviation from the proposed file layout.

**Final automated gate**

```bash
stock-app/.venv/bin/python -m pip check
utilities/.venv/bin/python -m pip check

stock-app/.venv/bin/python -m pytest -q services/tests/test_snaptrade_io.py \
  services/tests/test_tastytrade_io.py
utilities/.venv/bin/python -m pytest -q services/tests/test_tastytrade_io.py

stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests

SFP_BLOCK_NETWORK=1 stock-app/.venv/bin/python -m pytest -q \
  --rootdir=stock-app stock-app/tests
SFP_BLOCK_NETWORK=1 utilities/.venv/bin/python -m pytest -q utilities/tests

./setup-brokerages.sh status
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

No Angular change is expected. If an Angular file changes, run `npm run build`,
`npm run test:ci`, and browser-check both brokerage routes; explain why the
backend compatibility promise was insufficient.

## Stop conditions

Pause and ask the owner before proceeding if:

- preserving `python -m app.snaptrade_service` would require maintaining a
  second implementation rather than a thin facade;
- any moved artifact differs in headers, normalized values, row ordering,
  timestamps, retention, or atomic-write behavior;
- removing duplicate calls changes a public API status, result shape, or safe
  detail unexpectedly;
- the work would require renaming/migrating existing brokerage artifacts;
- a provider SDK limitation appears to require transport logic outside
  `services/`;
- a proposed cleanup changes accounting, lifecycle, risk, archive, or trend
  semantics;
- real provider data is needed to establish correctness; or
- unrelated user changes overlap a file that must be moved.

## Completion criteria

The cleanup is complete only when:

- every Fidelity resource command is single-purpose and executes once per
  request;
- the normal empty-body Fidelity sync makes no duplicate provider calls;
- `retirement_options.py` is deleted with no production reference remaining;
- `snaptrade_service.py` is a thin compatibility facade, not a materializer;
- setup/CLI, SnapTrade materialization, held-option market data, registry
  orchestration, and read adapters each have one clear owner;
- all existing public routes, response shapes/statuses, artifacts, and financial
  semantics are preserved;
- both complete Python suites pass normally and with network blocking;
- documentation, secret, dependency, brokerage-status, and diff gates pass; and
- no live provider call was made without explicit owner authorization.

## Phase 0 caller classification

Production callers only. Tests are consumers of the characterization, not
owners. Classifications feed Phases 1–4; do not delete a `DEAD` name until
Phase 1 re-confirms no external command reference.

### `snaptrade_service.py`

| Name | Production callers | Classification |
|---|---|---|
| `HOLDINGS_HEADERS` | adapters/tests write helpers | MOVE → `brokerages.importers.snaptrade` |
| `SnapTradeValidationError` | setup/CLI paths | MOVE → `snaptrade_setup`; COMPAT re-export |
| `register_user`, `connection_portal_url`, `list_accounts` | CLI; `tools/brokerages.py` verify snippet | MOVE → `snaptrade_setup`; COMPAT re-export |
| `_shell_quote`, `_validate_registration_target`, `_save_registration_credentials`, `_account_summary` | setup/CLI only | MOVE → `snaptrade_setup` |
| `fetch_snaptrade`, `fetch_activities` | default providers for sync/activity | MOVE → importer; COMPAT re-export until seams migrate |
| `_value`, `_text`, `_read_ledger` | `retirement_options`; `SnapTradeAdapter` | MOVE → public importer helpers |
| `_now`, `_decimal`, `_num`, `_atomic_write`, normalization/`_summarize`/`_sync_changes`/`_update_trend` | holdings materialization | MOVE → `brokerages.importers.snaptrade` |
| `sync` | registry `HOLDINGS`; CLI | COMPAT orchestrator (Phase 2), then facade |
| `snapshot` | CLI | MOVE → importer; COMPAT re-export |
| `_main` | `python -m app.snaptrade_service` | COMPAT facade |
| `UNCLASSIFIED`, `_read_enrichment`, `_round2` | none in production | DEAD — deleted in Phase 1 |
| `SOURCE`, `OPTION_MULTIPLIER` | holdings normalization only | MOVE with holdings |

### `retirement_options.py`

| Name | Production callers | Classification |
|---|---|---|
| `EVENT_HEADERS`, `sync_events`, `_normalize_activity`, `_read_events` | registry `ACTIVITY`; holdings side-effect; `SnapTradeAdapter` | MOVE → `brokerages.importers.snaptrade` (`sync_activity`) |
| `BETA_HEADERS`, `GREEKS_HEADERS`, `sync_betas`, `sync_greeks`, `sync_market_data`, `_option_rows`, `_fetch_tasty_*` | registry `MARKET_DATA`; holdings side-effect | MOVE → `brokerages.importers.held_option_market_data` |
| `_read_rows`, `_atomic_write`, `_epoch_ms_to_iso`, `_greek_key`, share-coverage helpers | adapter + market-data path | MOVE with market-data / activity owners |
| `RetirementOptionsError` | `sync_events` validation | MOVE with activity; rename only if a public import requires it |
| `_group_name`, `_build_groups` | tests only (`test_build_groups_*`); no production caller | DEAD — deleted in Phase 1 |
| Unused imports (`pandas`, `yaml`, `build_market_inputs`, risk-engine symbols except `apply_call_coverage`) | none | DEAD — deleted in Phase 1 |
| Empty legacy section scaffolding / retired `snapshot` | gone from production; dead `_group` test helper remains | DEAD — deleted in Phase 1 |

### Current Fidelity call counts (empty body)

Documented by `stock-app/tests/test_fidelity_sync_characterization.py`:

| Seam | Count today | Target after Phase 2 |
|---|---|---|
| positions (`fetch_snaptrade`) | 1 | 1 |
| activities (`fetch_activities`) | 1 | 1 |
| betas (`sync_betas`) | 1 | 1 |
| greeks (`sync_greeks`) | 1 | 1 |
| registry commands | HOLDINGS → ACTIVITY → MARKET_DATA | unchanged order, once each |

## Phase dashboard

| Phase | Scope | Status | Evidence / next action |
|---|---|---|---|
| 0 | Characterize ownership, compatibility, and provider call counts | COMPLETE | 13 characterization tests; golden artifacts under `stock-app/tests/fixtures/brokerage_sync/`; caller table above |
| 1 | Delete proven-dead remnants | COMPLETE | Removed `_group_name`/`_build_groups`, group-only row fields, unused retirement imports/scaffolding, and `UNCLASSIFIED`/`_read_enrichment`/`_round2`; deleted dead-only group/snapshot test helpers |
| 2 | Make resource commands single-purpose | COMPLETE | Registry: `sync_holdings` / `sync_activity` / `sync_held_option_market_data`; empty-body sync is 1/1/1/1; legacy `sync` orchestrates once each |
| 3 | Move materialization into explicit modules | NOT STARTED | Begin here |
| 4 | Isolate setup/CLI and finish compatibility facade | NOT STARTED | Blocked on Phase 3 |
| 5 | Enforcement, docs, and full regression | NOT STARTED | Blocked on Phase 4 |

## Progress log

| Date | Phase | Status | Evidence / decision | Next action |
|---|---|---|---|---|
| 2026-07-29 | Planning | COMPLETE | Current callers, registry commands, provider boundaries, dead remnants, CLI compatibility, and duplicate Fidelity orchestration were audited. The completed brokerage/provider refactor plans and unused coordination mailbox were retired. | Hand Phase 0 to the implementation agent |
| 2026-07-29 | 0 | COMPLETE | Added `test_fidelity_sync_characterization.py` (13 tests): empty-body duplicate call counts (positions 1 / activities 2 / betas 2 / greeks 2), per-resource cases, CLI surface + secret redaction, sync return shape, and golden CSV fixtures for holdings/events/betas/greeks. Caller classification recorded above. Gate suites green; no production behavior change. | Phase 1 — delete proven-dead remnants |
| 2026-07-29 | 1 | COMPLETE | Deleted dead group projection helpers and enrichment remnants; cleaned unused imports and empty scaffolding; removed `test_build_groups_*` and retired snapshot helpers. Fresh reference audit confirmed no production callers. Full stock-app suite + docs/secret gates green. | Phase 2 — single-purpose resource commands |
| 2026-07-29 | 2 | COMPLETE | Split `sync_holdings` from legacy `sync`; registry points at holdings/activity/market-data commands; empty-body Fidelity sync is one positions/activity/beta/greeks call each. Characterization updated; 459 stock-app tests pass. | Phase 3 — move materialization into importers |

## Implementation-agent kickoff prompt

```text
Implement the focused brokerage sync architecture cleanup described in
docs/BROKERAGE_SYNC_ARCHITECTURE_CLEANUP_PLAN.md.

Read the plan's required-reading list in order. The common brokerage API,
Symbol Ledger, legacy-route retirement, and services provider-I/O boundary are
complete. Do not reopen or redesign them.

Begin with Phase 0. Work one phase and one focused commit at a time. Before each
commit, run that phase's gate, update the phase dashboard and progress log, and
inspect git status so pre-existing user changes are preserved. Do not push or
open a pull request.

The primary correction is exact resource ownership: a Fidelity sync must fetch
HOLDINGS, ACTIVITY, and MARKET_DATA once each, and a single-resource request
must never invoke a sibling resource. Preserve the existing public API,
artifacts, financial/lifecycle semantics, safe errors, offline tests, and the
documented python -m app.snaptrade_service compatibility path.

Do not create trading_options.py or tastytrade_service.py for cosmetic
symmetry. Pause only at a stop condition in the plan, and never make a live
provider call without explicit owner permission.
```
