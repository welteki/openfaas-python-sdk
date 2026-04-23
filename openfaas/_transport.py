"""
Internal HTTP transport for the OpenFaaS Python SDK.

Responsibilities:
- Injects the ``User-Agent: openfaas-python-sdk/<version>`` header on every request.
- Logs request/response details when the ``FAAS_DEBUG`` environment variable is
  set to ``1`` (Authorization header is redacted).

This module is private — do not import it directly from user code.
"""

from __future__ import annotations

import logging
import os
import re

import requests

from openfaas._version import __version__

logger = logging.getLogger("openfaas")

_USER_AGENT = f"openfaas-python-sdk/{__version__}"
_AUTH_REDACT_RE = re.compile(r"(Basic|Bearer)\s+\S+", re.IGNORECASE)


def _redact_auth(value: str) -> str:
    return _AUTH_REDACT_RE.sub(r"\1 [REDACTED]", value)


def _is_debug() -> bool:
    return os.environ.get("FAAS_DEBUG", "").strip() == "1"


def _on_response(r: requests.Response, **_: object) -> None:
    """requests event hook: log responses when FAAS_DEBUG=1."""
    if _is_debug():
        logger.debug("← %s %s", r.status_code, r.url)


def build_session(timeout: float = 30.0) -> requests.Session:
    """Return a configured :class:`requests.Session` with the SDK User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    session.hooks["response"].append(_on_response)
    # Store default timeout on the session for use in _request()
    session._openfaas_timeout = timeout  # type: ignore[attr-defined]
    return session
