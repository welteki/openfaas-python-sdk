"""Tests for Pydantic models."""
from __future__ import annotations

import pytest

from openfaas.models import (
    FunctionDeployment,
    FunctionNamespace,
    FunctionResources,
    FunctionStatus,
    LogMessage,
    Secret,
    SystemInfo,
)


class TestFunctionDeployment:
    def test_minimal(self) -> None:
        spec = FunctionDeployment(service="env", image="ghcr.io/openfaas/env:latest")
        assert spec.service == "env"
        assert spec.image == "ghcr.io/openfaas/env:latest"
        assert spec.namespace is None

    def test_to_api_dict_excludes_none(self) -> None:
        spec = FunctionDeployment(service="env", image="ghcr.io/openfaas/env:latest")
        d = spec.to_api_dict()
        assert "namespace" not in d
        assert d["service"] == "env"

    def test_to_api_dict_uses_camel_aliases(self) -> None:
        spec = FunctionDeployment(
            service="env",
            image="ghcr.io/openfaas/env:latest",
            env_vars={"KEY": "VALUE"},
            read_only_root_filesystem=True,
        )
        d = spec.to_api_dict()
        assert "envVars" in d
        assert "readOnlyRootFilesystem" in d
        assert "env_vars" not in d

    def test_with_resources(self) -> None:
        spec = FunctionDeployment(
            service="env",
            image="ghcr.io/openfaas/env:latest",
            limits=FunctionResources(memory="128Mi", cpu="100m"),
        )
        d = spec.to_api_dict()
        assert d["limits"] == {"memory": "128Mi", "cpu": "100m"}


class TestFunctionStatus:
    def test_parse_from_api_response(self) -> None:
        data = {
            "name": "env",
            "image": "ghcr.io/openfaas/env:latest",
            "namespace": "openfaas-fn",
            "invocationCount": 42.0,
            "replicas": 1,
            "availableReplicas": 1,
        }
        status = FunctionStatus.model_validate(data)
        assert status.name == "env"
        assert status.invocation_count == 42.0
        assert status.replicas == 1

    def test_missing_optional_fields_default(self) -> None:
        status = FunctionStatus.model_validate({"name": "env"})
        assert status.image == ""
        assert status.replicas == 0
        assert status.usage is None


class TestFunctionNamespace:
    def test_to_api_dict(self) -> None:
        ns = FunctionNamespace(name="staging", labels={"team": "backend"})
        d = ns.to_api_dict()
        assert d["name"] == "staging"
        assert d["labels"] == {"team": "backend"}
        assert "annotations" not in d


class TestSecret:
    def test_to_api_dict_excludes_none(self) -> None:
        s = Secret(name="my-secret", namespace="openfaas-fn", value="s3cr3t")
        d = s.to_api_dict()
        assert d == {"name": "my-secret", "namespace": "openfaas-fn", "value": "s3cr3t"}

    def test_to_api_dict_no_value(self) -> None:
        s = Secret(name="my-secret")
        d = s.to_api_dict()
        assert "value" not in d
        assert "namespace" not in d


class TestSystemInfo:
    def test_parse_empty(self) -> None:
        info = SystemInfo.model_validate({})
        assert info.arch == ""

    def test_parse_full(self) -> None:
        data = {
            "arch": "amd64",
            "provider": {"provider": "faas", "orchestration": "kubernetes"},
            "version": {"release": "0.27.0"},
        }
        info = SystemInfo.model_validate(data)
        assert info.arch == "amd64"
        assert info.provider.orchestration == "kubernetes"
        assert info.version.release == "0.27.0"


class TestLogMessage:
    def test_parse_ndjson(self) -> None:
        line = '{"name":"env","namespace":"openfaas-fn","instance":"env-xxx","text":"hello"}'
        msg = LogMessage.model_validate_json(line)
        assert msg.name == "env"
        assert msg.text == "hello"
