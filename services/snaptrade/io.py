"""Raw, read-only SnapTrade SDK transport operations."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

ACTIVITIES_PAGE_SIZE = 1000


@dataclass(frozen=True, repr=False)
class SnapTradeCredentials:
    client_id: str
    consumer_key: str
    user_id: str | None = None
    user_secret: str | None = None

    def __repr__(self) -> str:
        return (
            "SnapTradeCredentials(client_id='[REDACTED]', "
            "consumer_key='[REDACTED]', user_id='[REDACTED]', "
            "user_secret='[REDACTED]')"
        )


class SnapTradeConfigurationError(ValueError):
    """Invalid environment-backed SnapTrade configuration."""


class SnapTradeServiceError(RuntimeError):
    """Safe provider-boundary error; original detail remains chained."""

    def __init__(self, operation: str, exc: Exception):
        self.provider = "snaptrade"
        self.operation = operation
        self.error_type = type(exc).__name__
        super().__init__(f"SnapTrade {operation} failed ({self.error_type})")


ClientFactory = Callable[[SnapTradeCredentials], Any]


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def load_credentials(environ: Mapping[str, str] | None = None) -> SnapTradeCredentials:
    values = os.environ if environ is None else environ
    get = lambda key: values.get(key, "").strip()
    client_id, consumer_key = get("SNAPTRADE_CLIENT_ID"), get("SNAPTRADE_CONSUMER_KEY")
    if not client_id or not consumer_key:
        raise SnapTradeConfigurationError(
            "SnapTrade app credentials are not configured; set "
            "SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY in app.env"
        )
    return SnapTradeCredentials(
        client_id, consumer_key, get("SNAPTRADE_USER_ID") or None,
        get("SNAPTRADE_USER_SECRET") or None,
    )


def is_personal_key(credentials: SnapTradeCredentials) -> bool:
    return credentials.client_id.upper().startswith("PERS-")


def user_kwargs(credentials: SnapTradeCredentials) -> dict[str, str]:
    if is_personal_key(credentials):
        return {}
    if not credentials.user_id or not credentials.user_secret:
        raise SnapTradeConfigurationError(
            "SnapTrade user is not registered; run "
            "'python -m app.snaptrade_service register' and save "
            "SNAPTRADE_USER_ID/SNAPTRADE_USER_SECRET to app.env"
        )
    return {"user_id": credentials.user_id, "user_secret": credentials.user_secret}


def _default_client_factory(credentials: SnapTradeCredentials) -> Any:
    from snaptrade_client import SnapTrade
    from snaptrade_client.auth import SnapTradeAuth

    auth_factory = (
        SnapTradeAuth.personal_api_key
        if is_personal_key(credentials)
        else SnapTradeAuth.commercial_api_key
    )
    return SnapTrade(auth=auth_factory(
        consumer_key=credentials.consumer_key, client_id=credentials.client_id
    ))


def _client(credentials: SnapTradeCredentials, client_factory: ClientFactory | None) -> Any:
    try:
        return (client_factory or _default_client_factory)(credentials)
    except Exception as exc:
        raise SnapTradeServiceError("client construction", exc) from exc


def _call(operation: str, callback: Callable[[], Any]) -> Any:
    try:
        return callback()
    except SnapTradeConfigurationError:
        raise
    except SnapTradeServiceError:
        raise
    except Exception as exc:
        raise SnapTradeServiceError(operation, exc) from exc


def register_user(user_id: str | None = None, *, credentials: SnapTradeCredentials | None = None,
                  client_factory: ClientFactory | None = None) -> Any:
    creds = credentials or load_credentials()
    if is_personal_key(creds):
        raise SnapTradeConfigurationError(
            "registration does not apply to personal API keys (PERS- prefix); "
            "link brokerages on the SnapTrade dashboard, then run 'sync'"
        )
    resolved_id = user_id or creds.user_id or f"smallfish-{uuid.uuid4()}"
    client = _client(creds, client_factory)
    return _call("user registration", lambda: client.authentication.register_snap_trade_user(
        user_id=resolved_id
    ).body)


def connection_portal(broker: str | None = None, custom_redirect: str | None = None, *,
                      credentials: SnapTradeCredentials | None = None,
                      client_factory: ClientFactory | None = None) -> Any:
    creds = credentials or load_credentials()
    client = _client(creds, client_factory)
    return _call("connection portal", lambda: client.authentication.login_snap_trade_user(
        **user_kwargs(creds), broker=broker or None, custom_redirect=custom_redirect or None,
    ).body)


def list_accounts(*, credentials: SnapTradeCredentials | None = None,
                  client_factory: ClientFactory | None = None) -> tuple[Any, ...]:
    creds = credentials or load_credentials()
    client = _client(creds, client_factory)
    body = _call("account listing", lambda: client.account_information.list_user_accounts(
        **user_kwargs(creds)
    ).body)
    return tuple(body or ())


def fetch_positions(account_ids: list[str] | None = None, *,
                    credentials: SnapTradeCredentials | None = None,
                    client_factory: ClientFactory | None = None) -> list[tuple[Any, Any]]:
    creds = credentials or load_credentials()
    client = _client(creds, client_factory)
    kwargs = user_kwargs(creds)
    accounts = _call("account listing", lambda: client.account_information.list_user_accounts(
        **kwargs
    ).body) or []
    wanted = set(account_ids) if account_ids else None
    pairs = []
    for account in accounts:
        account_id = _text(_value(account, "id"))
        if wanted is not None and account_id not in wanted:
            continue
        positions = _call("position retrieval", lambda account_id=account_id:
            client.account_information.get_all_account_positions(
                account_id=account_id, **kwargs
            ).body)
        pairs.append((account, positions))
    return pairs


def _activities_page(body: Any) -> list[Any]:
    return list(body) if isinstance(body, list) else list(_value(body, "data") or [])


def fetch_activities(start_date: Any, end_date: Any, account_ids: list[str] | None = None, *,
                     credentials: SnapTradeCredentials | None = None,
                     client_factory: ClientFactory | None = None,
                     page_size: int = ACTIVITIES_PAGE_SIZE) -> list[tuple[Any, list[Any]]]:
    creds = credentials or load_credentials()
    client = _client(creds, client_factory)
    kwargs = user_kwargs(creds)
    accounts = _call("account listing", lambda: client.account_information.list_user_accounts(
        **kwargs
    ).body) or []
    wanted = set(account_ids) if account_ids else None
    pairs = []
    for account in accounts:
        account_id = _text(_value(account, "id"))
        if wanted is not None and account_id not in wanted:
            continue
        rows, offset = [], 0
        while True:
            body = _call("activity retrieval", lambda offset=offset:
                client.account_information.get_account_activities(
                    account_id=account_id, start_date=start_date, end_date=end_date,
                    offset=offset, limit=page_size, **kwargs,
                ).body)
            page = _activities_page(body)
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        pairs.append((account, rows))
    return pairs
