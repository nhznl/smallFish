# Screenshots

Feature screenshots used by the root README and the docs.

**Every image here is captured from a checkout containing only starter market
data and synthetic examples.** No real position, account name or number, cost
basis, transaction, trade history, token, or local filesystem path appears in
any of them.

The three `*-connected.png` frames show the brokerage integrations populated.
Their ledger data is **generated, not imported**: the trading account is named
"Demo Trading", the retirement account is named "Demo Retirement Account", every
note reads "Synthetic demonstration …", and the underlyings are ETFs from the
starter universe. No brokerage was contacted and no real credential was used —
the connected state is reproduced by writing the ledger CSVs directly. That is a
hard rule, not a preference — see
[../../CONTRIBUTING.md](../../CONTRIBUTING.md).

## Inventory

Captured 2026-07-30. Data mode: `bootstrap-data` starter universe (97 ETFs plus
AAPL and MSFT, 2025–2026), no API key, no brokerage connected for the
unconfigured frames; connected frames use the synthetic CSVs described above.
Light theme, the application's only theme.

| File | Route | Viewport | Shows |
|---|---|---|---|
| `momentum-scanner.png` | `/momentum` | 1440×900 | Setup-ranked candidates from starter data. The README's lead image. |
| `sectors.png` | `/sectors` | 1440×900 | 11-sector leadership against SPY, with its not-a-fund-flow caveat. |
| `research-studies.png` | `/studies` | 1440×900 | The study catalog, including the `FAILED` verdict. |
| `wheel.png` | `/wheel` | 1440×900 | Wheel candidates from the local price cache. No credential needed. |
| `wheel-explainer.png` | `/wheelExplainer` | 1440×900 | Wheel mechanics and field definitions. |
| `portfolios.png` | `/portfolios` | 1440×900 | The five portfolios bootstrap seeds, compared equal-weighted against SPY. Exactly what a new user sees after `bootstrap-data`. |
| `portfolio-detail-defensive-broad.png` | `/portfolios` → detail drawer | 1440×1000 | The "Defensive - Broad" detail drawer, showing a portfolio holding more than one symbol: per-member price, weekly move, 52-week range, and YTD/inception returns. |
| `stock-detail-aapl.png` | `/stockDetail/AAPL` | 1440×900 | Per-symbol analysis for a well-known public company. |
| `options-ledger-unconfigured.png` | `/options` | 1440×1100 | Trading Ledger Holdings tab with nothing imported yet — the normal optional empty state. |
| `retirement-unconfigured.png` | `/retirement` | 1440×1100 | Retirement Ledger Holdings tab in the same empty state. |
| `options-ledger-connected.png` | `/options` → Options | 1440×1400 | Trading Symbol Ledger with three synthetic Active underlyings (XLE, XLF, XLK), Demo Trading account, indicative period P/L. |
| `options-basis-connected.png` | `/options` → Option-Adjusted Basis | 1440×1400 | Trading Option-Adjusted Basis with the same three synthetic underlyings: share economics, option P/L, and live option-adjusted basis per share. |
| `retirement-ledger-connected.png` | `/retirement` | 1440×1300 | Retirement Holdings with four synthetic ETF lots, Demo Retirement Account, editable classifications. |
| `retirement-options-connected.png` | `/retirement` → Options | 1440×1400 | Retirement Symbol Ledger with synthetic covered-call underlyings (VTI, XLV). |
| `momentum-scanner-mobile.png` | `/momentum` | 420×900 | The most table-dense screen at a narrow width. |

Both Wheel screens are included because they communicate different things: the
scan output, and the methodology behind it.

Trading and Retirement share three tabs — Holdings, Options (Symbol Ledger), and
Option-Adjusted Basis. The connected set shows all three on the Trading ledger;
retirement connected frames show Holdings and the Symbol Ledger.
Option-Adjusted Basis is documented in
[`../../stock-app-ui/docs/UX_GUIDANCE.md`](../../stock-app-ui/docs/UX_GUIDANCE.md).

## Capture procedure

1. Start from a **fresh clone** with no credentials:

   ```bash
   ./setup.sh
   ./commands.sh bootstrap-data
   ./commands.sh wheel
   ./commands.sh sector-rotation
   ```

2. Confirm nothing is configured — every credential must be blank:

   ```bash
   ./setup-brokerages.sh status     # both providers NOT_CONFIGURED
   grep -E '^(FINNHUB|TT_|SNAPTRADE)' app.env
   ```

3. Nothing extra to set up for the portfolio shots — `bootstrap-data` seeds the
   five portfolios on a first run. Confirm with `curl -s localhost:8000/portfolios`.

4. Build and serve, then capture each route with headless Chrome at
   `--force-device-scale-factor=2`, `--hide-scrollbars`, and a settled network idle.
   Headless capture is what keeps browser chrome, bookmarks, profile names, and
   local URLs out of the frame.

5. For connected brokerage frames, write synthetic ledger CSVs under a dedicated
   `SFP_DATA_DIR` (never the maintainer's real ledgers). Use invented account
   names (`Demo Trading`, `Demo Retirement Account`), starter-universe ETFs, and
   notes that say the rows are synthetic. Serve that data root on a separate port
   so the live checkout is untouched. Click the Options or Option-Adjusted Basis
   tab (and Holdings where needed) before capturing — those tabs are not URL routes
   of their own.

6. `portfolio-detail-defensive-broad.png` needs a click, because the detail view
   is a drawer with no URL of its own. Run Chrome with
   `--remote-debugging-port`, then drive it over the DevTools Protocol: navigate,
   click the element whose `aria-label` contains the portfolio name, wait for the
   drawer, and `Page.captureScreenshot`.

7. Downscale to 1800px wide so the set stays visually consistent.

## Refreshing

Recapture when a screen changes materially — new columns, a restructured layout,
or changed copy. Cosmetic drift is not worth the churn.

When you do, recapture the whole inventory rather than one file, so the set stays
visually consistent, and update the capture date above.

## Rules

- Starter or synthetic data only. Never the maintainer's real account.
- No browser chrome, no address bar, no OS menu bar, no notifications.
- No local filesystem path visible in the frame.
- Filenames are lowercase and descriptive, matching the route they show.
- Every image referenced from a README needs meaningful alt text describing what
  it demonstrates, not just "screenshot".
- Verify against [`../../stock-app-ui/docs/UX_GUIDANCE.md`](../../stock-app-ui/docs/UX_GUIDANCE.md):
  status must never be conveyed by colour alone, and risk, staleness, and
  caveat labels must remain legible.
- Do not revive Trade Groups or portfolio-risk dashboard imagery.

Before committing a new screenshot, look at it at full size and read every
value in it.
