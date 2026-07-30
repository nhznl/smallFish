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
| `NEEDS_REGISTRATION` | Commercial SnapTrade keys without a registered user. | `setup snaptrade` |
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
`services.snaptrade` owns SnapTrade client construction, registration,
connection-portal, account, position, and paginated-activity calls.

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

Adds the options ledger, DXLink quotes, exact-contract Greeks, and market-metric
beta.

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

Adds the retirement holdings ledger and retirement option positions.

**Fidelity connects through SnapTrade.** You authenticate with Fidelity on
SnapTrade's own portal. smallFish receives only read-only holdings and
transaction data, and never sees your Fidelity credentials.

### Personal vs commercial keys

SnapTrade issues two kinds of API keys and they need different steps. The script
detects which you have from the client-ID prefix.

| | Personal (`PERS-` prefix) | Commercial |
|---|---|---|
| Users | Single-user: you | Many |
| Registration | None | A SnapTrade user must be registered first |
| Linking a brokerage | On the SnapTrade dashboard | Through the connection portal |
| `SNAPTRADE_USER_ID` / `_USER_SECRET` | Leave **empty** | Required |

### Setup

```bash
./setup-brokerages.sh setup snaptrade
```

Enter the client ID and consumer key from <https://dashboard.snaptrade.com>.
Then:

**Personal keys** — link your brokerage on the SnapTrade dashboard. Nothing
further goes in `app.env`.

**Commercial keys** — register a user:

```bash
stock-app/.venv/bin/python -m app.snaptrade_service register
```

The command saves the generated `userId` and `userSecret` directly to `app.env`
using an atomic mode-0600 write; it never displays either value. Then rerun
`setup snaptrade` and follow the connection-portal link.

That command path is stable. Registration, credential persistence, and account
listing are implemented in `stock-app/app/snaptrade_setup.py`;
`stock-app/app/snaptrade_service.py` remains a thin compatibility facade so the
documented command keeps working.

### Verify

```bash
./setup-brokerages.sh verify
```

Reports the number of linked accounts. Never an account number or name. Zero
linked accounts means the credentials work but no brokerage is connected yet.

---

## Combined risk inputs

Retirement option risk is reported as its own capability because **SnapTrade
alone cannot supply exact-contract Greeks or the beta inputs.**

| Configured | Risk figures |
|---|---|
| Neither | Unavailable |
| SnapTrade only | Fall back to realized volatility and a locally computed beta, and are **labelled** as fallbacks. The UI shows a warning banner. Not a complete risk picture. |
| Both | Exact-contract IV, Greeks, and market-metric beta |

Partial risk inputs are never presented as complete totals.

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
| `$SFP_DATA_DIR/ledger_trading/options_position_marks.csv` | Current marks |
| `$SFP_DATA_DIR/ledger_trading/options_greeks.csv` | Timestamped IV and Greeks |
| `$SFP_DATA_DIR/ledger_trading/options_betas.csv` | Timestamped market-metric beta |
| `$SFP_DATA_DIR/ledger_retirement/snaptrade_holdings.csv` | Normalized holdings |

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
