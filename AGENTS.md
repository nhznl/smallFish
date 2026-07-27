# smallFish agent instructions

Operational rules for coding agents. Applies recursively from the repository
root. `stock-app-ui/AGENTS.md` adds UI-specific rules for work under that
directory.

For product context, setup, and architecture, read [`README.md`](README.md) and
the module READMEs linked below rather than duplicating them here.

## Repository map and dependency direction

```
stock-app-ui/  ──HTTP──▶  stock-app/  ──reads──▶  data/  ◀──writes──  utilities/, studies/
                              │                                            │
                              └────────────▶  models/  ◀──────────────────┘
```

| Path | Owns | README |
|---|---|---|
| `models/` | Standard-library-only shared data contracts | [models/README.md](models/README.md) |
| `utilities/` | Batch pipeline: scraper, universe, indicators, options | [utilities/README.md](utilities/README.md) |
| `studies/` | Research studies and materialization | [studies/README.md](studies/README.md) |
| `stock-app/` | FastAPI backend | [stock-app/README.md](stock-app/README.md) |
| `stock-app-ui/` | Angular 20 dashboard | [stock-app-ui/README.md](stock-app-ui/README.md) |
| `tools/` | Repo tooling: preflight, doctor, brokerages, secret scan | — |

**The dependency direction is a hard rule.** `models/` imports nothing from the
project and uses only the standard library. `stock-app/` must never import
`utilities/` or `studies/` — it consumes generated artifacts under
`SFP_DATA_DIR`. Adding such an import couples the two Python environments and
breaks the split. If you think you need it, you need a new artifact instead.

## Runtimes

Three, and they are separate on purpose:

| Runtime | Interpreter | Use for |
|---|---|---|
| utilities + studies | `utilities/.venv/bin/python` | scraper, universe, options, studies, `utilities/tests` |
| FastAPI backend | `stock-app/.venv/bin/python` | the API and `stock-app/tests` |
| Angular 20 | Node 20 LTS via `npm` | the dashboard |

`tools/` is standard-library-only and runs on the system interpreter, because it
must work *before* either virtual environment exists. Keep it that way.

Do not merge the two Python environments, and do not add a dependency to one
because the other has it. Minimum versions live in
[docs/SUPPORT_MATRIX.md](docs/SUPPORT_MATRIX.md) and are enforced by
`tools/preflight.py`; change them in both places or not at all.

## Canonical commands

```bash
./setup.sh                       # non-interactive, idempotent, no credentials
./setup.sh --check               # report only, changes nothing
./commands.sh doctor             # local state; no network, secrets masked
./commands.sh bootstrap-data     # starter price history
./commands.sh build-ui           # Angular -> stock-app/static
./commands.sh server --no-reload # FastAPI on 127.0.0.1:8000
./setup-brokerages.sh status     # optional integrations, masked
```

Never invoke a global `ng`; the Angular CLI is a devDependency reached through
npm scripts. Never start a dev server with a bare shell command when a
`commands.sh` subcommand exists.

## Verification by change type

Run the targeted checks, then `git diff --check`.

| Change | Run |
|---|---|
| `models/` | both Python suites — every consumer depends on these contracts |
| `utilities/`, `studies/` | `utilities/.venv/bin/python -m pytest -q utilities/tests` |
| `stock-app/` | `stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests` |
| `stock-app-ui/` | `npm run build` **and** `npm run test:ci`, then load the affected route |
| `setup.sh`, `commands.sh`, `tools/` | `utilities/tests/test_setup_tooling.py` plus a real run |
| docs | `python3 tools/check_docs.py` |
| anything | `python3 tools/scan_secrets.py` |

A UI change is not verified by a green build. Load the route and look at it.

**Never treat the developer checkout as evidence that onboarding works.** It has
data, credentials, virtual environments, and a global `ng` that a new user does
not. Verify onboarding from a fresh clone in a separate directory.

**Check which process owns a port before trusting a response.** A stale server
from an earlier run will answer, and its data will look convincing:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

## Documentation authority

When sources disagree, prefer in this order:

1. **Code and committed config** — always authoritative.
2. **Active YAML** under `utilities/config/`, `studies/*/config/`,
   `stock-app/config/` — the live behavioural parameters.
3. **Study specs** (`studies/*/**_spec.md`) — frozen methodology. Read-only; see
   below.
4. **Module READMEs** and `docs/` — current behaviour. Fix them when you change
   behaviour.
5. **`Requirements.md`** — outstanding, deferred, and closed work only. It
   states what must not be reopened without a decision, so read it before
   starting anything in those areas. It no longer records finished work; for
   how something behaves, read the code and `docs/`.
6. **`stock-app-ui/docs/UX_GUIDANCE.md`** — required reading before UI work.

## Hard rules

### Secrets and personal data

- Never write a credential, account number, or real position into a file, test,
  fixture, log, commit message, screenshot, or issue.
- Never print a secret. `tools/doctor.py` and `tools/brokerages.py` mask values
  and have tests asserting it; keep new reporting paths equally safe.
- Provider exceptions can embed tokens. Surface the exception *type* to the user
  and leave the detail in server logs.
- Secrets are prompted with `getpass`, never taken as command-line arguments.
- Run `python3 tools/scan_secrets.py` before committing. `--history` scans every
  reachable object and must stay clean.

### Tests must not touch the network

Fetchers are injected everywhere for this reason. Use a fake. Several suites
assert that no socket is opened; do not weaken those. Live-provider checks are
manual and clearly labelled.

### Compatibility

- Do not change an existing API path, response shape, or field name without an
  explicit request. The Angular client and the generated data are the contract.
- Do not introduce a second OHLCV format or bypass price validation. Reuse the
  scraper's fetch, validation, and atomic-write paths.
- Optional integrations must never block startup or navigation. A missing
  credential is a capability state, not an error.

### Research integrity

Frozen studies are frozen. Do not rerun a spent holdout, retune a parameter to
improve a published result, edit pinned evidence, or soften a `FAILED` verdict.
Materialized artifacts under `data/studies/` are byte-for-byte contracts.
Correcting a path or a command in a study README is fine; changing a number is
not. Methodology changes need the owner's explicit agreement.

### No financial claims

Present scores and screens as evidence, never as predictions or advice. Keep
risk, staleness, and incompleteness visible where a decision is made. Never
render a partial risk figure as though it were complete.

## Before you finish

- Targeted tests green, `git diff --check` clean.
- Docs updated in the same change as the behaviour they describe.
- No secret, no real financial data, no absolute developer path in the diff.
- One focused commit per concern; do not mix a refactor into a fix.
