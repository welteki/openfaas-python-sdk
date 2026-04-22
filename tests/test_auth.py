"""Tests for auth implementations."""
from __future__ import annotations

import base64

import pytest
import requests
import requests.auth

from openfaas.auth import BasicAuth, ClientCredentialsAuth, TokenSource


def _apply_auth(auth: requests.auth.AuthBase) -> requests.PreparedRequest:
    """Build a PreparedRequest and run auth through it."""
    req = requests.Request("GET", "http://gateway.example.com/system/functions")
    prepared = req.prepare()
    return auth(prepared)


class TestBasicAuth:
    def test_sets_authorization_header(self) -> None:
        auth = BasicAuth(username="admin", password="secret")
        prepared = _apply_auth(auth)
        assert "Authorization" in prepared.headers

    def test_header_is_basic_scheme(self) -> None:
        auth = BasicAuth(username="admin", password="secret")
        prepared = _apply_auth(auth)
        assert prepared.headers["Authorization"].startswith("Basic ")

    def test_header_encodes_credentials_correctly(self) -> None:
        auth = BasicAuth(username="admin", password="secret")
        prepared = _apply_auth(auth)
        encoded = prepared.headers["Authorization"].removeprefix("Basic ")
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "admin:secret"

    def test_repr(self) -> None:
        auth = BasicAuth(username="admin", password="secret")
        assert repr(auth) == "BasicAuth(username='admin')"

    def test_is_requests_auth(self) -> None:
        assert isinstance(BasicAuth("admin", "secret"), requests.auth.AuthBase)
