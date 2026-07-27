# Contributing to smallFish

Thanks for taking a look. smallFish is a small, single-maintainer project, so a
short conversation before a large change saves everyone time.

## Open an issue first for

- anything touching **research methodology**, a study's parameters, or a
  published verdict;
- changes to an **API path, response shape, or field name**;
- changes to the **price cache format** or another on-disk contract;
- a new dependency, or a change to either Python environment;
- a large refactor.

Small, self-contained fixes — a bug, a doc correction, a test, a clearer error
message — can go straight to a pull request.

## Setup

```bash
git clone https://github.com/nhznl/smallFish.git
cd smallFish
./setup.sh
./commands.sh doctor
./commands.sh bootstrap-data
```

No API key or brokerage account is needed to develop any part of smallFish. If
you find yourself needing one, that is a bug — please report it.

Details in [docs/SETUP.md](docs/SETUP.md).

## Branches and commits

Branch from `main`, named `<type>/<short-description>`: `fix/stale-price-label`,
`feat/portfolio-export`, `docs/setup-wsl`.

Commit messages: a `type: summary` subject in the imperative under ~72
characters, then a body explaining **why**. Types: `feat`, `fix`, `docs`,
`test`, `refactor`, `build`, `ci`, `chore`.

Keep one concern per commit. Do not mix a refactor into a bug fix, and never mix
a methodology change into anything.

## Conventions

Read [`AGENTS.md`](AGENTS.md) — it is written for coding agents but it is the
most concise statement of the repository's rules, and they apply to humans too.

The essentials:

- **Dependency direction is one-way.** `models/` is standard-library only.
  `stock-app/` must never import `utilities/` or `studies/`.
- **Three runtimes**, kept separate: `utilities/.venv`, `stock-app/.venv`, and
  Node. `tools/` is standard-library only so it runs before either venv exists.
- **Match the surrounding code.** Comment density, naming, and idiom vary by
  module; follow the file you are in.
- **Comments explain why**, not what. Existing code is a good guide.
- **Update the docs in the same change** as the behaviour they describe.

For UI work, read [`stock-app-ui/AGENTS.md`](stock-app-ui/AGENTS.md) and
[`stock-app-ui/docs/UX_GUIDANCE.md`](stock-app-ui/docs/UX_GUIDANCE.md) first,
and reuse the shared primitives in `src/app/shared/ui/` and `src/styles.scss`.

## Tests

Run what CI runs:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
cd stock-app-ui && npm run build && npm run test:ci
python3 tools/check_docs.py
python3 tools/scan_secrets.py
```

| You changed | Run at minimum |
|---|---|
| `models/` | both Python suites |
| `utilities/`, `studies/` | utilities suite |
| `stock-app/` | backend suite |
| `stock-app-ui/` | `npm run build` and `npm run test:ci`, and load the route |
| `setup.sh`, `commands.sh`, `tools/` | `utilities/tests/test_setup_tooling.py`, plus a real run |
| any docs | `tools/check_docs.py` |

**No test may contact a provider.** Fetchers are injected everywhere for this
reason — use a fake. Several tests assert that no socket is opened; do not
weaken them. The suites must pass offline.

A UI change is not verified by a green build. Load the affected route and look
at it.

## Never include

- a credential, token, or API key — in code, tests, fixtures, commit messages,
  or screenshots;
- a real account number, position, cost basis, or transaction;
- an absolute path from your machine;
- market data files. Nothing downloaded from a provider may be committed.

`python3 tools/scan_secrets.py` runs in CI on every pull request. Run it
locally first.

If you accidentally commit a secret, say so immediately and **revoke it at the
provider** — removing the file is not enough, because the value stays in Git
history.

## Research integrity

This matters more here than in most projects.

Published studies are **frozen**. Do not rerun a spent holdout, retune a
parameter to improve a published result, edit pinned evidence, or soften a
verdict. A failed study stays published as failed — that is the point.

A revised methodology needs a new pre-registered study, not an edit to an old
one. Materialized artifacts under `data/studies/` are byte-for-byte contracts.

Correcting a stale path or command in a study README is welcome. Changing a
number is not.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#research-studies).

## No financial claims

Present scores and screens as evidence, never as predictions or advice. Keep
risk, staleness, and incompleteness visible where a user makes a decision. Never
render a partial risk figure as though it were complete.

## Pull request checklist

- [ ] Targeted tests pass locally
- [ ] `git diff --check` is clean
- [ ] `python3 tools/scan_secrets.py` passes
- [ ] `python3 tools/check_docs.py` passes if docs changed
- [ ] Docs updated alongside the behaviour
- [ ] No credential, real financial data, or absolute local path in the diff
- [ ] No API, data-format, or methodology change without a prior issue
- [ ] UI changes verified in a browser, not just built

## Licensing

Contributions are submitted under the repository's [MIT License](LICENSE). There
is no CLA and no DCO sign-off requirement.

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
