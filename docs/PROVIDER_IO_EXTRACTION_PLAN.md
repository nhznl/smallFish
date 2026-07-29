# Provider I/O extraction plan

**Status:** Ready for owner review and implementation handoff. No provider code
has moved yet.

**Owner:** smallFish owner. The intended implementation agent is Terra.

**Relationship to the brokerage refactor:** The Symbol Ledger and
brokerage-neutral API migration are complete. This is a separate architecture
follow-up; it must not recreate any retired ledger, route, trade-group, or
compatibility surface. See
[`BROKERAGE_REFACTOR_PLAN.md`](BROKERAGE_REFACTOR_PLAN.md).

## Resume here

The next implementation step is Phase 0 below. Do not restart any phase from the
brokerage refactor.

This plan extracts provider communication into two top-level packages:

```text
services/
├── tastytrade/
│   ├── __init__.py
│   └── io.py
└── snaptrade/
    ├── __init__.py
    └── io.py
```

They are siblings of `models/` and are importable from both project runtimes
when the relevant SDK is installed. The packages own credentials-to-session
construction and provider calls. They return raw SDK payloads or small transport
envelopes containing raw payloads. They do not normalize brokerage facts, write
artifacts, know FastAPI, or import either application runtime.

Implementation is deliberately ordered as:

1. characterize and correct two provider-boundary defects;
2. extract the `stock-app` Tastytrade calls;
3. extract the `stock-app` SnapTrade calls;
4. migrate the independent `utilities` Tastytrade call path and verification
   probe;
5. enforce the boundary and run both runtimes' full gates.

The shared Tastytrade package couples the two environments' SDK pins by design.
Today both manifests pin `tastytrade==13.2.0`. A future pin change is one change
to two manifests and must pass both Python suites. SnapTrade remains installed
only in the backend environment because `utilities/` has no SnapTrade consumer.

## Objective

Make `services/tastytrade/` and `services/snaptrade/` the only production code
that imports or drives the provider SDKs, while preserving every public API,
artifact schema, sync result field, brokerage identity, and offline behavior.

The result should make the dependency boundary obvious:

```text
stock-app/ ───────┐
                  ├──▶ services/tastytrade/ ──▶ tastytrade SDK
utilities/ ───────┘

stock-app/ ──────────▶ services/snaptrade/  ──▶ SnapTrade SDK

services/* ──returns raw provider payloads──▶ consumer-owned normalization
```

## Current provider call graph

| Consumer | Provider work currently mixed into it | What remains after extraction |
|---|---|---|
| `stock-app/app/options_activity.py` | Tastytrade credentials, `Session`, account lookup, history, positions, DXLink Greeks, market metrics | Tastytrade-to-ledger selection and normalization, CSV writes, trend update, frozen sync response |
| `stock-app/app/retirement_options.py` | Separate Tastytrade sessions for beta and DXLink Greeks | Retirement option selection, normalization, retain-prior-on-miss behavior, CSV writes |
| `stock-app/app/snaptrade_service.py` | SnapTrade credentials, auth/client construction, user registration, portal login, accounts, positions, paginated activities | app-env persistence, CLI presentation, holdings/activity normalization, artifact writes, summaries and trend updates |
| `utilities/options/tastytrade_quotes.py` | Tastytrade credentials, session, DXLink subscription and collection | OCC/dxFeed symbol mapping, quote normalization, `QuoteBatch`, coverage/error metadata |
| `tools/brokerages.py` | Tastytrade verification subprocess constructs and refreshes a session directly | standard-library orchestration and safe human-facing status only |
| `utilities/options/chains.py` | imports `tastytrade` only to read `__version__` | use `importlib.metadata.version()`; no SDK import |

The brokerage adapters remain artifact readers. They must not call the new
services.

## Settled design decisions

1. **Provider-specific packages, no artificial common client.** Tastytrade and
   SnapTrade have different account, paging, and streaming models. Share the
   architectural boundary, not a lowest-common-denominator interface.
2. **Provider I/O only.** A service may load environment-backed credentials,
   construct a session/client, authenticate, page, subscribe, fetch, and return
   raw provider objects. It may use a small dataclass to group raw results. It
   must not calculate P/L, derive lifecycle, normalize symbols into canonical
   facts, select risk policy, or read/write an artifact.
