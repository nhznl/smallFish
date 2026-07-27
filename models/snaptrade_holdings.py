"""Version contract for persisted SnapTrade holdings-ledger artifacts."""

SNAPTRADE_HOLDINGS_SCHEMA_NAME = "smallfish.snaptrade-holdings"
SNAPTRADE_HOLDINGS_SCHEMA_VERSION = 1
SUPPORTED_SNAPTRADE_HOLDINGS_SCHEMA_VERSIONS = frozenset(
    {SNAPTRADE_HOLDINGS_SCHEMA_VERSION}
)
