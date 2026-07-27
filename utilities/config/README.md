# Utility configuration

Behavioral configuration is split by owner. Shared utilities keep focused YAML
files here. Options-wheel configuration lives with its package under
`utilities/options/config/`; strategy-owned configuration lives with the
study package, for example `studies/pre_earnings_momentum/config/`,
so one domain cannot silently reuse or overwrite another's settings.

Paths and credentials do not belong here. They remain in root `app.env` and are
passed to utility processes by `commands.sh`.