3. **No reverse imports.** `services/` imports neither `stock-app`, `utilities`,
   `studies`, nor their configuration modules. It does not import FastAPI,
   pandas, numpy, or project data contracts.
4. **Credentials come from the process environment.** The shared packages do
   not locate or parse `app.env`; existing entry points remain responsible for
   loading that file. SnapTrade's atomic mode-0600 credential persistence stays
   in `stock-app/app/snaptrade_service.py`.
5. **SDK imports stay lazy.** Importing the FastAPI app, utilities package, or
   either shared service without configured credentials must not authenticate
   or contact a provider. A missing optional integration remains a capability
   state and never blocks startup or navigation.
6. **Raw transport envelopes are not public contracts.** They may contain SDK
   objects and exist only between a service and its in-process consumer. Public
   `/api/brokerages` schemas and persisted CSV schemas do not change.
7. **Consumer policy remains with the consumer.** Examples include Tastytrade's
   single-account requirement, option-event selection, retain-prior-on-miss,
   quote eligibility, archive rules, and Fidelity's public identity despite
   SnapTrade transport.
8. **Tests inject transports.** Service functions accept a client, session,
   streamer, or factory seam suitable for fakes. No automated test opens a
   socket or needs a credential.
9. **Errors are safe at the boundary.** Shared exceptions/results may expose
   provider, operation, and exception type. They must not expose credentials,
   account identifiers, raw response bodies, or an unsanitized provider
   exception message. The original exception may be chained for controlled
   server logging.
10. **The duplicated Tastytrade pin becomes one compatibility contract.** Keep
    the exact pin in both requirements files and add a test that fails when the
    versions diverge. Do not create a third requirements file or merge the two
    virtual environments.

## Proposed service contracts

Exact private helper names may change during implementation, but these public
package responsibilities and result boundaries are fixed.

### `services.tastytrade`

- `TastytradeCredentials`: client secret, refresh token, and `live`/`sandbox`
  environment. Its representation must redact secret fields.
- `load_credentials(environ=None)`: validates `TT_CLIENT_SECRET`,
  `TT_REFRESH_TOKEN`, and `TT_ENV` without reading a file.
- `fetch_account_data(start_date, end_date, ...)`: owns session lifetime and
  returns raw account, transaction, and marked-position payloads. It does not
  choose ledger rows or construct application metadata.
- `fetch_market_metrics(symbols, ...)`: returns raw market-metric objects.
- `fetch_greeks(streamer_symbols, timeout_seconds, ...)`: returns raw DXLink
  Greek events keyed by provider streamer symbol, plus a safe partial/error
  status.
- `fetch_quotes(streamer_symbols, timeout_seconds, batch_size, ...)`: returns
  raw DXLink quote events keyed by provider streamer symbol, plus safe
  per-batch error types. Quote normalization remains in `utilities/`.
- `verify_session(...)`: performs the current read-only refresh used by
  `setup-brokerages.sh verify` and returns only safe status.

The service owns session entry/exit and closes the SDK client on every success
or failure path. It preserves the current partial-result behavior for optional
Greeks, beta, and quote calls.

### `services.snaptrade`

- `SnapTradeCredentials`: client id, consumer key, and optional registered-user
  credentials. Its representation must redact secret fields.
- `load_credentials(environ=None)`, `is_personal_key()`, and the authenticated
  user-argument resolution.
- `register_user(user_id=None, ...)`: returns the raw registration result;
  saving it to `app.env` remains in `snaptrade_service.py`.
- `connection_portal(broker=None, custom_redirect=None, ...)`: returns the raw
  portal result; URL validation and CLI presentation remain in the consumer.
- `list_accounts(...)`: returns raw linked-account payloads.
- `fetch_positions(account_ids=None, ...)`: returns `(account, raw positions)`
  pairs using `get_all_account_positions`.
- `fetch_activities(start_date, end_date, account_ids=None, ...)`: returns
  `(account, raw activities)` pairs and preserves defensive offset/limit
  pagination.

No function places, modifies, or cancels an order. The shared package must not
import a trading endpoint.

## Pre-existing findings to resolve explicitly

These were found while mapping the seam. They are not reasons to widen the
refactor, but the implementation must not preserve them accidentally.

