# Brokerage integrations

Both integrations are **optional and read-only**. smallFish's core — stocks,
ETFs, portfolios, sectors, momentum, wheel screening, Research Studies — never
needs one. You can stop at any point and still have a working application.

smallFish never places, modifies, or cancels an order, and never asks for a
brokerage password.

One entry point for everything below:

```bash
./setup-brokerages.sh status            # local, masked, no network
./setup-brokerages.sh setup tastytrade
./setup-brokerages.sh setup snaptrade
./setup-brokerages.sh setup all
./setup-brokerages.sh verify            # read-only provider calls
```

## States

`status` reports one state per provider, so you always know the exact next step.

| State | Meaning | Next |
|---|---|---|
| `NOT_CONFIGURED` | No credentials. The feature is off. | `setup` |
| `INCOMPLETE` | Some but not all required settings. | `setup` |
| `CREDENTIALS_PRESENT` | Complete, not yet checked against the provider. | `verify` |
| `NEEDS_REGISTRATION` | Commercial SnapTrade keys without externally created user credentials. | Create/link the user outside smallFish, then `setup snaptrade` |
| `NEEDS_CONNECTION` | Configured, but no brokerage linked yet. | Link at the provider, then `verify` |
| `READY` | Verified against the provider. | Sync in the UI |
| `ERROR` | Misconfigured or rejected. | See the printed remediation |

---

## Provider I/O ownership

The command and API entry points load `app.env`; shared `services/` packages
then read credentials only from the process environment. `services.tastytrade`
owns Tastytrade session construction, account/history/position reads, DXLink
streaming, and raw payloads. `services.options_market` is the provider-neutral
read API for exact-contract quotes, Greeks/IV, and underlying beta; it routes
to Tastytrade today and owns OCC-to-dxFeed conversion in its adapter.
`services.snaptrade` owns SnapTrade client construction and read-only account,
position, and paginated-activity calls.

Tastytrade therefore has two independent roles: brokerage-account source for
the options ledger, and the current options market-data provider behind
`services.options_market`. Brokerage importers and quote enrichment call the
neutral API for quotes/Greeks/beta; they do not call Tastytrade market-data
transport directly.

The backend and utilities retain provider-specific policy: normalization,
Symbol Ledger selection, quote eligibility, artifact writes, CLI presentation,
and public API responses. Services never place, modify, or cancel an order, and
they never persist credentials or brokerage data.

---

## Tastytrade

Adds the Trading ledger (Holdings, Symbol Ledger, Combined Adjusted Basis),
DXLink quotes, exact-contract Greeks, and market-metric beta.

### What smallFish reads

- Options activity — immutable transaction history used by Symbol Ledger P/L
  and archive verification.
- Current open option positions and their marks.
- Live IV and Greeks for your exact open contracts.
- Market-metric beta, the governed beta input for beta-delta risk.

It writes those into git-ignored CSVs under `$SFP_DATA_DIR/ledger_trading/`.

### Setup

1. Open the [OAuth applications portal](https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications)
   (my.tastytrade.com → Manage → API Access → OAuth Applications).
2. **Create an application.** Tick every scope you intend to use and add
   `http://localhost:8000` as the callback URL.
3. **Save the client secret now** — it is displayed only once. This is
   `TT_CLIENT_SECRET`.
