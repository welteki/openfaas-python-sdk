"""
OpenFaaS Python SDK — HTTP client.

Provides a synchronous client backed by :class:`requests.Session`:

* ``Client`` — synchronous, backed by ``requests.Session``

Example — sync with Basic auth::

    from openfaas import Client, BasicAuth

    client = Client(
        gateway_url="https://gateway.example.com",
        auth=BasicAuth("admin", "secret"),
    )
    functions = client.get_functions("openfaas-fn")

Example — sync with IAM auth (Kubernetes workload)::

    from openfaas import Client, TokenAuth, ServiceAccountTokenSource

    auth = TokenAuth(
        token_url="https://gateway.example.com/oauth/token",
        token_source=ServiceAccountTokenSource(),
    )
    client = Client("https://gateway.example.com", auth=auth)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import requests
import requests.auth

from openfaas._transport import build_session
from openfaas.auth import TokenSource
from openfaas.exceptions import (
    APIConnectionError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnexpectedStatusError,
)
from openfaas.exchange import exchange_id_token
from openfaas.models import (
    FunctionDeployment,
    FunctionNamespace,
    FunctionStatus,
    LogMessage,
    Secret,
    SystemInfo,
)
from openfaas.token_cache import TokenCache

# Label required by OpenFaaS Pro namespace management, injected on every
# deploy and update to indicate a namespace can be used by OpenFaaS.
_OPENFAAS_LABEL = "openfaas"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _raise_for_status(response: requests.Response) -> None:
    """Raise the appropriate SDK exception for non-2xx responses."""
    if response.ok:
        return
    body = response.text
    if response.status_code == 404:
        raise NotFoundError(f"Not found: {body}", response=response)
    if response.status_code == 401:
        raise UnauthorizedError(f"Unauthorized: {body}", response=response)
    if response.status_code == 403:
        raise ForbiddenError(f"Forbidden: {body}", response=response)
    raise UnexpectedStatusError(
        f"Unexpected status {response.status_code}: {body}",
        response=response,
    )


def _inject_openfaas_labels(spec: FunctionNamespace) -> dict[str, Any]:
    """Return the API dict for a namespace, ensuring the openfaas label is set."""
    data = spec.to_api_dict()
    data.setdefault("labels", {})[_OPENFAAS_LABEL] = "1"
    data.setdefault("annotations", {})[_OPENFAAS_LABEL] = "1"
    return data


def _parse_log_line(line: str) -> LogMessage | None:
    line = line.strip()
    if not line:
        return None
    try:
        return LogMessage.model_validate_json(line)
    except Exception:
        return None


def _fn_cache_key(name: str, namespace: str) -> str:
    return f"{name}.{namespace}"


class _BearerAuth(requests.auth.AuthBase):
    """Sets a static Bearer token on the Authorization header."""

    def __init__(self, token: str) -> None:
        self._token = token

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        r.headers["Authorization"] = f"Bearer {self._token}"
        return r


# ---------------------------------------------------------------------------
# Synchronous client
# ---------------------------------------------------------------------------


class Client:
    """Synchronous OpenFaaS gateway client.

    Args:
        gateway_url: Base URL of the OpenFaaS gateway, e.g.
            ``"https://gateway.example.com"``.
        auth: Authentication strategy.  Pass a :class:`~openfaas.BasicAuth`,
            :class:`~openfaas.TokenAuth`, or any :class:`requests.auth.AuthBase`
            subclass.
        timeout: Default request timeout in seconds.  Defaults to ``30``.
        function_token_source: Optional :class:`~openfaas.auth.TokenSource`
            used to obtain per-function scoped tokens for function invocation.
            When *auth* implements the :class:`~openfaas.auth.TokenSource`
            protocol (e.g. :class:`~openfaas.TokenAuth`), it is automatically
            used as the function token source if this is not set explicitly.
        token_cache: Optional :class:`~openfaas.token_cache.TokenCache` for
            caching per-function scoped tokens across invocations.
        http_client: Supply a pre-configured :class:`requests.Session` to
            override transport, proxies, or other low-level settings.
    """

    def __init__(
        self,
        gateway_url: str,
        auth: requests.auth.AuthBase | None = None,
        *,
        timeout: float = 30.0,
        function_token_source: TokenSource | None = None,
        token_cache: TokenCache | None = None,
        http_client: requests.Session | None = None,
    ) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._auth = auth
        self._timeout = timeout
        self._http = http_client or build_session(timeout=timeout)
        self._token_cache = token_cache

        # Auto-wire: if auth implements TokenSource (e.g. TokenAuth), use it
        # as the function token source when none is provided explicitly.
        if function_token_source is not None:
            self._function_token_source: TokenSource | None = function_token_source
        elif isinstance(auth, TokenSource):
            self._function_token_source = auth
        else:
            self._function_token_source = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._http.close()

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
        stream: bool = False,
    ) -> requests.Response:
        url = f"{self._gateway_url}{path}"
        try:
            return self._http.request(
                method,
                url,
                params=params,
                json=json,
                auth=self._auth,
                timeout=timeout if timeout is not None else self._timeout,
                stream=stream,
            )
        except requests.ConnectionError as exc:
            raise APIConnectionError() from exc
        except requests.Timeout as exc:
            raise APIConnectionError("Request to the OpenFaaS gateway timed out") from exc

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def get_info(self) -> SystemInfo:
        """Return gateway system information."""
        response = self._request("GET", "/system/info")
        _raise_for_status(response)
        return SystemInfo.model_validate(response.json())

    # ------------------------------------------------------------------
    # Namespaces
    # ------------------------------------------------------------------

    def get_namespaces(self) -> list[str]:
        """Return all namespaces available on the gateway."""
        response = self._request("GET", "/system/namespaces")
        _raise_for_status(response)
        return response.json()

    def get_namespace(self, namespace: str) -> FunctionNamespace:
        """Return details for a single namespace."""
        response = self._request("GET", f"/system/namespace/{namespace}")
        _raise_for_status(response)
        return FunctionNamespace.model_validate(response.json())

    def create_namespace(self, spec: FunctionNamespace) -> int:
        """Create a namespace.  Returns the HTTP status code."""
        response = self._request("POST", "/system/namespace/", json=_inject_openfaas_labels(spec))
        _raise_for_status(response)
        return response.status_code

    def update_namespace(self, spec: FunctionNamespace) -> int:
        """Update an existing namespace.  Returns the HTTP status code."""
        response = self._request("PUT", f"/system/namespace/{spec.name}", json=_inject_openfaas_labels(spec))
        _raise_for_status(response)
        return response.status_code

    def delete_namespace(self, namespace: str) -> None:
        """Delete a namespace."""
        body = FunctionNamespace(name=namespace)
        response = self._request("DELETE", f"/system/namespace/{namespace}", json=_inject_openfaas_labels(body))
        _raise_for_status(response)

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------

    def get_functions(self, namespace: str | None = None) -> list[FunctionStatus]:
        """Return all functions, optionally filtered by namespace."""
        params = {"namespace": namespace} if namespace else None
        response = self._request("GET", "/system/functions", params=params)
        _raise_for_status(response)
        return [FunctionStatus.model_validate(f) for f in response.json()]

    def get_function(self, name: str, namespace: str | None = None) -> FunctionStatus:
        """Return details for a single function."""
        params = {"namespace": namespace} if namespace else None
        response = self._request("GET", f"/system/function/{name}", params=params)
        _raise_for_status(response)
        return FunctionStatus.model_validate(response.json())

    def deploy(self, spec: FunctionDeployment) -> int:
        """Deploy a new function.  Returns the HTTP status code."""
        response = self._request("POST", "/system/functions", json=spec.to_api_dict())
        _raise_for_status(response)
        return response.status_code

    def update(self, spec: FunctionDeployment) -> int:
        """Update an existing function.  Returns the HTTP status code."""
        response = self._request("PUT", "/system/functions", json=spec.to_api_dict())
        _raise_for_status(response)
        return response.status_code

    def delete_function(self, name: str, namespace: str | None = None) -> None:
        """Delete a function."""
        body: dict[str, Any] = {"functionName": name}
        if namespace:
            body["namespace"] = namespace
        response = self._request("DELETE", "/system/functions", json=body)
        _raise_for_status(response)

    def scale_function(self, name: str, replicas: int, namespace: str | None = None) -> None:
        """Scale a function to the specified number of replicas."""
        body: dict[str, Any] = {"serviceName": name, "replicas": replicas}
        if namespace:
            body["namespace"] = namespace
        response = self._request("POST", f"/system/scale-function/{name}", json=body)
        _raise_for_status(response)

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------

    def get_secrets(self, namespace: str | None = None) -> list[Secret]:
        """Return all secrets, optionally filtered by namespace."""
        params = {"namespace": namespace} if namespace else None
        response = self._request("GET", "/system/secrets", params=params)
        _raise_for_status(response)
        return [Secret.model_validate(s) for s in response.json()]

    def create_secret(self, spec: Secret) -> int:
        """Create a secret.  Returns the HTTP status code."""
        response = self._request("POST", "/system/secrets", json=spec.to_api_dict())
        _raise_for_status(response)
        return response.status_code

    def update_secret(self, spec: Secret) -> int:
        """Update an existing secret.  Returns the HTTP status code."""
        response = self._request("PUT", "/system/secrets", json=spec.to_api_dict())
        _raise_for_status(response)
        return response.status_code

    def delete_secret(self, name: str, namespace: str | None = None) -> None:
        """Delete a secret."""
        body: dict[str, Any] = {"name": name}
        if namespace:
            body["namespace"] = namespace
        response = self._request("DELETE", "/system/secrets", json=body)
        _raise_for_status(response)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def get_logs(
        self,
        name: str,
        namespace: str | None = None,
        *,
        tail: int | None = None,
        follow: bool = False,
        since: datetime | None = None,
    ) -> Iterator[LogMessage]:
        """Stream log messages for a function.

        Yields :class:`~openfaas.LogMessage` instances parsed from the
        NDJSON response.  The iterator blocks until the server closes the
        connection (``follow=False``) or the caller breaks out of the loop.

        Args:
            name: Function name.
            namespace: Function namespace.
            tail: Maximum number of recent log lines to return.
            follow: If ``True``, keep the connection open and stream new log
                lines as they arrive.
            since: Return only log lines after this timestamp.
        """
        params: dict[str, Any] = {"name": name}
        if namespace:
            params["namespace"] = namespace
        if tail is not None:
            params["tail"] = tail
        params["follow"] = "1" if follow else "0"
        if since is not None:
            params["since"] = since.isoformat()

        response = self._request("GET", "/system/logs", params=params, stream=True)
        _raise_for_status(response)
        try:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode()
                msg = _parse_log_line(line)
                if msg is not None:
                    yield msg
        finally:
            response.close()

    # ------------------------------------------------------------------
    # Function invocation
    # ------------------------------------------------------------------

    def invoke_function(
        self,
        name: str,
        namespace: str = "openfaas-fn",
        *,
        method: str,
        payload: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        use_function_auth: bool = False,
    ) -> requests.Response:
        """Invoke a deployed function and return the raw response.

        No exception is raised for non-2xx responses — function responses are
        application-level and the caller decides how to interpret them.

        Args:
            name: Function name.
            namespace: Function namespace.  Defaults to ``"openfaas-fn"``.
            method: HTTP method, e.g. ``"GET"`` or ``"POST"``.
            payload: Request body.  Accepts :class:`bytes` or :class:`str`
                (UTF-8 encoded automatically).
            headers: Additional request headers merged with any auth headers.
            query_params: Query string parameters.
            use_function_auth: If ``True``, obtain a per-function scoped token
                via :meth:`get_function_token` and attach it as
                ``Authorization: Bearer <token>``, overriding any gateway-level
                auth header.  Requires a ``function_token_source`` to be
                configured on the client.

        Returns:
            The raw :class:`requests.Response` from the function.

        Raises:
            :exc:`~openfaas.APIConnectionError`: On network or timeout errors.
        """
        url = f"{self._gateway_url}/function/{name}.{namespace}"
        return self._invoke(
            method=method,
            url=url,
            name=name,
            namespace=namespace,
            payload=payload,
            headers=headers,
            query_params=query_params,
            use_function_auth=use_function_auth,
        )

    def invoke_function_async(
        self,
        name: str,
        namespace: str = "openfaas-fn",
        *,
        payload: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        callback_url: str | None = None,
        use_function_auth: bool = False,
    ) -> requests.Response:
        """Queue a function invocation and return the gateway's 202 response.

        The gateway queues the invocation and responds immediately with
        ``202 Accepted``.  The function result is not returned synchronously.

        Args:
            name: Function name.
            namespace: Function namespace.  Defaults to ``"openfaas-fn"``.
            payload: Request body.  Accepts :class:`bytes` or :class:`str`
                (UTF-8 encoded automatically).
            headers: Additional request headers merged with any auth headers.
            query_params: Query string parameters.
            callback_url: If provided, the gateway will ``POST`` the function
                result to this URL once the invocation completes.
            use_function_auth: If ``True``, obtain a per-function scoped token
                via :meth:`get_function_token` and attach it as
                ``Authorization: Bearer <token>``, overriding any gateway-level
                auth header.  Requires a ``function_token_source`` to be
                configured on the client.

        Returns:
            The ``202 Accepted`` :class:`requests.Response` from the gateway.

        Raises:
            :exc:`~openfaas.APIConnectionError`: On network or timeout errors.
        """
        url = f"{self._gateway_url}/async-function/{name}.{namespace}"
        merged_headers: dict[str, str] = dict(headers) if headers else {}
        if callback_url is not None:
            merged_headers["X-Callback-Url"] = callback_url
        return self._invoke(
            method="POST",
            url=url,
            name=name,
            namespace=namespace,
            payload=payload,
            headers=merged_headers,
            query_params=query_params,
            use_function_auth=use_function_auth,
        )

    def _invoke(
        self,
        *,
        method: str,
        url: str,
        name: str,
        namespace: str,
        payload: bytes | str | None,
        headers: dict[str, str] | None,
        query_params: dict[str, str] | None,
        use_function_auth: bool,
    ) -> requests.Response:
        merged_headers: dict[str, str] = dict(headers) if headers else {}

        data: bytes | None = None
        if payload is not None:
            data = payload.encode() if isinstance(payload, str) else payload

        if use_function_auth:
            fn_token = self.get_function_token(name, namespace)
            auth: requests.auth.AuthBase | None = _BearerAuth(fn_token)
        else:
            auth = None

        try:
            return self._http.request(
                method,
                url,
                data=data,
                headers=merged_headers,
                params=query_params,
                auth=auth,
                timeout=self._timeout,
            )
        except requests.ConnectionError as exc:
            raise APIConnectionError() from exc
        except requests.Timeout as exc:
            raise APIConnectionError("Request to the OpenFaaS gateway timed out") from exc

    # ------------------------------------------------------------------
    # Function token exchange (for IAM-protected function invocation)
    # ------------------------------------------------------------------

    def get_function_token(self, name: str, namespace: str) -> str:
        """Return a scoped access token for invoking a specific function.

        Exchanges the client's own identity token (from
        ``function_token_source``) for a token scoped to the given function.
        The result is cached in ``token_cache`` if one was provided.

        This is called automatically by ``invoke_function`` when IAM auth is
        enabled, but can also be called directly.

        Args:
            name:      Function name.
            namespace: Function namespace.

        Returns:
            A raw JWT string scoped to the target function.

        Raises:
            :exc:`RuntimeError`: If no ``function_token_source`` is configured.
        """
        if self._function_token_source is None:
            raise RuntimeError(
                "No function_token_source configured. "
                "Pass a TokenAuth as auth, or set function_token_source explicitly."
            )

        cache_key = _fn_cache_key(name, namespace)
        if self._token_cache is not None:
            cached = self._token_cache.get(cache_key)
            if cached is not None:
                return cached.id_token

        id_token = self._function_token_source.token()
        token_url = f"{self._gateway_url}/oauth/token"
        token = exchange_id_token(
            token_url,
            id_token,
            scope=["function"],
            audience=[f"{namespace}:{name}"],
            http_client=self._http,
        )

        if self._token_cache is not None:
            self._token_cache.set(cache_key, token)

        return token.id_token