1. `options_activity._credentials()` returns three values, while
   `retirement_options._fetch_tasty_betas()` and `_fetch_tasty_greeks()` unpack
   four. The live optional retirement market-data path therefore fails before
   making its provider call and is swallowed as best-effort. Add a fake-SDK
   regression test and correct this in a focused commit before moving the code.
2. Provider error presentation is inconsistent with the repository's hard
   security rule: some sync reports and the Tastytrade verification probe can
   include provider exception text. Add token/account-id sentinel tests and
   reduce user-facing error detail to the exception type plus stable remediation.
   Keep existing response field names where they are already part of a response
   shape.

Do not combine either correction with unrelated normalization or ledger work.

## Phased implementation

Each phase is one focused commit after its gate passes. Update this document's
dashboard and progress log in that same commit. Preserve any pre-existing
worktree changes and stage only the phase's files.

### Phase 0 — characterize and repair the live boundary

**Changes**

- Add a fake-SDK test that reaches the default retirement beta and Greek
  fetchers without a network call and pins the three-field Tastytrade credential
  contract.
- Correct the erroneous four-value unpack.
- Add secret and account-identifier sentinels to provider-error tests.
- Make existing user-facing provider errors type-only with stable remediation;
  preserve existing response keys and status codes.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_options_activity.py \
  stock-app/tests/test_retirement_options.py
utilities/.venv/bin/python -m pytest -q \
  utilities/tests/test_brokerages.py \
  utilities/tests/test_tastytrade_quotes.py
python3 tools/scan_secrets.py
git diff --check
```

### Phase 1 — shared Tastytrade I/O for `stock-app`

**Changes**

- Add `services/README.md`, `services/__init__.py`, and
  `services/tastytrade/{__init__.py,io.py}`.
- Add `services/tests/conftest.py` with the same `SFP_BLOCK_NETWORK` socket
  guard as both existing suites.
- Add fake-session/streamer service tests for credential validation, session
  closure, account/history/position calls, live-vs-sandbox behavior, partial
  DXLink results, market metrics, timeout, and safe errors.
- Replace the direct Tastytrade SDK calls in `options_activity.py` and
  `retirement_options.py` with service calls.
- Keep normalization, artifact writes, retain-prior-on-miss, and the existing
  sync response shapes in the application modules.
- Update `AGENTS.md` and `docs/ARCHITECTURE.md` in this phase because the
  dependency graph changes as soon as `services/` lands.

**Compatibility checks**

- The same injected raw fixtures produce byte-equivalent activity, position,
  Greek, and beta rows before and after extraction.
- `GET /api/brokerages` and all sync command response shapes remain unchanged.
- Importing `app.main` does not import the Tastytrade SDK or touch the network.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q services/tests/test_tastytrade_io.py
utilities/.venv/bin/python -m pytest -q services/tests/test_tastytrade_io.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_options_activity.py \
  stock-app/tests/test_retirement_options.py \
  stock-app/tests/test_brokerage_api.py \
  stock-app/tests/test_symbol_ledger_api.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

### Phase 2 — shared SnapTrade I/O for `stock-app`

**Changes**

- Add `services/snaptrade/{__init__.py,io.py}` and fake-client tests.
- Move credentials, auth/client construction, registration, portal login,
  account listing, positions, and paginated activities into the shared package.
- Keep `app.env` validation/atomic persistence, CLI output, normalization,
  artifact writes, summaries, option-event orchestration, and trend updates in
  `snaptrade_service.py`.
- Preserve personal-key versus commercial-key behavior and the existing
  `SnapTradeValidationError` status mapping at the application boundary.
- Keep `retirement_options.sync_events(provider=...)` and
  `snaptrade_service.sync(provider=...)` as injectable compatibility seams.

**Compatibility checks**

- Raw fake account/position/activity payloads produce byte-equivalent holdings
  and event artifacts.
- Pagination uses the current page size and stops only on a short page.
- Registration still saves generated credentials atomically at mode 0600 and
  never prints them.
- No SnapTrade SDK import is required to import or navigate the app when the
  integration is absent.

**Gate**

```bash
stock-app/.venv/bin/python -m pytest -q services/tests/test_snaptrade_io.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_snaptrade_service.py \
  stock-app/tests/test_retirement_options.py \
  stock-app/tests/test_brokerage_api.py \
  stock-app/tests/test_symbol_ledger_api.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests/test_brokerages.py
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

### Phase 3 — migrate `utilities` Tastytrade I/O and verification

