# Implementation prompt: shared analysis refactor

Implement the approved shared-analysis refactor in the smallFish repository at
`/Users/zenilhussain/projects/smallFish`.

Read and follow, in this order:

1. `AGENTS.md` and any more specific agent instructions;
2. `PROJECT_STATUS.md`;
3. `docs/SHARED_ANALYSIS_REFACTOR_PLAN.md`;
4. `docs/ARCHITECTURE.md`, `models/README.md`, `utilities/README.md`,
   `studies/README.md`, and `stock-app/README.md`; and
5. the implementations, consumers, and tests for
   `stock-app/app/stock_model.py`, `stock-app/app/trend_engine.py`, and
   `stock-app/app/ema_crossover.py`.

Complete both implementation stages before handing the work back:

- Stage 1 must move the single active implementation into a new top-level
  `analysis/` package and temporarily preserve old imports with explicit,
  import-only shims. Commit this stage separately.
- Stage 2 must migrate every production/test consumer to `analysis.*`, delete
  all three shims, update architecture documentation, and commit this stage
  separately.

The final tree must have no duplicate active implementations, no compatibility
shims, no legacy imports, and no `stock-app -> utilities` dependency. The shims
are reviewable migration scaffolding in the first commit only; they must not be
present in the delivered checkout.

This is strictly a behavior-preserving structural refactor. Do not change API
paths or JSON, numeric/indicator conventions, Momentum Scanner `momentum-v3`
classification or scoring, EMA crossover rules, cache behavior, or date
serialization. Do not edit or redirect the frozen
`studies/pre_earnings_momentum/momentum_v3_replay.py`, frozen study specs,
evidence, configs, results, or materialized artifacts. Do not rerun a spent
holdout. Record unrelated or suspicious behavior as follow-up observations
instead of fixing it.

Preserve unrelated worktree changes. Use `apply_patch` for edits. Keep tests
offline. Add an enforced import boundary for `analysis/` and adequate
characterization/parity coverage before deleting the old module paths.

Run the targeted checks during each stage and these complete checks on the
final tree:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
stock-app/.venv/bin/python -c "import analysis"
utilities/.venv/bin/python -c "import analysis"
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
test ! -e stock-app/app/trend_engine.py
test ! -e stock-app/app/ema_crossover.py
test ! -e stock-app/app/stock_model.py
! rg -n 'app\.(trend_engine|ema_crossover|stock_model)|from \.(trend_engine|ema_crossover|stock_model)' \
    stock-app utilities studies --glob '*.py'
```

Do not stop after writing code. Return a concise review packet with these exact
sections:

1. **Outcome** — what changed and whether both stages are complete.
2. **Commits** — Stage 1 and Stage 2 hashes and subjects.
3. **Movement map** — every old module/public symbol and its final import path.
4. **Behavior evidence** — characterization/parity coverage and preserved
   invariants.
5. **Verification** — each command, exit status, test count, skips, and concise
   result.
6. **No-shim proof** — file-absence checks, legacy-import search, and duplicate
   implementation search.
7. **Diff/worktree** — `git diff --stat`, `git status --short`, and unrelated
   changes deliberately left untouched.
8. **Review notes** — highest-risk files/lines, follow-up observations, and any
   remaining uncertainty. If none, say so explicitly.

Include clickable absolute file paths and tight line references where helpful.
Do not paste the entire diff or claim success when a required check was skipped
or failed.
