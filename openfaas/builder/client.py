"""
HTTP client for the OpenFaaS Function Builder API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator

import requests

from openfaas._transport import build_session
from openfaas.builder.models import BuildResult


class FunctionBuilder:
    """Client for the OpenFaaS Function Builder API.

    Sends a tar archive containing a build context and a ``BuildConfig`` to
    the builder's ``/build`` endpoint and returns one or more
    :class:`~openfaas.builder.models.BuildResult` objects.

    Args:
        url: Base URL of the builder, e.g. ``"http://127.0.0.1:8081"``.
        hmac_secret: Optional shared secret used to sign each request with an
            HMAC-SHA256 digest.  When provided the ``X-Build-Signature:
            sha256=<digest>`` header is added to every request.
        http_client: Optional pre-configured :class:`requests.Session`.
            Defaults to a session with the SDK ``User-Agent`` header set.

    Example::

        from openfaas.builder import FunctionBuilder, BuildConfig, make_tar

        config = BuildConfig(image="ttl.sh/hello-world:1h")
        make_tar("/tmp/req.tar", "./build/hello-world", config)

        builder = FunctionBuilder("http://127.0.0.1:8081", hmac_secret="s3cr3t")
        result = builder.build("/tmp/req.tar")
        print(result.status, result.image)
    """

    def __init__(
        self,
        url: str,
        *,
        hmac_secret: str | None = None,
        http_client: requests.Session | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._hmac_secret = hmac_secret
        self._http = http_client or build_session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, tar_path: str) -> BuildResult:
        """Send the tar archive to the builder and return the final result.

        The call blocks until the builder responds with a complete
        :class:`~openfaas.builder.models.BuildResult`.

        Args:
            tar_path: Path to the tar archive produced by
                :func:`~openfaas.builder.tar.make_tar`.

        Returns:
            A :class:`~openfaas.builder.models.BuildResult` describing the
            outcome of the build.

        Raises:
            requests.HTTPError: If the builder returns a non-2xx status code.
        """
        response = self._post(tar_path, stream=False)
        response.raise_for_status()
        return BuildResult.from_dict(response.json())

    def build_stream(self, tar_path: str) -> Iterator[BuildResult]:
        """Send the tar archive to the builder and stream build results.

        Yields :class:`~openfaas.builder.models.BuildResult` objects as
        NDJSON lines arrive from the builder.  The underlying connection is
        closed once the iterator is exhausted or if the caller breaks early.

        Args:
            tar_path: Path to the tar archive produced by
                :func:`~openfaas.builder.tar.make_tar`.

        Yields:
            :class:`~openfaas.builder.models.BuildResult` for each NDJSON
            line received from the builder.

        Raises:
            requests.HTTPError: If the builder returns a non-2xx status code.
        """
        response = self._post(tar_path, stream=True)
        response.raise_for_status()
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                yield BuildResult.from_dict(data)
        finally:
            response.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, tar_path: str, *, stream: bool) -> requests.Response:
        """Read the tar file, sign it if configured, and POST to ``/build``."""
        with open(tar_path, "rb") as fh:
            body = fh.read()

        headers: dict[str, str] = {
            "Content-Type": "application/octet-stream",
        }
        if stream:
            headers["Accept"] = "application/x-ndjson"
        if self._hmac_secret:
            digest = hmac.new(
                self._hmac_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Build-Signature"] = f"sha256={digest}"

        return self._http.post(
            f"{self._url}/build",
            data=body,
            headers=headers,
            stream=stream,
        )