**Changes**

- Make `utilities/options/tastytrade_quotes.py` delegate credentials, session,
  and raw DXLink quote collection to `services.tastytrade`.
- Keep `streamer_symbol`, quote timestamp normalization, `QuoteBatch`, provider
  coverage, RTH eligibility inputs, and archive behavior in `utilities/`.
- Change the `tools/brokerages.py` Tastytrade subprocess to call the shared
  read-only session verification instead of importing `Session` directly.
- Replace `chains.py`'s SDK import used for version reporting with
  `importlib.metadata.version("tastytrade")`.
- Add a standard-library boundary test that rejects production imports of
  `tastytrade` or `snaptrade_client` outside `services/`.
- Add a pin-parity test for the two `tastytrade` requirement entries.

**Compatibility checks**

- The same raw quote fixtures produce byte-equivalent normalized quote rows and
  `QuoteBatch.metadata()`.
- Partial, unavailable, invalid-symbol, timeout, and off-hours behavior remain
  unchanged.
- `setup-brokerages.sh status` remains network-free and `verify` never prints a
  credential or account identifier.

**Gate**

```bash
utilities/.venv/bin/python -m pytest -q services/tests/test_tastytrade_io.py
utilities/.venv/bin/python -m pytest -q \
  utilities/tests/test_tastytrade_quotes.py \
  utilities/tests/test_chains.py \
  utilities/tests/test_brokerages.py \
  utilities/tests/test_setup_tooling.py
utilities/.venv/bin/python -m pytest -q utilities/tests
./setup-brokerages.sh status
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

### Phase 4 — enforcement and closeout

**Changes**

- Remove migration-only wrappers only after a repository-wide caller audit.
- Update `stock-app/README.md`, `utilities/README.md`,
  `utilities/options/README.md`, `docs/BROKERAGES.md`, and
  `docs/THIRD_PARTY_NOTICES.md` to describe the final ownership accurately.
- Update `.github/workflows/ci.yml` so Tastytrade service tests run once under
  each Python environment and SnapTrade service tests run under the backend
  environment. Keep the existing offline guard job covering both consumer
  suites and the new service tests.
- Record final test totals and commits in the dashboard and progress log.

**Final automated gate**

```bash
stock-app/.venv/bin/python -m pip check
utilities/.venv/bin/python -m pip check
stock-app/.venv/bin/python -m pytest -q services/tests/test_tastytrade_io.py \
  services/tests/test_snaptrade_io.py
utilities/.venv/bin/python -m pytest -q services/tests/test_tastytrade_io.py
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
SFP_BLOCK_NETWORK=1 stock-app/.venv/bin/python -m pytest -q \
  services/tests/test_tastytrade_io.py services/tests/test_snaptrade_io.py
SFP_BLOCK_NETWORK=1 stock-app/.venv/bin/python -m pytest -q \
  --rootdir=stock-app stock-app/tests
SFP_BLOCK_NETWORK=1 utilities/.venv/bin/python -m pytest -q \
  services/tests/test_tastytrade_io.py
SFP_BLOCK_NETWORK=1 utilities/.venv/bin/python -m pytest -q utilities/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

No Angular build or browser checkpoint is required unless an API response or UI
file changes. Such a change is outside this plan and requires owner approval.

## Manual provider verification boundary

Automated tests never use the network. After all automated gates pass, report
the extraction as code-complete but live-provider verification pending. Only
with the owner's explicit permission and the already configured local
credentials, run:

```bash
./setup-brokerages.sh verify
```

Then ask the owner to initiate one Tastytrade sync and one Fidelity sync from
the existing UI. Compare only counts, capability state, artifact schemas, and
safe status fields. Never print, copy, or attach real account identifiers,
positions, provider payloads, or artifact contents.

## Acceptance criteria

- Production imports of `tastytrade` and `snaptrade_client` exist only under
  `services/`.
- `services/` has no import from `stock-app`, `utilities`, `studies`, FastAPI,
  pandas, numpy, or project config modules.
- All existing `/api/brokerages` routes, request/response field names, public
  brokerage ids, capability behavior, and artifact headers are unchanged.
- Tastytrade activity, positions, Greeks, betas, and quote normalization remain
  consumer-owned and match characterized fixtures.
