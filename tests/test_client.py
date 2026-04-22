"""Tests for the sync Client using requests-mock."""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import requests
import requests_mock as req_mock

from openfaas import BasicAuth, Client
from openfaas.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from openfaas.models import FunctionDeployment, FunctionNamespace, Secret

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

GATEWAY = "http://gateway.example.com"

FUNCTIONS_LIST = [
    {"name": "env", "image": "ghcr.io/openfaas/env:latest", "namespace": "openfaas-fn",
     "replicas": 1, "availableReplicas": 1, "invocationCount": 0},
]

FUNCTION_DETAIL = {
    "name": "env", "image": "ghcr.io/openfaas/env:latest", "namespace": "openfaas-fn",
    "replicas": 1, "availableReplicas": 1, "invocationCount": 5,
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
