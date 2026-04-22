"""
Token exchange for the OpenFaaS Python SDK.

Implements the OAuth 2.0 Token Exchange grant
(RFC 8693 / ``urn:ietf:params:oauth:grant-type:token-exchange``) against the
OpenFaaS gateway's ``/oauth/token`` endpoint.
"""

from __future__ import annotations

import logging
import os
import re

import requests

from openfaas.token import OAuthError, Token, _parse_token_response

logger = logging.getLogger("openfaas")

_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
_USER_AGENT = "openfaas-python-sdk"

_AUTH_REDACT_RE = re.compile(r"(Basic|Bearer)\s+\S+", re.IGNORECASE)


def _is_debug() -> bool:
    return os.environ.get("FAAS_DEBUG", "").strip() == "1"


def _redact_auth(value: str) -> str:
    return _AUTH_REDACT_RE.sub(r"\1 [REDACTED]", value)


def exchange_id_token(
    token_url: str,
    raw_id_token: str,
    *,
    audience: list[str] | None = None,
    scope: list[str] | None = None,
    http_client: requests.Session | None = None,
) -> Token:
    """Exchange an upstream identity token for an OpenFaaS gateway token.

    Performs the OAuth 2.0 Token Exchange grant against the OpenFaaS gateway
    ``/oauth/token`` endpoint.

    Args:
        token_url:    Full URL of the token endpoint,
                      e.g. ``"https://gateway.example.com/oauth/token"``.
        raw_id_token: The upstream JWT identity token to exchange.
        audience:     Optional list of audiences to request.  For function
                      invocation this should be
                      ``["<namespace>:<function-name>"]``.
        scope:        Optional list of scopes to request, e.g.
                      ``["function"]``.
        http_client:  Optional :class:`requests.Session` to use for the
                      request.  Defaults to a short-lived throwaway session.

    Returns:
        A :class:`~openfaas.token.Token` containing the OpenFaaS JWT.

    Raises:
        :class:`~openfaas.token.OAuthError`: When the endpoint returns HTTP 400
            with an OAuth error JSON body.
        :exc:`requests.HTTPError`: For other non-2xx responses.
    """
    # Build form data.  audience is repeated for each value; requests handles
    # lists correctly when passed via the ``data`` parameter as a list of
    # (key, value) tuples.
    data: list[tuple[str, str]] = [
        ("grant_type", _GRANT_TYPE),
        ("subject_token_type", _SUBJECT_TOKEN_TYPE),
        ("subject_token", raw_id_token),
    ]
    if audience:
        for aud in audience:
            data.append(("audience", aud))
    if scope:
        data.append(("scope", " ".join(scope)))

    headers = {"User-Agent": _USER_AGENT}

    if _is_debug():
        redacted = [(k, _redact_auth(v) if k == "subject_token" else v) for k, v in data]
        logger.debug("→ POST %s  data=%s", token_url, redacted)

    _owns_session = http_client is None
    session = http_client or requests.Session()

    try:
        response = session.post(token_url, data=data, headers=headers)

        if _is_debug():
            logger.debug("← %s %s", response.status_code, token_url)

        if response.status_code == 400:
            try:
                err = response.json()
                raise OAuthError(
                    err.get("error", "unknown_error"),
                    err.get("error_description", ""),
                )
            except (ValueError, KeyError):
                raise OAuthError("unknown_error", response.text)

        response.raise_for_status()
        return _parse_token_response(response.json())
    finally:
        if _owns_session:
            session.close()
