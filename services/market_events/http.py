"""Injectable HTTP GET transport.

Production uses ``urllib``. Tests pass a fake. Credential-bearing URLs are
rejected before a request is built so a secret cannot land in a traceback.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class TransportError(Exception):
    """A provider request failed. The message is an exception type, not the raw error."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


class HttpTransport(Protocol):
    def get(self, url: str, headers: dict[str, str] | None = None) -> "HttpResponse":
        """Perform one GET and return the status, body, and headers."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str


def public_https_url(url: str) -> str:
    """Return a credential-free https URL, or raise ``TransportError``."""
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise TransportError("invalid_url")
    if parsed.username or parsed.password:
        raise TransportError("credentialed_url")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    blocked = {"key", "api_key", "apikey", "token", "secret", "password"}
    if any(name.lower() in blocked for name in query):
        raise TransportError("credentialed_url")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


class UrllibTransport:
    """GET via an injectable opener. The default opener is ``urllib.request.urlopen``."""

    def __init__(self, opener=None, timeout: float = 30.0) -> None:
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        safe_url = public_https_url(url)
        request = urllib.request.Request(safe_url, headers=dict(headers or {}), method="GET")
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw_headers = {key: value for key, value in response.headers.items()}
                return HttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=raw_headers,
                    url=safe_url,
                )
        except TransportError:
            raise
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            raw_headers = {key: value for key, value in exc.headers.items()} if exc.headers else {}
            return HttpResponse(status=int(exc.code), body=body, headers=raw_headers, url=safe_url)
        except Exception as exc:
            raise TransportError(type(exc).__name__) from None
