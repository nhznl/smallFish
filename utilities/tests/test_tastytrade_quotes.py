from __future__ import annotations

from utilities.options import tastytrade_quotes


def test_quote_error_hides_provider_message():
    secret = "test-refresh-token-123"
    account = "account-identifier-987"

    error = tastytrade_quotes._safe_error(
        RuntimeError(f"provider rejected {secret} for {account}")
    )

    assert error == (
        "RuntimeError: Tastytrade quote collection is unavailable; "
        "check the brokerage setup and retry the collection."
    )
    assert secret not in error
    assert account not in error
