# Sector relative leadership

**Default variation: verdict `FAILED`, evidence `CONFIRMATORY`.**
**Second variation (`full-period`): verdict `NO_VERDICT`, evidence `EXPLORATORY`.**

Two frozen studies asking whether one sector ETF outperforming another predicts
what happens next. The pre-registered confirmatory endpoint **failed**; the
later full-period work is exploratory and carries no verdict. Neither lifts a
product gate, and the Sectors view remains a descriptive screen.

Published at `/studies/sector-relative-leadership`.

## The distinction that matters

| | What it is |
|---|---|
| **The Sectors view** (`./commands.sh sector-rotation`) | A *descriptive* snapshot: which of the eleven Select Sector SPDRs are leading SPY right now, from price and volume. Permanently descriptive. It is a relative-strength proxy, **not** a measured fund flow, and it makes no claim about the future. |
| **These studies** (`study_v1.py`, `study_v2.py`) | *Research* into whether that leadership signal has any forward predictive value. Frozen, and separate from the view. |

Nothing in the studies changes what the view shows or how it is labelled.

## Study 1 — legacy-nine forward leadership

Spec: [`sector_rotation_study_spec.md`](sector_rotation_study_spec.md)
Config: [`config/sector_rotation_study.yaml`](config/sector_rotation_study.yaml)
Runner: `./commands.sh sector-rotation-study`

Pre-registered on the nine sector ETFs with history long enough for the intended
window (XLRE and XLC launched too late). It asked whether a confirmed leadership
event predicts the source–target spread over a forward horizon.

**The one-shot primary result failed.** The holdout is spent. It cannot be rerun,
and the failure stands.

## Study 2 — full-period exploration

Spec: [`sector_rotation_study_v2_spec.md`](sector_rotation_study_v2_spec.md)
Config: [`config/sector_rotation_study_v2.yaml`](config/sector_rotation_study_v2.yaml)
Runner: `./commands.sh sector-rotation-study-v2`

A 108-decision pooled estimate over the full available period, run **after** the
first study's outcome was known.

This is post-outcome exploratory evidence. It has **no pass/fail verdict** by
construction: it was not pre-registered, the decisions overlap, and the analyst
had already seen the first result. It generates hypotheses. It cannot confirm
one, and it does not lift the product gate.

## Reproducing a run

Both runners accept `--verify-run PATH` to reproduce a completed run's
analytical artifacts byte-for-byte from its frozen inputs:

```bash
./commands.sh sector-rotation-study --verify-run data/sector_rotation_study/legacy-nine-v1/runs/<run-id>
```

This is the supported way to check a result. **Do not start a fresh run of
either study** — both holdouts are spent, and a new run would produce a number
with no evidential standing.

Each run directory carries a `manifest.json` with a SHA-256 for every output.
Study materialization verifies those hashes before publishing, so a modified
artifact fails the build rather than quietly changing the published record.

## Sample-size limitation

The binding constraint is disjoint windows, not calendar length. The SPDR
sector ETFs give roughly 26 disjoint windows against far more candidate pairs,
which is why the evidence stays exploratory. Extending the history would need
either pre-1998 sector proxies or accepting the Sectors view as descriptive-only
— the position it holds today.

## Files

| Path | Contents |
|---|---|
| `definition.json` | What is published, and which run proves it |
| `study_v1.py`, `study_v2.py` | Frozen implementations |
| `config/*.yaml` | Frozen parameters. Do not edit. |
| `*_spec.md` | Frozen methodology |

Tests: `utilities/tests/test_sector_rotation_study.py` and
`test_sector_rotation_study_v2.py`.

Changing a parameter, a spec, or a published number here needs the maintainer's
explicit agreement. See [`../README.md`](../README.md).
