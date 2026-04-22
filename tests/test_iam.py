"""Tests for OpenFaaS IAM authentication components.

Covers:
- Token / OAuthError / _parse_token_response
- MemoryTokenCache
- exchange_id_token
- ServiceAccountTokenSource
- ClientCredentialsTokenSource
- TokenAuth (sync auth_flow, sync_token)
- ClientCredentialsAuth (sync auth_flow)
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
import requests_mock as req_mock

from openfaas.auth import (
    ClientCredentialsAuth,
    ClientCredentialsTokenSource,
    ServiceAccountTokenSource,
    TokenAuth,
    TokenSource,
)
from openfaas.exchange import exchange_id_token
from openfaas.token import OAuthError, Token, _parse_token_response
from openfaas.token_cache import MemoryTokenCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUTURE = datetime.now(tz=timezone.utc) + timedelta(hours=1)
_PAST = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
_ALMOST_EXPIRED = datetime.now(tz=timezone.utc) + timedelta(seconds=5)


def _token(value: str = "tok", *, expiry: datetime | None = _FUTURE) -> Token:
    return Token(id_token=value, expiry=expiry)


def _apply_auth(auth: requests.auth.AuthBase) -> requests.PreparedRequest:
    """Run auth against a PreparedRequest and return it."""
    req = requests.Request("GET", "http://gateway.example.com/system/functions")
    prepared = req.prepare()
    return auth(prepared)


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


class TestToken:
    def test_not_expired_when_no_expiry(self) -> None:
        assert Token(id_token="tok").is_expired() is False

    def test_not_expired_when_far_future(self) -> None:
        assert _token().is_expired() is False

    def test_expired_when_in_past(self) -> None:
        assert Token(id_token="tok", expiry=_PAST).is_expired() is True

    def test_expired_within_10s_delta(self) -> None:
        assert Token(id_token="tok", expiry=_ALMOST_EXPIRED).is_expired() is True

    def test_scope_defaults_to_empty(self) -> None:
        assert _token().scope == []


class TestOAuthError:
    def test_message_without_description(self) -> None:
        err = OAuthError("invalid_grant")
        assert str(err) == "invalid_grant"
        assert err.error == "invalid_grant"
        assert err.error_description == ""

    def test_message_with_description(self) -> None:
        err = OAuthError("invalid_grant", "token has expired")
        assert "invalid_grant" in str(err)
        assert "token has expired" in str(err)


class TestParseTokenResponse:
    def test_basic_response(self) -> None:
        data = {"access_token": "mytoken", "expires_in": 3600}
        tok = _parse_token_response(data)
        assert tok.id_token == "mytoken"
        assert tok.expiry is not None
        assert tok.expiry > datetime.now(tz=timezone.utc)

    def test_scope_parsed(self) -> None:
        data = {"access_token": "t", "expires_in": 3600, "scope": "openid function"}
        tok = _parse_token_response(data)
        assert tok.scope == ["openid", "function"]

    def test_no_expires_in_gives_none_expiry(self) -> None:
        tok = _parse_token_response({"access_token": "t"})
        assert tok.expiry is None

    def test_zero_expires_in_gives_none_expiry(self) -> None:
        tok = _parse_token_response({"access_token": "t", "expires_in": 0})
        assert tok.expiry is None


# ---------------------------------------------------------------------------
# MemoryTokenCache
# ---------------------------------------------------------------------------


class TestMemoryTokenCache:
    def test_get_returns_none_for_missing_key(self) -> None:
        cache = MemoryTokenCache()
        assert cache.get("ns:fn") is None

    def test_set_then_get_returns_token(self) -> None:
        cache = MemoryTokenCache()
        tok = _token("abc")
        cache.set("ns:fn", tok)
        assert cache.get("ns:fn") is tok

    def test_get_evicts_expired_token(self) -> None:
        cache = MemoryTokenCache()
        cache.set("ns:fn", Token(id_token="x", expiry=_PAST))
        assert cache.get("ns:fn") is None

    def test_get_evicts_almost_expired_token(self) -> None:
        cache = MemoryTokenCache()
        cache.set("ns:fn", Token(id_token="x", expiry=_ALMOST_EXPIRED))
        assert cache.get("ns:fn") is None

    def test_set_overwrites_existing(self) -> None:
        cache = MemoryTokenCache()
        cache.set("k", _token("first"))
        cache.set("k", _token("second"))
        assert cache.get("k").id_token == "second"  # type: ignore[union-attr]

    def test_clear_expired_removes_only_expired(self) -> None:
        cache = MemoryTokenCache()
        cache.set("alive", _token("a"))
        cache.set("dead", Token(id_token="d", expiry=_PAST))
        cache.clear_expired()
        assert cache.get("alive") is not None
        assert cache.get("dead") is None

    def test_thread_safety(self) -> None:
        cache = MemoryTokenCache()
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(50):
                    key = f"fn-{n}-{i}"
                    cache.set(key, _token(key))
                    cache.get(key)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# exchange_id_token
# ---------------------------------------------------------------------------

_TOKEN_URL = "https://gateway.example.com/oauth/token"
_ID_TOKEN = "eyJhbGciOiJSUzI1NiJ9.fake"


class TestExchangeIdToken:
    def test_returns_token_on_success(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "gw-token", "expires_in": 3600})
            tok = exchange_id_token(_TOKEN_URL, _ID_TOKEN)
        assert tok.id_token == "gw-token"
        assert tok.expiry is not None

    def test_sends_subject_token(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "t", "expires_in": 3600})
            exchange_id_token(_TOKEN_URL, "my-id-token")
            assert "my-id-token" in m.last_request.text  # type: ignore[union-attr]

    def test_raises_oauth_error_on_400(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, status_code=400, json={"error": "invalid_grant", "error_description": "token expired"})
            with pytest.raises(OAuthError) as exc_info:
                exchange_id_token(_TOKEN_URL, _ID_TOKEN)
        assert exc_info.value.error == "invalid_grant"

    def test_raises_http_error_on_500(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, status_code=500)
            with pytest.raises(requests.HTTPError):
                exchange_id_token(_TOKEN_URL, _ID_TOKEN)

    def test_audience_included_when_provided(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "t", "expires_in": 3600})
            exchange_id_token(_TOKEN_URL, _ID_TOKEN, audience=["openfaas-fn:my-func"])
            assert "audience" in m.last_request.text  # type: ignore[union-attr]

    def test_scope_included_when_provided(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "t", "expires_in": 3600})
            exchange_id_token(_TOKEN_URL, _ID_TOKEN, scope=["function"])
            assert "scope=function" in m.last_request.text  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# ServiceAccountTokenSource
# ---------------------------------------------------------------------------


class TestServiceAccountTokenSource:
    def test_reads_token_from_file(self, tmp_path: Path) -> None:
        (tmp_path / "openfaas-token").write_text("my-sa-token\n")
        src = ServiceAccountTokenSource()
        with patch.dict(os.environ, {"token_mount_path": str(tmp_path)}):
            assert src.sync_token() == "my-sa-token"

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / "openfaas-token").write_text("  tok  \n")
        src = ServiceAccountTokenSource()
        with patch.dict(os.environ, {"token_mount_path": str(tmp_path)}):
            assert src.sync_token() == "tok"

    def test_raises_runtime_error_if_file_missing(self, tmp_path: Path) -> None:
        src = ServiceAccountTokenSource()
        with patch.dict(os.environ, {"token_mount_path": str(tmp_path)}):
            with pytest.raises(RuntimeError, match="Unable to load service account token"):
                src.sync_token()

    def test_raises_value_error_if_path_empty(self) -> None:
        src = ServiceAccountTokenSource()
        with patch.dict(os.environ, {"token_mount_path": ""}):
            with pytest.raises(ValueError, match="Invalid token_mount_path"):
                src.sync_token()

    def test_re_reads_on_every_call(self, tmp_path: Path) -> None:
        token_file = tmp_path / "openfaas-token"
        token_file.write_text("first")
        src = ServiceAccountTokenSource()
        with patch.dict(os.environ, {"token_mount_path": str(tmp_path)}):
            assert src.sync_token() == "first"
            token_file.write_text("second")
            assert src.sync_token() == "second"

    def test_satisfies_token_source_protocol(self) -> None:
        assert isinstance(ServiceAccountTokenSource(), TokenSource)

    def test_repr(self) -> None:
        src = ServiceAccountTokenSource()
        assert "ServiceAccountTokenSource" in repr(src)
        assert "openfaas-token" in repr(src)


# ---------------------------------------------------------------------------
# ClientCredentialsTokenSource
# ---------------------------------------------------------------------------

_IDP_TOKEN_URL = "https://idp.example.com/token"


class TestClientCredentialsTokenSource:
    def _make_source(self) -> ClientCredentialsTokenSource:
        return ClientCredentialsTokenSource(
            client_id="app",
            client_secret="secret",
            token_url=_IDP_TOKEN_URL,
            scope="openid",
        )

    def test_sync_returns_token(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_IDP_TOKEN_URL, json={"access_token": "cc-token", "expires_in": 3600})
            src = self._make_source()
            assert src.sync_token() == "cc-token"

    def test_sync_caches_valid_token(self) -> None:
        call_count = 0

        def handler(request: requests.PreparedRequest, context: object) -> dict:  # type: ignore[type-arg]
            nonlocal call_count
            call_count += 1
            return {"access_token": "t", "expires_in": 3600}

        with req_mock.Mocker() as m:
            m.post(_IDP_TOKEN_URL, json=handler)
            src = self._make_source()
            src.sync_token()
            src.sync_token()
        assert call_count == 1

    def test_satisfies_token_source_protocol(self) -> None:
        src = ClientCredentialsTokenSource(
            client_id="a", client_secret="b", token_url=_IDP_TOKEN_URL
        )
        assert isinstance(src, TokenSource)

    def test_repr_does_not_leak_secret(self) -> None:
        src = ClientCredentialsTokenSource(
            client_id="app", client_secret="topsecret", token_url=_IDP_TOKEN_URL
        )
        r = repr(src)
        assert "ClientCredentialsTokenSource" in r
        assert "app" in r
        assert "topsecret" not in r


# ---------------------------------------------------------------------------
# TokenAuth
# ---------------------------------------------------------------------------


class _FakeTokenSource:
    """Minimal sync-only TokenSource."""

    def __init__(self, value: str = "upstream-token") -> None:
        self._value = value
        self.call_count = 0

    def sync_token(self) -> str:
        self.call_count += 1
        return self._value


def _make_token_auth(
    upstream: str = "upstream-token",
    gw_token: str = "gw-token",
    expires_in: int = 3600,
) -> tuple[TokenAuth, _FakeTokenSource]:
    """Build a TokenAuth backed by a _FakeTokenSource."""
    source = _FakeTokenSource(upstream)
    auth = TokenAuth(token_url=_TOKEN_URL, token_source=source)
    return auth, source


class TestTokenAuth:
    def test_sync_sets_bearer_header(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "gw-token", "expires_in": 3600})
            auth, _ = _make_token_auth()
            prepared = _apply_auth(auth)
        assert prepared.headers["Authorization"] == "Bearer gw-token"

    def test_sync_caches_token(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "gw-token", "expires_in": 3600})
            auth, source = _make_token_auth()
            _apply_auth(auth)
            _apply_auth(auth)
        assert source.call_count == 1

    def test_sync_refreshes_expired_token(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "gw-token", "expires_in": 3600})
            auth, source = _make_token_auth()
            auth._token = Token(id_token="old", expiry=_PAST)
            _apply_auth(auth)
        assert source.call_count == 1

    def test_sync_token_returns_string(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, json={"access_token": "gw-token", "expires_in": 3600})
            auth, _ = _make_token_auth()
            assert auth.sync_token() == "gw-token"

    def test_satisfies_token_source_protocol(self) -> None:
        auth = TokenAuth(token_url=_TOKEN_URL, token_source=_FakeTokenSource())
        assert isinstance(auth, TokenSource)

    def test_is_requests_auth(self) -> None:
        import requests.auth
        auth = TokenAuth(token_url=_TOKEN_URL, token_source=_FakeTokenSource())
        assert isinstance(auth, requests.auth.AuthBase)

    def test_raises_oauth_error_on_bad_exchange(self) -> None:
        with req_mock.Mocker() as m:
            m.post(_TOKEN_URL, status_code=400, json={"error": "invalid_grant", "error_description": "upstream token rejected"})
            auth = TokenAuth(token_url=_TOKEN_URL, token_source=_FakeTokenSource())
            with pytest.raises(OAuthError, match="invalid_grant"):
                auth.sync_token()

    def test_repr(self) -> None:
        auth = TokenAuth(token_url=_TOKEN_URL, token_source=_FakeTokenSource())
        assert "TokenAuth" in repr(auth)
        assert _TOKEN_URL in repr(auth)


# ---------------------------------------------------------------------------
# ClientCredentialsAuth
# ---------------------------------------------------------------------------


class TestClientCredentialsAuth:
    def test_sync_sets_bearer_header(self) -> None:
        source = _FakeTokenSource("cc-tok")
        auth = ClientCredentialsAuth(source)
        prepared = _apply_auth(auth)
        assert prepared.headers["Authorization"] == "Bearer cc-tok"

    def test_is_requests_auth(self) -> None:
        import requests.auth
        assert isinstance(ClientCredentialsAuth(_FakeTokenSource()), requests.auth.AuthBase)

    def test_repr(self) -> None:
        auth = ClientCredentialsAuth(_FakeTokenSource())
        assert "ClientCredentialsAuth" in repr(auth)
