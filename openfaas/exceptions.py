"""
Exceptions for the OpenFaaS Python SDK.

All exceptions inherit from OpenFaaSError, allowing callers to catch broadly
or narrowly as needed:

    try:
        client.get_function("my-fn", "openfaas-fn")
    except NotFoundError:
        ...
    except OpenFaaSError:
        ...
"""

from __future__ import annotations

import requests


class OpenFaaSError(Exception):
    """Base class for all OpenFaaS SDK exceptions."""


class APIConnectionError(OpenFaaSError):
    """Raised when the SDK cannot reach the OpenFaaS gateway."""

    def __init__(self, message: str = "Could not connect to the OpenFaaS gateway") -> None:
        super().__init__(message)


class APIStatusError(OpenFaaSError):
    """Raised when the gateway returns a non-successful HTTP status code."""

    status_code: int
    response: requests.Response

    def __init__(self, message: str, *, response: requests.Response) -> None:
        super().__init__(message)
        self.status_code = response.status_code
        self.response = response


class NotFoundError(APIStatusError):
    """Raised on HTTP 404 responses."""


class UnauthorizedError(APIStatusError):
    """Raised on HTTP 401 responses."""


class ForbiddenError(APIStatusError):
    """Raised on HTTP 403 responses."""


class UnexpectedStatusError(APIStatusError):
    """Raised when an unexpected HTTP status code is returned."""