4. On that application, choose **Manage → Create Grant** to generate a refresh
   token. This is `TT_REFRESH_TOKEN`.

   Refresh tokens **do not expire**, so this is a one-time setup. If
   authentication fails later it is a revoked grant or the wrong environment,
   not expiry.

   Sandbox uses a [separate portal](https://developer.tastytrade.com/sandbox/)
   with its own credentials. Sandbox credentials will not authenticate against
   `TT_ENV=live`, and vice versa — a mismatch looks like a bad credential.

   Reference: [tastytrade SDK session documentation](https://tastyworks-api.readthedocs.io/en/latest/sessions.html).

5. Run:

   ```bash
   ./setup-brokerages.sh setup tastytrade
   ```

6. Paste the client secret and refresh token when prompted. Input is hidden and
   is never taken as a command-line argument, so nothing reaches your shell
   history or a process listing.
7. Choose the environment. **Sandbox is the default and the safe choice**;
   `live` reads your real account and requires explicit confirmation.

smallFish uses only `TT_CLIENT_SECRET`, `TT_REFRESH_TOKEN`, and `TT_ENV`. There
is no client-ID setting — neither the code nor the `tastytrade` SDK uses one.

### Verify

```bash
./setup-brokerages.sh verify
```

Opens a read-only session. Prints the outcome only; never a token or an account
number.

---

## SnapTrade (Fidelity and other retirement brokerages)

Adds the Retirement ledger and retirement option activity through SnapTrade.

**Fidelity connects through SnapTrade.** You authenticate with Fidelity on
SnapTrade's own portal. smallFish receives only read-only holdings and
transaction data, and never sees your Fidelity credentials.

### Personal vs commercial keys

SnapTrade issues two kinds of API keys and they need different steps. The script
detects which you have from the client-ID prefix.

| | Personal (`PERS-` prefix) | Commercial |
|---|---|---|
| Users | Single-user: you | Many |
| Registration | None | Create the SnapTrade user outside smallFish |
| Linking a brokerage | On the SnapTrade dashboard | Complete outside smallFish |
| `SNAPTRADE_USER_ID` / `_USER_SECRET` | Leave **empty** | Required |

### Setup

```bash
./setup-brokerages.sh setup snaptrade
```

Enter the client ID and consumer key from <https://dashboard.snaptrade.com>.
Then:

**Personal keys** — link your brokerage on the SnapTrade dashboard. Nothing
further goes in `app.env`.

**Commercial keys** — create the user and link its brokerage outside smallFish.
Then rerun guided setup and enter the existing user credentials:

```bash
./setup-brokerages.sh setup snaptrade
```

The setup command saves the supplied user ID and secret to `app.env` using an
atomic mode-0600 write and never echoes the secret. smallFish does not register
SnapTrade users or create connection-portal URLs.

### Verify

```bash
./setup-brokerages.sh verify
```

Reports the number of linked accounts. Never an account number or name. Zero
linked accounts means the credentials work but no brokerage is connected yet.

---

## Ledger views

Trading (`/options`) and Retirement (`/retirement`) share one four-tab shell:

| Tab | What it shows |
|---|---|
| **Holdings** | Open equity and cash-equivalent positions with an Edit dialog for category, industry, note, optional display name, and any missing cost basis. Options are excluded. |
| **Options** | The Symbol Ledger: one durable record per underlying, derived Active/Closed lifecycle, option-only positions and P/L, immutable event history, and optional archived-period detail. |
| **Combined Adjusted Basis** | Combined equity and option P/L for each symbol that still holds long shares, plus the basis adjusted by its option history. |
| **Portfolio Analysis** | Account-role profile fit, construction, deployment, concentration, current-holdings replay, hypothetical shocks, option commitments, and a non-persistent stock/ETF What-if preview. |

Some employer-plan holdings arrive without provider cost basis. Those rows show
an em dash for cost and gain/loss values, and affected portfolio totals remain
unavailable rather than silently treating the missing basis as zero. The
Holdings Edit dialog accepts either total cost basis or cost per share/unit for
such a row. This account-scoped value is stored as app-owned metadata and
survives brokerage sync; broker-supplied basis takes precedence if it later
appears. Captured gain/loss comparisons are withheld until a basis is known.

Trade groups and the former portfolio-risk dashboard are retired. Sync
materializes provider artifacts (positions, account capital, activity, marks,
Greeks, beta). Providers return a **current position snapshot**, not a "this
ticker was sold" event. After a successful holdings write, smallFish compares
that snapshot with the previous open long-equity set for the same brokerage: a
universe symbol that was a long equity and is now gone is added to Tracking
under **Sold Stock**, or recategorized there if it was already tracked, with
coverage initiation reset to today (so the initiation price is the latest
cached close) and a note `updated to Sold Stock per sync on DATE` appended.
Options, cash-equivalents, shorts, and partial quantity
reductions are not treated as sales. A tracking failure never fails the
brokerage sync. Portfolio Analysis is a new provider-neutral projection rather
than a restoration of that dashboard. It refuses percentage conclusions when
net liquidating value is unavailable, never invents profile limits, and labels
its historical output as a current-holdings replay rather than realized account
history. Preview recalculation writes no ledger, metadata artifact, or order.
Symbol Ledger and related projections read positions and activity for P/L and
lifecycle; call-coverage accounting selects which held option legs to fetch for
market sync. Greek and beta **values** are retained provider evidence (and feed
IV / `as_of.market` where projected), not Symbol Ledger arithmetic and not a
separate risk UI. The owner confirmed on 2026-07-30 that there are no external
consumers and chose to retain this materialization for now. Measurement:
[`BETA_GREEK_CONSUMER_MEASUREMENT.md`](BETA_GREEK_CONSUMER_MEASUREMENT.md).

The Symbol Ledger's `exposure=options` projection scopes current, archived, and
lifetime P/L to option components. Equity positions may still supply underlying
price, coverage, and reconciliation context internally, but their rows and P/L
are excluded from the Options tab. The unscoped API retains the combined-ledger
view for compatibility.

## Materialized market inputs

Exact-contract Greeks/IV and market-metric beta are still fetched and written
during sync when Tastytrade is configured. They remain available as materialized
CSV evidence for adapters, IV on `GET …/options`, market timestamps, and any
future in-repository consumer. Call coverage decides which legs to enrich; it does
not consume Greek or beta values. Retirement option enrichment that needs those
inputs still depends on Tastytrade as the market-data provider:

| Configured | Market inputs |
|---|---|
| Neither | Unavailable |
| SnapTrade only | Holdings and option activity import; exact-contract Greeks/IV and market-metric beta are not available from SnapTrade alone |
| Both | Exact-contract IV, Greeks, and market-metric beta can be materialized alongside SnapTrade holdings |

Partial inputs must never be presented as complete market-data enrichment —
holdings alone are not exact-contract Greeks or market-metric beta.

## Security

- Secrets are entered without echo and never passed as arguments.
- `app.env` is rewritten atomically at mode 0600, preserving your comments and
  any settings smallFish does not know about. An existing value is never
  replaced without confirmation, and a non-interactive run declines rather than
  overwriting.
- `status` makes no network call. `verify` makes only documented read-only calls.
- Nothing prints a secret or an account identifier. Provider errors can embed
  tokens, so only the exception *type* is shown; the detail stays in the server
  logs.
- Automated tests use fakes. No test contacts a provider.

## Local files

| Path | Contents |
|---|---|
| `$SFP_DATA_DIR/ledger_trading/options_activity.csv` | Immutable broker transactions |
| `$SFP_DATA_DIR/ledger_trading/positions.csv` | All current equity and option positions |
| `$SFP_DATA_DIR/ledger_trading/options_position_marks.csv` | Current marks |
| `$SFP_DATA_DIR/ledger_trading/options_greeks.csv` | Timestamped IV and Greeks |
| `$SFP_DATA_DIR/ledger_trading/options_betas.csv` | Timestamped market-metric beta |
| `$SFP_DATA_DIR/ledger_retirement/positions.csv` | Normalized holdings (equity, option, cash) |
| `$SFP_DATA_DIR/ledger_retirement/options_activity.csv` | Immutable option transaction events |

All git-ignored. All contain real position data — never attach them to an issue.

## Rotating credentials

Rerun `./setup-brokerages.sh setup <provider>` and confirm the overwrite, then
`verify`. Revoke the old credential at the provider afterwards; smallFish cannot
do that for you.

## Revoking access

Disconnecting stops future syncs. It does **not** delete data already written to
`$SFP_DATA_DIR`; remove those files yourself if you want them gone.

**Tastytrade** — revoke the OAuth client in your Tastytrade API settings, then
clear `TT_CLIENT_SECRET` and `TT_REFRESH_TOKEN` from `app.env`.

**SnapTrade** — remove the brokerage connection on the SnapTrade dashboard,
which revokes smallFish's access at Fidelity too, then clear the
`SNAPTRADE_*` settings.

Confirm with `./setup-brokerages.sh status`; both should read `NOT_CONFIGURED`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `INCOMPLETE` after setup | Only one of the two required settings was entered. Rerun `setup`. |
| `ERROR` naming `TT_ENV` | `TT_ENV` must be exactly `sandbox` or `live`. |
| `verify` reports an authentication error | Expired or revoked refresh token, or keys from a different environment. Rerun `setup`. |
| Verified, but the ledger stays empty | The credentials work and the account is genuinely flat, or you have not synced yet. The UI distinguishes these. |
| SnapTrade verified with 0 accounts | Credentials are fine; no brokerage linked. Link it on the dashboard or through the portal. |
| `NEEDS_REGISTRATION` that will not clear | Commercial keys need `SNAPTRADE_USER_ID` *and* `SNAPTRADE_USER_SECRET`. |

More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