- SnapTrade holdings and activity normalization, pagination, personal/commercial
  auth behavior, and credential persistence match characterized fixtures.
- Optional integrations still do not block startup or navigation.
- Provider-facing functions remain read-only.
- No automated test contacts a provider, and the explicit offline gates pass.
- No user-facing path exposes a credential, account identifier, raw payload, or
  unsanitized provider exception text.
- Both Tastytrade pins are identical and both Python suites pass whenever that
  pin or the shared Tastytrade package changes.
- Documentation, secret scan, and diff checks pass.

## Non-goals

- No change to Symbol Ledger accounting, lifecycle, archives, or UI.
- No restoration of legacy brokerage routes, grouped options projections, or
  trade-group artifacts.
- No provider-neutral brokerage SDK abstraction.
- No new database, service process, HTTP hop, packaging system, or merged venv.
- No utility-to-FastAPI or FastAPI-to-utility import.
- No SDK upgrade during extraction.
- No order placement or brokerage mutation capability.
- No migration of the 2,000-line chain pipeline beyond its provider-I/O call.
- No live-provider calls in tests or without explicit owner permission.

## Stop and escalation rules

Pause and ask the owner before proceeding if:

- preserving behavior requires changing an API or artifact schema;
- the current SDK cannot return a raw payload after session closure;
- a provider call needs a permission broader than the documented read-only
  operations;
- exact Tastytrade pins cannot remain aligned across both environments;
- extraction would require `services/` to import application configuration or
  persistence code;
- a test suggests changing financial, lifecycle, quote-eligibility, or archive
  semantics;
- real provider data would be required to establish correctness.

## Phase dashboard

| Phase | Scope | Status | Next action / blocker | Evidence |
|---|---|---|---|---|
| 0 | Boundary characterization and two corrective fixes | COMPLETE | Phase 1 can begin | 32 backend + 40 utilities targeted tests; secret scan and diff check clean |
| 1 | Shared Tastytrade I/O for backend | NOT STARTED | Phase 0 passed | — |
| 2 | Shared SnapTrade I/O for backend | NOT STARTED | Phase 1 must pass | — |
| 3 | Utilities and verification migration | NOT STARTED | Phase 2 must pass | — |
| 4 | Enforcement, docs, full regression | NOT STARTED | Phase 3 must pass | — |

## Progress log

| Date | Phase | Status | Evidence / decision | Next action |
|---|---|---|---|---|
| 2026-07-29 | Planning | COMPLETE | Current SDK imports, runtime manifests, consumer injection seams, CI gates, and security boundaries audited; focused plan prepared. No implementation code changed. | Owner review, then hand off Phase 0 to Terra |
| 2026-07-29 | 0 | COMPLETE | Corrected the retirement beta/Greeks three-value credential unpack; fake SDK regression coverage proves the default fetchers stay offline and close sessions/streamers. Provider reports, verification output, and quote metadata now expose only exception type plus stable remediation, with token/account sentinel coverage. 32 backend and 40 utilities targeted tests passed. | Phase 1 |

## Terra kickoff prompt

```text
Implement the provider-I/O extraction described in
docs/PROVIDER_IO_EXTRACTION_PLAN.md.

Read, in order:
1. AGENTS.md
2. Requirements.md
3. docs/PROVIDER_IO_EXTRACTION_PLAN.md
4. the "Resume here" and "Retained on purpose" sections of
   docs/BROKERAGE_REFACTOR_PLAN.md
5. stock-app/README.md, utilities/README.md, and docs/ARCHITECTURE.md

The Symbol Ledger refactor and legacy cleanup are complete. Do not restart
them and do not recreate any retired route, projection, grouping concept, or
CSV config path.

Begin with Phase 0. Work one phase and one focused commit at a time. Run the
phase gate before committing, update the plan dashboard/progress log in the
same commit, and inspect git status before staging. Preserve pre-existing user
changes. Do not push or open a pull request.

The boundary is strict: services/ owns provider authentication, sessions,
pagination/streaming, fetches, and raw payload envelopes. Consumer modules own
normalization, financial/lifecycle policy, artifact writes, and public API
shapes. Both test suites remain offline. Never print a credential, account
identifier, raw provider payload, or unsanitized provider exception.

Pause only at a stop condition in the plan. After Phase 4, report automated
verification separately from optional live-provider verification; do not make
a live call without explicit owner permission.
```
