"""
OpenFaaS Python SDK
===================

A Python client for the OpenFaaS gateway API.

Quickstart — Basic auth::

    from openfaas import Client, BasicAuth

    with Client("https://gateway.example.com", auth=BasicAuth("admin", "secret")) as client:
        functions = client.get_functions("openfaas-fn")
        for fn in functions:
            print(fn.name, fn.replicas)

Quickstart — IAM auth (Kubernetes workload)::

    from openfaas import Client, TokenAuth, ServiceAccountTokenSource

    auth = TokenAuth(
        token_url="https://gateway.example.com/oauth/token",
        token_source=ServiceAccountTokenSource(),
    )
    with Client("https://gateway.example.com", auth=auth) as client:
        functions = client.get_functions("openfaas-fn")
"""

from openfaas._version import __version__
from openfaas.auth import (
    BasicAuth,
    ClientCredentialsAuth,
    ClientCredentialsTokenSource,
    ServiceAccountTokenSource,
    TokenAuth,
    TokenSource,
)
from openfaas.client import Client
from openfaas.exchange import exchange_id_token
from openfaas.exceptions import (
    APIConnectionError,
    APIStatusError,
    ForbiddenError,
    NotFoundError,
    OpenFaaSError,
    UnauthorizedError,
    UnexpectedStatusError,
)
from openfaas.models import (
    FunctionDeployment,
    FunctionNamespace,
    FunctionResources,
    FunctionStatus,
    FunctionUsage,
    LogMessage,
    Provider,
    Secret,
    SystemInfo,
    VersionInfo,
)
from openfaas.token import OAuthError, Token
from openfaas.token_cache import MemoryTokenCache, TokenCache

__all__ = [
    # Version
    "__version__",
    # Clients
    "Client",
    # Auth
    "TokenSource",
    "BasicAuth",
    "TokenAuth",
    "ServiceAccountTokenSource",
    "ClientCredentialsTokenSource",
    "ClientCredentialsAuth",
    # Token exchange
    "exchange_id_token",
    # Token
    "Token",
    "OAuthError",
    # Token cache
    "TokenCache",
    "MemoryTokenCache",
    # Exceptions
    "OpenFaaSError",
    "APIConnectionError",
    "APIStatusError",
    "NotFoundError",
    "UnauthorizedError",
    "ForbiddenError",
    "UnexpectedStatusError",
    # Models
    "FunctionDeployment",
    "FunctionNamespace",
    "FunctionResources",
    "FunctionStatus",
    "FunctionUsage",
    "LogMessage",
    "Provider",
    "Secret",
    "SystemInfo",
    "VersionInfo",
]
