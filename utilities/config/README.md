# Utility configuration

Behavioral configuration is split by owner. Shared utilities keep focused YAML
files here. Options-wheel configuration lives with its package under
`utilities/options/config/`; strategy-owned configuration lives with the
study package, for example `studies/pre_earnings_momentum/config/`,
so one domain cannot silently reuse or overwrite another's settings.

Paths and credentials do not belong here. They remain in root `app.env` and are
passed to utility processes by `commands.sh`.

`market_calendar_*.yaml` holds primary and optional secondary event-risk sources, importance, strategy
clocks, named risk rules, and ETF exposure channels. Those files do not contain
credentials or price forecasts. Primary source URLs select official
machine-readable surfaces where available; coverage is derived from each
official document and fails closed when the requested horizon is not proved.
Secondary schedule failures do not downgrade primary availability. Released
EIA/FAS values and terms-blocked private feeds remain separately visible as
unconfigured capabilities rather than being inferred from schedule coverage.
Cluster rules compare exact event times with the inclusive configured interval
from strategy entry through hard exit; being on the same session is not enough.
