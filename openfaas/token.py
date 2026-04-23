"""
Token types for the OpenFaaS Python SDK.

Represents an access token returned by the OpenFaaS IAM token exchange
endpoint, along with related error and parsing helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# Expire tokens 10 seconds early to account for clock skew.
_EXPIRY_DELTA = timedelta(seconds=10)


@dataclass
class Token:
    """An access token returned by the OpenFaaS gateway or an IdP.

    Attributes:
        id_token: The raw JWT / access token string.
        expiry:   When the token expires.  ``None`` means the token never
                  expires — the token is treated as permanently valid.
        scope:    List of scopes granted with this token.
    """

    id_token: str
    expiry: datetime | None = None
    scope: list[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        """Return ``True`` if the token has expired (or is about to within 10 s)."""
        if self.expiry is None:
            return False
        now = datetime.now(tz=timezone.utc)
        return now >= self.expiry - _EXPIRY_DELTA


class OAuthError(Exception):
    """Raised when an OAuth 2.0 token endpoint returns an error response.

    Attributes:
        error:             The ``error`` field from the JSON response body.
        error_description: The optional ``error_description`` field.
    """

    def __init__(self, error: str, error_description: str = "") -> None:
        self.error = error
        self.error_description = error_description
        if error_description:
            super().__init__(f"{error}: {error_description}")
        else:
            super().__init__(error)


def parse_token_response(data: dict[str, Any]) -> Token:
    """Parse a successful OAuth JSON token response into a :class:`Token`."""
    id_token: str = data.get("access_token", "")
    expires_in: int | None = data.get("expires_in")
    scope_str: str = data.get("scope", "")

    expiry: datetime | None = None
    if expires_in:
        expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)

    scope = scope_str.split() if scope_str else []

    return Token(id_token=id_token, expiry=expiry, scope=scope)
