# Market regime stock study

Published, leakage-resistant study testing whether a transparent SPY/VIX
regime framework adds risk information beyond buy-and-hold and a lagged 200-day
moving-average rule.

Read [`market_regime_study_spec.md`](market_regime_study_spec.md) before running
it. The protocol is published with a failed verdict. The 2021–2025 holdout has
been spent and every runner is intentionally unable to calculate it again.

## Run the development and validation baseline

```bash
./commands.sh market-regime-study --fetch-vix --through validation
```

The explicit network flag downloads Cboe's official VIX daily-history CSV. A
subsequent offline run can reuse the retained source snapshot:

```bash
./commands.sh market-regime-study --through validation
```

Tests inject source bytes and never contact Cboe or Yahoo.

Generated files live under `SFP_DATA_DIR/market_regime/validation/`:

- causal daily dataset with split and regime labels;
- data-quality and walk-forward reports;
- development and validation forward statistics;
- persistence and transition matrices;
- compounded equity curves and performance comparisons at 0/1/5/10 bps;
- an SVG SPY/SMA/VIX/RV timeline with regime shading;
- per-CSV provenance manifests and an experiment hash catalog.

These generated results are research artifacts, not application state and not
financial advice. `VALIDATION` may be used for model development; it is not a
confirmatory result. Do not add a UI consumer until a separate versioned JSON
contract is designed and the research gate is resolved.

## Run the full pre-holdout stock comparison

```bash
./commands.sh market-regime-compare --fetch-sources
```

This produces annual 2005–2020 expanding walk-forward results for the fixed
rule model, volatility targeting, K-means, Gaussian mixtures, and causally
filtered Gaussian HMMs with 2/3/4 states. It also runs ten-year rolling-window
and rule-threshold sensitivities. After primary selection it reports, but does
not promote, two/three-session confirmation and five/ten-session minimum-state
duration sensitivities. Uninvested exposure earns a one-session-lagged
three-month Treasury-bill proxy. The command cannot access 2021–2025.

Outputs live under `SFP_DATA_DIR/market_regime/model_comparison/`. The selected
model is a pre-holdout research candidate, not a final verdict. Post-selection
stability variants are explicitly marked holdout-ineligible unless the protocol
is amended and frozen before the holdout is opened.

## Published holdout

The original expanding `kmeans_2` model without a stability filter was run once
from clean commit `6d6b5fd`. At 5 bps it produced 10.95% CAGR, -19.54% maximum
drawdown, and 0.56 Calmar, versus 12.24%, -17.85%, and 0.69 for SMA200. It
failed the frozen criteria. The immutable summary is
[`evidence/holdout_result.json`](evidence/holdout_result.json). Do not rerun the
holdout or retune this study.
