"""
Token cache for the OpenFaaS Python SDK.

Provides a thread-safe in-memory cache for :class:`~openfaas.token.Token`
instances, used to avoid redundant token exchanges for per-function IAM tokens.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from openfaas.token import Token


class TokenCache(ABC):
    """Abstract base class for token caches."""

    @abstractmethod
    def get(self, key: str) -> Token | None:
        """Return the cached token for *key*, or ``None`` if absent or expired."""
        ...

    @abstractmethod
    def set(self, key: str, token: Token) -> None:
        """Store *token* under *key*."""
        ...


class MemoryTokenCache(TokenCache):
    """Thread-safe in-memory token cache.

    Expired tokens are evicted eagerly on :meth:`get`.

    Example::

        cache = MemoryTokenCache()
        client = Client(
            "https://gateway.example.com",
            auth=token_auth,
            token_cache=cache,
        )
    """

    def __init__(self) -> None:
        self._tokens: dict[str, Token] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Token | None:
        """Return the token for *key*, or ``None`` if missing or expired."""
        with self._lock:
            token = self._tokens.get(key)
            if token is None:
                return None
            if token.is_expired():
                del self._tokens[key]
                return None
            return token

    def set(self, key: str, token: Token) -> None:
        """Store *token* under *key*."""
        with self._lock:
            self._tokens[key] = token

    def clear_expired(self) -> None:
        """Remove all expired tokens from the cache."""
        with self._lock:
            expired_keys = [k for k, t in self._tokens.items() if t.is_expired()]
            for key in expired_keys:
                del self._tokens[key]
