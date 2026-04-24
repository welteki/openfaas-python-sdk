"""Tests for the sync Client using requests-mock."""

from __future__ import annotations

import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest
import requests
import requests_mock as req_mock

from openfaas import BasicAuth, Client
from openfaas.exceptions import NotFoundError
from openfaas.models import FunctionDeployment, FunctionNamespace, Secret

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

GATEWAY = "http://gateway.example.com"

FUNCTIONS_LIST = [
    {
        "name": "env",
        "image": "ghcr.io/openfaas/env:latest",
        "namespace": "openfaas-fn",
        "replicas": 1,
        "availableReplicas": 1,
        "invocationCount": 0,
    },
]

FUNCTION_DETAIL = {
    "name": "env",
    "image": "ghcr.io/openfaas/env:latest",
    "namespace": "openfaas-fn",
    "replicas": 1,
    "availableReplicas": 1,
    "invocationCount": 5,
}

NAMESPACES_LIST = ["openfaas-fn", "staging"]

NAMESPACE_DETAIL = {"name": "openfaas-fn", "labels": {"openfaas": "1"}}

SECRETS_LIST = [{"name": "my-secret", "namespace": "openfaas-fn"}]

SYSTEM_INFO = {
    "arch": "amd64",
    "provider": {"provider": "faas", "orchestration": "kubernetes"},
    "version": {"release": "0.27.0"},
}

