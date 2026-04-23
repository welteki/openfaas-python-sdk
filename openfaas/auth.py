"""
Authentication for the OpenFaaS Python SDK.

All auth classes subclass :class:`requests.auth.AuthBase`, which means they
work with :class:`requests.Session` and can be passed anywhere requests
accepts an ``auth=`` argument.

Provided implementations:

* :class:`BasicAuth`                    — HTTP Basic authentication
* :class:`TokenAuth`                    — OpenFaaS IAM token exchange auth
* :class:`ServiceAccountTokenSource`   — Reads a Kubernetes projected service
                                          account token from disk
* :class:`ClientCredentialsTokenSource` — Fetches tokens from an IdP via the
                                           OAuth 2.0 client_credentials grant
* :class:`ClientCredentialsAuth`        — ``requests.auth.AuthBase`` wrapper
                                           around a :class:`TokenSource`

Token source protocol:

* :class:`TokenSource` — anything with a ``token() -> str`` method
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Protocol, runtime_checkable

import requests
import requests.auth

from openfaas.exchange import exchange_id_token
from openfaas.token import OAuthError, Token, _parse_token_response

logger = logging.getLogger("openfaas")


# ---------------------------------------------------------------------------
# Token source protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TokenSource(Protocol):
    """Protocol for objects that can provide a raw identity token synchronously.

    Any object with a ``token() -> str`` method satisfies this protocol.
    """

    def token(self) -> str:
        """Return a raw JWT identity token string."""
        ...


# ---------------------------------------------------------------------------
# Basic auth
# ---------------------------------------------------------------------------


class BasicAuth(requests.auth.AuthBase):
    """HTTP Basic authentication using a username and password.

    Example::

        auth = BasicAuth(username="admin", password="secret")
        client = Client("https://gateway.example.com", auth=auth)

    The password can be read from a mounted secret file::

        with open("/var/secrets/basic-auth-password") as f:
            auth = BasicAuth("admin", f.read().strip())
    """

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._requests_auth = requests.auth.HTTPBasicAuth(username, password)

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        return self._requests_auth(r)

    def __repr__(self) -> str:
        return f"BasicAuth(username={self.username!r})"


# ---------------------------------------------------------------------------
# Token auth (OpenFaaS IAM)
# ---------------------------------------------------------------------------


class TokenAuth(requests.auth.AuthBase):
    """OpenFaaS IAM authentication via OAuth 2.0 token exchange.

    Wraps an upstream :class:`TokenSource` and exchanges the upstream identity
    token for an OpenFaaS gateway JWT on first use, then caches and
    auto-refreshes it.

    ``TokenAuth`` also implements :class:`TokenSource`, so it can be used
    directly as a ``function_token_source`` on the client — enabling
    per-function scoped token exchange for function invocation.

    Example — Kubernetes workload::

        from openfaas import Client, TokenAuth, ServiceAccountTokenSource

        auth = TokenAuth(
            token_url="https://gateway.example.com/oauth/token",
            token_source=ServiceAccountTokenSource(),
        )
        client = Client("https://gateway.example.com", auth=auth)

    Example — external IdP via client credentials::

        from openfaas import Client, TokenAuth, ClientCredentialsTokenSource

        ts = ClientCredentialsTokenSource(
            client_id="my-app",
            client_secret="secret",
            token_url="https://idp.example.com/token",
            scope="openid",
        )
        auth = TokenAuth(token_url="https://gateway.example.com/oauth/token", token_source=ts)
        client = Client("https://gateway.example.com", auth=auth)
    """

    def __init__(self, token_url: str, token_source: TokenSource) -> None:
        self._token_url = token_url
        self._token_source = token_source
        self._token: Token | None = None
        self._lock = threading.Lock()

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        r.headers["Authorization"] = f"Bearer {self.token()}"
        return r

    # TokenSource protocol ------------------------------------------------

    def token(self) -> str:
        """Return a valid gateway token, exchanging a new one if necessary."""
        with self._lock:
            if self._token is None or self._token.is_expired():
                id_token = self._token_source.token()
                try:
                    self._token = exchange_id_token(self._token_url, id_token)
                except OAuthError:
                    raise
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to exchange token for an OpenFaaS token: {exc}"
                    ) from exc
            return self._token.id_token

    def __repr__(self) -> str:
        return f"TokenAuth(token_url={self._token_url!r})"


# ---------------------------------------------------------------------------
# Kubernetes service account token source
# ---------------------------------------------------------------------------

_DEFAULT_TOKEN_MOUNT_PATH = "/var/secrets/tokens"
_TOKEN_FILENAME = "openfaas-token"


class ServiceAccountTokenSource:
    """Reads a Kubernetes projected service account token from disk.

    The token is read from ``<token_mount_path>/openfaas-token`` on every
    call to :meth:`token`.  The file is re-read each time rather than
    cached because Kubernetes rotates projected tokens in-place.

    The mount path defaults to ``/var/secrets/tokens`` and can be overridden
    via the ``token_mount_path`` environment variable.

    Example::

        from openfaas import TokenAuth, ServiceAccountTokenSource

        auth = TokenAuth(
            token_url="https://gateway.example.com/oauth/token",
            token_source=ServiceAccountTokenSource(),
        )
    """

    def token(self) -> str:
        """Read and return the raw service account token from disk."""
        mount_path = os.environ.get("token_mount_path", _DEFAULT_TOKEN_MOUNT_PATH).strip()
        if not mount_path:
            raise ValueError(
                "Invalid token_mount_path: path is empty. "
                "Set the 'token_mount_path' environment variable."
            )
        token_path = os.path.join(mount_path, _TOKEN_FILENAME)
        try:
            with open(token_path) as f:
                return f.read().strip()
        except OSError as exc:
            raise RuntimeError(
                f"Unable to load service account token from {token_path}: {exc}"
            ) from exc

    def __repr__(self) -> str:
        mount_path = os.environ.get("token_mount_path", _DEFAULT_TOKEN_MOUNT_PATH)
        return f"ServiceAccountTokenSource(path={os.path.join(mount_path, _TOKEN_FILENAME)!r})"


# ---------------------------------------------------------------------------
# Client credentials token source
# ---------------------------------------------------------------------------


class ClientCredentialsTokenSource:
    """Fetches tokens from an IdP using the OAuth 2.0 client_credentials grant.

    Tokens are cached internally and refreshed automatically when expired.

    Example::

        from openfaas import TokenAuth, ClientCredentialsTokenSource

        ts = ClientCredentialsTokenSource(
            client_id="my-app",
            client_secret="secret",
            token_url="https://idp.example.com/realms/master/protocol/openid-connect/token",
            scope="openid",
        )
        auth = TokenAuth(
            token_url="https://gateway.example.com/oauth/token",
            token_source=ts,
        )
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        scope: str = "",
        grant_type: str = "client_credentials",
        audience: str = "",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scope = scope
        self._grant_type = grant_type
        self._audience = audience
        self._token: Token | None = None
        self._lock = threading.Lock()

    def _build_data(self) -> dict[str, str]:
        data: dict[str, str] = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": self._grant_type,
        }
        if self._scope:
            data["scope"] = self._scope
        if self._audience:
            data["audience"] = self._audience
        return data

    def token(self) -> str:
        """Return a valid access token, fetching a new one if necessary."""
        with self._lock:
            if self._token is None or self._token.is_expired():
                self._token = self._fetch()
            return self._token.id_token

    def _fetch(self) -> Token:
        with requests.Session() as session:
            response = session.post(self._token_url, data=self._build_data())
        if not response.ok:
            raise RuntimeError(
                f"Failed to obtain client credentials token: "
                f"HTTP {response.status_code} — {response.text}"
            )
        return _parse_token_response(response.json())

    def __repr__(self) -> str:
        return (
            f"ClientCredentialsTokenSource("
            f"client_id={self._client_id!r}, token_url={self._token_url!r})"
        )


# ---------------------------------------------------------------------------
# Client credentials auth (requests.auth.AuthBase wrapper)
# ---------------------------------------------------------------------------


class ClientCredentialsAuth(requests.auth.AuthBase):
    """``requests.auth.AuthBase`` wrapper around any :class:`TokenSource`.

    Sets a ``Bearer`` token header on each request by delegating to the
    underlying token source.

    Typically used with :class:`ClientCredentialsTokenSource` when you want to
    authenticate directly to the gateway using client credentials rather than
    going through the OpenFaaS IAM token exchange.

    Example::

        ts = ClientCredentialsTokenSource(...)
        auth = ClientCredentialsAuth(ts)
        client = Client("https://gateway.example.com", auth=auth)
    """

    def __init__(self, token_source: TokenSource) -> None:
        self._token_source = token_source

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        r.headers["Authorization"] = f"Bearer {self._token_source.token()}"
        return r

    def __repr__(self) -> str:
        return f"ClientCredentialsAuth(token_source={self._token_source!r})"
