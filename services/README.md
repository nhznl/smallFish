# Shared provider services

`services/` owns read-only provider transport: credentials from the process
environment, SDK session/client construction, provider calls, streaming, and
raw SDK payload envelopes.

It is shared by the backend and utilities runtimes when the relevant SDK is
installed. It must not import `stock-app`, `utilities`, `studies`, FastAPI,
project configuration, artifact code, pandas, or numpy.

Consumers own provider-policy decisions, normalization, public response shapes,
and artifact writes. The services never place, modify, or cancel an order.

## Tastytrade

`services.tastytrade` supplies lazy, read-only account/history/position,
market-metric, DXLink Greek and quote transport calls, plus session
verification. Importing it does not import the SDK, read a credential,
authenticate, or contact a provider.

The Tastytrade pin remains identical in `stock-app/requirements.txt` and
`utilities/requirements.txt`; both runtimes run its fake-SDK tests.

## SnapTrade

`services.snaptrade` supplies lazy, read-only setup and transport calls for
registration, connection portal creation, linked accounts, positions, and
defensively offset/limit-paginated activities. It is installed only in the
backend environment; the backend retains credential persistence, CLI output,
normalization, and artifact writes.

## Consumers

| Consumer | Owns after transport returns raw payloads |
|---|---|
| `stock-app/app/options_activity.py`, `retirement_options.py` | Account selection, option-event and market-data normalization, API sync policy, ledger writes |
| `stock-app/app/snaptrade_service.py` | Credential persistence, CLI output, holdings/activity normalization, ledger writes |
| `utilities/options/tastytrade_quotes.py` | OCC/dxFeed mapping, quote normalization, coverage metadata, archive policy |
| `tools/brokerages.py` | Standard-library verification orchestration and safe human-facing status |

## Tests

`services/tests/test_tastytrade_io.py` runs under both Python environments.
`services/tests/test_snaptrade_io.py` runs under the backend environment.
Both use injected fake sessions/clients and never contact a provider.
