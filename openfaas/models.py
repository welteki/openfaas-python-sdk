"""
Pydantic models for the OpenFaaS Python SDK.

These models map directly to the OpenFaaS REST API request and response
bodies, following the same schema as the faas-provider types used by the
OpenFaaS gateway.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / primitive types
# ---------------------------------------------------------------------------


class VersionInfo(BaseModel):
    commit_message: str = Field(default="", alias="commit_message")
    sha: str = Field(default="")
    release: str = Field(default="")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------


class Provider(BaseModel):
    provider: str = Field(default="")
    version: VersionInfo = Field(default_factory=VersionInfo)
    orchestration: str = Field(default="")


class SystemInfo(BaseModel):
    arch: str = Field(default="")
    provider: Provider = Field(default_factory=Provider)
    version: VersionInfo = Field(default_factory=VersionInfo)


# ---------------------------------------------------------------------------
# Function resources
# ---------------------------------------------------------------------------


class FunctionResources(BaseModel):
    memory: str | None = None
    cpu: str | None = None


# ---------------------------------------------------------------------------
# Function deployment (create / update request body)
# ---------------------------------------------------------------------------


class FunctionDeployment(BaseModel):
    """Request body for deploying or updating a function."""

    service: str
    image: str
    namespace: str | None = None
    env_process: str | None = Field(default=None, alias="envProcess")
    env_vars: dict[str, str] | None = Field(default=None, alias="envVars")
    constraints: list[str] | None = None
    secrets: list[str] | None = None
    labels: dict[str, str] | None = None
    annotations: dict[str, str] | None = None
    limits: FunctionResources | None = None
    requests: FunctionResources | None = None
    read_only_root_filesystem: bool | None = Field(default=None, alias="readOnlyRootFilesystem")

    model_config = {"populate_by_name": True}

    def to_api_dict(self) -> dict:
        """Serialise to the JSON shape expected by the OpenFaaS API."""
        return self.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# Function status (response body from GET /system/functions)
# ---------------------------------------------------------------------------


class FunctionUsage(BaseModel):
    cpu: float | None = Field(default=None)
    total_memory_bytes: float | None = Field(default=None, alias="totalMemoryBytes")

    model_config = {"populate_by_name": True}


class FunctionStatus(BaseModel):
    """Response body for a single function returned by the API."""

    name: str = Field(alias="name")
    image: str = Field(default="")
    namespace: str | None = None
    env_process: str | None = Field(default=None, alias="envProcess")
    env_vars: dict[str, str] | None = Field(default=None, alias="envVars")
    constraints: list[str] | None = None
    secrets: list[str] | None = None
    labels: dict[str, str] | None = None
    annotations: dict[str, str] | None = None
    limits: FunctionResources | None = None
    requests: FunctionResources | None = None
    read_only_root_filesystem: bool | None = Field(default=None, alias="readOnlyRootFilesystem")
    invocation_count: float = Field(default=0.0, alias="invocationCount")
    replicas: int = Field(default=0)
    available_replicas: int = Field(default=0, alias="availableReplicas")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    usage: FunctionUsage | None = None

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------


class FunctionNamespace(BaseModel):
    name: str = Field(default="")
    labels: dict[str, str] | None = None
    annotations: dict[str, str] | None = None

    def to_api_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class Secret(BaseModel):
    name: str
    namespace: str | None = None
    value: str | None = None

    def to_api_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


class LogMessage(BaseModel):
    name: str = Field(default="")
    namespace: str | None = None
    instance: str | None = None
    timestamp: datetime | None = None
    text: str = Field(default="")
