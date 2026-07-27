"""Version contract for persisted option-premium snapshot artifacts."""

PREMIUM_SCHEMA_NAME = "smallfish.option-premium"
PREMIUM_SCHEMA_VERSION = 3

# Version 2 already carries the exact contract identity and volatility fields
# consumed by portfolio risk.  Keep it readable while new chain collections
# move to v3's quote-provider provenance and side-specific timestamps.
SUPPORTED_PREMIUM_SCHEMA_VERSIONS = frozenset({2, PREMIUM_SCHEMA_VERSION})
