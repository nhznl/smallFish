# Shared provider services

`services/` owns read-only provider transport: credentials from the process
environment, SDK session/client construction, provider calls, streaming, and
raw SDK payload envelopes.

It is shared by the backend and utilities runtimes when the relevant SDK is
installed. It must not import `stock-app`, `utilities`, `studies`, FastAPI,
project configuration, artifact code, pandas, or numpy.

Consumers own provider-policy decisions, normalization, public response shapes,
and artifact writes. The services never place, modify, or cancel an order.

## Options market data

`services.options_market` is the provider-neutral read API for exact-contract
quotes, exact-contract Greeks/IV, and underlying market metrics (initially
beta). It owns standard-library request/observation contracts and routes to a
supported provider adapter. Tastytrade is the only provider today; unknown
provider ids fail with a safe configuration error.

OCC-to-dxFeed conversion lives only in the Tastytrade adapter under
`services.options_market.providers`. Application and utilities consumers must
not call `services.tastytrade` quote, Greek, or market-metric transport
directly.

Importing `services.options_market` does not import a provider SDK, read a
credential, authenticate, or contact a provider. Both Python runtimes run its
fake-provider contract tests.

## Tastytrade

`services.tastytrade` supplies lazy, read-only account/history/position,
market-metric, DXLink Greek and quote transport calls, plus session
verification. It plays two independent roles:

1. **Brokerage account transport** — used by `options_activity.py` for account
   history and marked positions.
2. **Options market-data transport** — reached only through
   `services.options_market` for quotes, Greeks/IV, and underlying beta.

Importing it does not import the SDK, read a credential, authenticate, or
contact a provider. The Tastytrade pin remains identical in
`stock-app/requirements.txt` and `utilities/requirements.txt`; both runtimes
run its fake-SDK tests.

## SnapTrade

`services.snaptrade` supplies lazy, read-only setup and transport calls for
registration, connection portal creation, linked accounts, positions, and
defensively offset/limit-paginated activities. It is installed only in the
backend environment; the backend retains credential persistence, CLI output,
normalization, and artifact writes, split between `snaptrade_setup.py` and the
brokerage importers.

## Consumers

| Consumer | Owns after transport returns |
|---|---|
| `stock-app/app/options_activity.py` | Account selection, option-event normalization, API sync policy, ledger writes; market-data portion uses `services.options_market` |
| `stock-app/app/brokerages/importers/held_option_market_data.py` | Held-option beta/Greek materialization via `services.options_market` |
| `stock-app/app/brokerages/importers/snaptrade.py` | SnapTrade holdings/activity normalization and ledger writes |
| `stock-app/app/snaptrade_setup.py` | SnapTrade registration, credential persistence, account listing, setup CLI output |
| `stock-app/app/snaptrade_service.py` | Compatibility facade only: re-exports, the legacy all-resource sync orchestrator, and CLI delegation |
| `utilities/options/market_quotes.py` | Quote coverage metadata, freshness, and premium-archive enrichment from neutral observations |
| `tools/brokerages.py` | Standard-library verification orchestration and safe human-facing status |

## Tests

`services/tests/test_tastytrade_io.py` and
`services/tests/test_options_market.py` run under both Python environments.
`services/tests/test_snaptrade_io.py` runs under the backend environment.
All use injected fake sessions/clients and never contact a provider.