LOG_LINES = [
    '{"name":"env","namespace":"openfaas-fn","instance":"env-xxx","text":"starting"}',
    '{"name":"env","namespace":"openfaas-fn","instance":"env-xxx","text":"ready"}',
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_gateway() -> Iterator[req_mock.Mocker]:
    """Activate requests-mock and register all gateway endpoints."""
    with req_mock.Mocker() as m:
        m.get(f"{GATEWAY}/system/info", json=SYSTEM_INFO)
        m.get(f"{GATEWAY}/system/namespaces", json=NAMESPACES_LIST)
        m.get(req_mock.ANY, json=NAMESPACE_DETAIL)  # /system/namespace/<name>
        m.post(f"{GATEWAY}/system/namespace/", status_code=201)
        m.put(req_mock.ANY, status_code=200)
        m.delete(req_mock.ANY, status_code=200)
        m.get(f"{GATEWAY}/system/functions", json=FUNCTIONS_LIST)
        m.post(f"{GATEWAY}/system/functions", status_code=202)
        m.get(f"{GATEWAY}/system/secrets", json=SECRETS_LIST)
        m.post(f"{GATEWAY}/system/secrets", status_code=201)
        m.get(f"{GATEWAY}/system/logs", text="\n".join(LOG_LINES))
        yield m


@pytest.fixture()
def client(mock_gateway: req_mock.Mocker) -> Iterator[Client]:
    with Client(GATEWAY, auth=BasicAuth("admin", "secret")) as c:
        yield c


# ---------------------------------------------------------------------------
# Per-endpoint mock helpers used by tests that need specific routing
# ---------------------------------------------------------------------------


def _echo_handler(request: requests.PreparedRequest, context: object) -> dict:  # type: ignore[type-arg]
    """Echo back path, method, body and headers — used for invoke assertions."""
    return {
        "path": request.path_url.split("?")[0],
        "method": request.method,
        "body": request.body.decode() if isinstance(request.body, bytes) else (request.body or ""),
        "headers": dict(request.headers),
        "callback_url": request.headers.get("X-Callback-Url", ""),
    }


def _make_client(m: req_mock.Mocker) -> Client:
    """Register the standard routes on *m* and return a Client."""
    m.get(f"{GATEWAY}/system/info", json=SYSTEM_INFO)
    m.get(f"{GATEWAY}/system/namespaces", json=NAMESPACES_LIST)
    m.get(f"{GATEWAY}/system/namespace/openfaas-fn", json=NAMESPACE_DETAIL)
    m.post(f"{GATEWAY}/system/namespace/", status_code=201)
    m.put(f"{GATEWAY}/system/namespace/staging", status_code=200)
    m.delete(f"{GATEWAY}/system/namespace/staging", status_code=200)
    m.get(f"{GATEWAY}/system/functions", json=FUNCTIONS_LIST)
    m.get(f"{GATEWAY}/system/function/env", json=FUNCTION_DETAIL)
    m.get(f"{GATEWAY}/system/function/missing", status_code=404)
    m.post(f"{GATEWAY}/system/functions", status_code=202)
    m.put(f"{GATEWAY}/system/functions", status_code=200)
    m.delete(f"{GATEWAY}/system/functions", status_code=200)
    m.post(f"{GATEWAY}/system/scale-function/env", status_code=202)
    m.get(f"{GATEWAY}/system/secrets", json=SECRETS_LIST)
    m.post(f"{GATEWAY}/system/secrets", status_code=201)
    m.put(f"{GATEWAY}/system/secrets", status_code=200)
    m.delete(f"{GATEWAY}/system/secrets", status_code=200)
    m.get(f"{GATEWAY}/system/logs", text="\n".join(LOG_LINES))
    m.get(f"{GATEWAY}/auth-required", status_code=401)
    m.get(f"{GATEWAY}/forbidden", status_code=403)
    # Function invocation echo — matches any method on /function/... and /async-function/...
    m.register_uri(req_mock.ANY, re.compile(rf"{re.escape(GATEWAY)}/(async-function|function)/.*"), json=_echo_handler)
    return Client(GATEWAY, auth=BasicAuth("admin", "secret"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClientSync:
    def test_get_info(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            info = c.get_info()
        assert info.arch == "amd64"
        assert info.provider.orchestration == "kubernetes"

    def test_get_namespaces(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            ns = c.get_namespaces()
        assert "openfaas-fn" in ns

    def test_get_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            ns = c.get_namespace("openfaas-fn")
        assert ns.name == "openfaas-fn"

    def test_create_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            status = c.create_namespace(FunctionNamespace(name="staging"))
        assert status == 201

    def test_update_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            status = c.update_namespace(FunctionNamespace(name="staging"))
        assert status == 200

    def test_delete_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            c.delete_namespace("staging")  # should not raise

    def test_get_functions(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            fns = c.get_functions("openfaas-fn")
        assert len(fns) == 1
        assert fns[0].name == "env"

    def test_get_function(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            fn = c.get_function("env", "openfaas-fn")
        assert fn.name == "env"
        assert fn.invocation_count == 5

    def test_get_function_not_found(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            with pytest.raises(NotFoundError):
                c.get_function("missing", "openfaas-fn")

    def test_deploy(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            spec = FunctionDeployment(service="env", image="ghcr.io/openfaas/env:latest", namespace="openfaas-fn")
            status = c.deploy(spec)
        assert status == 202

    def test_update(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            spec = FunctionDeployment(service="env", image="ghcr.io/openfaas/env:latest")
            status = c.update(spec)
        assert status == 200

    def test_delete_function(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            c.delete_function("env", "openfaas-fn")  # should not raise

    def test_scale_function(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            c.scale_function("env", 3, "openfaas-fn")  # should not raise

    def test_get_secrets(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            secrets = c.get_secrets("openfaas-fn")
        assert len(secrets) == 1
        assert secrets[0].name == "my-secret"

    def test_create_secret(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            status = c.create_secret(Secret(name="new-secret", namespace="openfaas-fn", value="val"))
        assert status == 201

    def test_update_secret(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            status = c.update_secret(Secret(name="my-secret", value="new-val"))
        assert status == 200

    def test_delete_secret(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            c.delete_secret("my-secret", "openfaas-fn")  # should not raise

    def test_context_manager(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            with c:
                ns = c.get_namespaces()
        assert isinstance(ns, list)

    def test_get_logs(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            msgs = list(c.get_logs("env", "openfaas-fn"))
        assert len(msgs) == 2
        assert msgs[0].text == "starting"
        assert msgs[1].text == "ready"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# invoke_function / invoke_function_async
# ---------------------------------------------------------------------------


class TestClientInvokeSync:
    def test_invoke_basic_post(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", method="POST", payload=b"hello")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/function/env.openfaas-fn"
        assert data["method"] == "POST"
        assert data["body"] == "hello"

    def test_invoke_str_payload(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", method="POST", payload="world")
        assert resp.json()["body"] == "world"

    def test_invoke_no_payload(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", method="POST")
        assert resp.status_code == 200
        assert resp.json()["body"] == ""

    def test_invoke_custom_method(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", method="GET")
        assert resp.json()["method"] == "GET"

    def test_invoke_custom_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", "staging", method="POST")
        assert resp.json()["path"] == "/function/env.staging"

    def test_invoke_custom_headers(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", method="POST", headers={"X-Custom": "value"})
        assert resp.json()["headers"]["X-Custom"] == "value"

    def test_invoke_query_params(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function("env", method="GET", query_params={"foo": "bar"})
        assert resp.status_code == 200

    def test_non_2xx_not_raised(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            # Register a 500 for a specific function path — should be returned, not raised
            m.post(f"{GATEWAY}/function/broken.openfaas-fn", status_code=500, text="internal error")
            resp = c.invoke_function("broken", method="POST")
        assert resp.status_code == 500

    def test_invoke_with_function_auth(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            with patch.object(c, "get_function_token", return_value="fn-tok") as mock_gft:
                resp = c.invoke_function("env", method="POST", use_function_auth=True)
            mock_gft.assert_called_once_with("env", "openfaas-fn")
        assert resp.json()["headers"]["Authorization"] == "Bearer fn-tok"

    def test_invoke_with_function_auth_custom_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            with patch.object(c, "get_function_token", return_value="scoped-tok") as mock_gft:
                c.invoke_function("env", "staging", method="POST", use_function_auth=True)
            mock_gft.assert_called_once_with("env", "staging")


class TestClientInvokeAsync:
    def test_invoke_async_route(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function_async("env")
        assert resp.json()["path"] == "/async-function/env.openfaas-fn"

    def test_invoke_async_uses_post(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function_async("env")
        assert resp.json()["method"] == "POST"

    def test_invoke_async_custom_namespace(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function_async("env", "staging")
        assert resp.json()["path"] == "/async-function/env.staging"

    def test_invoke_async_with_payload(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function_async("env", payload=b"data")
        assert resp.json()["body"] == "data"

    def test_invoke_async_with_callback_url(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            resp = c.invoke_function_async("env", callback_url="https://example.com/cb")
        assert resp.json()["callback_url"] == "https://example.com/cb"

    def test_invoke_async_with_function_auth(self) -> None:
        with req_mock.Mocker() as m:
            c = _make_client(m)
            with patch.object(c, "get_function_token", return_value="fn-tok") as mock_gft:
                resp = c.invoke_function_async("env", use_function_auth=True)
            mock_gft.assert_called_once_with("env", "openfaas-fn")
        assert resp.json()["headers"]["Authorization"] == "Bearer fn-tok"
