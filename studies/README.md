# Research Studies

Published research. Each study states a question, the method used to answer it,
the evidence, and a verdict — including when the verdict is *this did not work*.

Studies are visible in the dashboard at `/studies` and served by
`GET /api/studies`.

## Why this exists

A backtest that is retuned until it looks good measures nothing. The discipline
here is deliberate: pre-register the method, run the holdout **once**, and
publish whatever comes out.

That means:

- **A spent holdout cannot be reused.** Rerunning it after seeing the result, or
  adjusting a parameter to improve it, converts an out-of-sample test into an
  in-sample one and destroys its evidential value.
- **Failed studies stay published as failed.** `pre-earnings-momentum` carries a
  `FAILED` verdict and remains in the catalog. Deleting it would misrepresent the
  research record.
- **A revised method needs a new pre-registered study**, not an edit to an old
  one.

`EXPLORATORY` evidence is exactly that. It generates hypotheses; it does not
license a product decision.

## Catalog

Each study publishes two variations. The catalog reports the **default**
variation's outcome.

| Study | Variation | Verdict | Evidence |
|---|---|---|---|
| [Pre-earnings momentum](pre_earnings_momentum/README.md) | `base` (default) | `FAILED` | `CONFIRMATORY` |
| | `spy-cash-sweep` | `NO_VERDICT` | `EXPLORATORY` |
| [Sector relative leadership](sector_rotation/README.md) | `base` (default) | `FAILED` | `CONFIRMATORY` |
| | `full-period` | `NO_VERDICT` | `EXPLORATORY` |

Specs: [`backtest_spec.md`](pre_earnings_momentum/backtest_spec.md),
[`backtest_spec_2.md`](pre_earnings_momentum/backtest_spec_2.md),
[`sector_rotation_study_spec.md`](sector_rotation/sector_rotation_study_spec.md),
[`sector_rotation_study_v2_spec.md`](sector_rotation/sector_rotation_study_v2_spec.md).

Read this carefully: in both studies the **pre-registered, one-shot,
confirmatory** endpoint **failed**. The second variation in each is exploratory
work done afterwards, carries no verdict, and cannot rescue the first.

Neither result lifts a product gate. The Momentum and Sectors views remain
descriptive screens.

## Frozen studies awaiting holdout

[`rsi_supertrend/rsi_supertrend_study_spec.md`](rsi_supertrend/rsi_supertrend_study_spec.md)
is the frozen design and handoff for an exact RSI/SuperTrend Pine implementation
replication approved on 2026-08-23. Pine execution, shared-TA sensitivity
comparison, and synthetic tests are present. There is no published artifact,
result, or verdict. Independent review must accept this stage before the
one-shot holdout is run.

## How a study is published

Studies are **materialized ahead of time**, not computed on request. The API
never runs research code.

```
studies/<name>/definition.json     what to publish, and which evidence proves it
        │
        ▼   studies/catalog.py     verify, then materialize
data/studies/<id>/study.json       committed, byte-for-byte contract
data/studies/catalog.json          committed
        │
        ▼   stock-app/app/studies_read.py    validate on read, fail closed
GET /api/studies
```

```bash
./commands.sh studies build      # verify evidence and republish
./commands.sh studies validate   # validate what is already published
```

### Verification before publication

`build` refuses to publish unless every check passes:

- the summary embedded in the metadata matches the summary file;
- the trades CSV matches the SHA-256 recorded in its metadata;
- the metadata's git commit matches the commit pinned in the definition;
- the result is the **holdout** split, not a development run;
- for study runs, every output file matches the frozen manifest hash.

Any mismatch aborts the build. This is what makes "frozen" mean something: the
published numbers cannot silently drift from the evidence that produced them.

### What is committed, and what is not

| | Committed | Why |
|---|---|---|
| `data/studies/catalog.json`, `data/studies/<id>/study.json` | **Yes** | Aggregate statistics only. A fresh clone gets working Research Studies with no downloads, regardless of where `SFP_DATA_DIR` points. |
| Pinned evidence (backtest summaries, trade CSVs, study run directories) | **No** | Generated data, and its metadata embeds absolute developer paths. |

So `studies build` only works in a checkout where the studies were actually run.
This is why the test suite is layered: published artifacts are validated
everywhere, the verification rules are covered with synthetic evidence, and the
byte-for-byte reproduction skips when the evidence is absent.

## Layout

```
studies/
├── catalog.py                  materialization and verification
├── pre_earnings_momentum/
│   ├── definition.json         what to publish
│   ├── backtest_spec*.md       frozen methodology
│   ├── config/                 frozen parameters
│   ├── scan.py                 the live scan (needs FINNHUB_API_KEY)
│   ├── backtest.py             walk-forward backtest
│   └── event_backtest.py       event-study backtest
├── sector_rotation/
    ├── definition.json
    ├── sector_rotation_study*_spec.md
    ├── config/
    ├── study_v1.py             frozen legacy-nine study
    └── study_v2.py             frozen full-period exploration
└── rsi_supertrend/
    ├── rsi_supertrend_study_spec.md   frozen protocol (do not edit)
    ├── source.pine                    pasted executable Pine source
    ├── config/study.yaml              frozen parameters and cohorts
    ├── pine.py / emulator.py / comparison.py / study.py
    └── README.md                      operational runner notes
```

The RSI/SuperTrend Pine emulator, Pine/shared-TA indicator providers, and paired
sensitivity outcome artifacts are implemented. The 2022–2025 holdout has not
been authorized and remains blocked in code pending independent review.

Studies share the `utilities/.venv` environment and may import `models/` and
`utilities/`. Nothing in `stock-app/` may import them.

## Rules for changing anything here

Allowed:

- correcting a stale path, command, or link in a README;
- fixing a typo that does not change a claim;
- adding a **new** pre-registered study.

Not allowed without the maintainer's explicit agreement:

- rerunning a spent holdout;
- editing pinned evidence, a spec, or a frozen config;
- changing a published number, verdict, or evidence level;
- deleting a failed study.

`./commands.sh sector-rotation-study` and `sector-rotation-study-v2` accept
`--verify-run PATH` to reproduce a completed run's artifacts. Use that to check
a result; do not start a fresh run of a spent study.

See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#research-studies) and
[`../CONTRIBUTING.md`](../CONTRIBUTING.md#research-integrity).
