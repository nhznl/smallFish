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

`services.tastytrade` supplies lazy, read-only account, market-metric, and
DXLink Greek transport calls. Importing it does not import the SDK, read a
credential, authenticate, or contact a provider.

The Tastytrade pin remains identical in `stock-app/requirements.txt` and
`utilities/requirements.txt`; both runtimes run its fake-SDK tests.
