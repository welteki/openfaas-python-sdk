"""
Data models for the OpenFaaS Function Builder API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Name of the build config file embedded in the tar archive.
BUILDER_CONFIG_FILE_NAME = "com.openfaas.docker.config"

# Build status constants returned by the builder API.
BUILD_IN_PROGRESS = "in_progress"
BUILD_SUCCESS = "success"
BUILD_FAILED = "failed"


@dataclass
class BuildConfig:
    """Configuration for a function build, serialised into the tar archive as
    ``com.openfaas.docker.config``.

    Args:
        image: Fully-qualified Docker image name to build and push, e.g.
            ``ttl.sh/hello-world:1h``.
        build_args: Optional Docker build arguments passed to the builder.
        platforms: Target platforms, e.g. ``["linux/amd64", "linux/arm64"]``.
            Leave empty to use the builder default.
        skip_push: When ``True`` the image is built but not pushed to the
            registry.
    """

    image: str
    build_args: dict[str, str] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=list)
    skip_push: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict using the wire field names expected
        by the builder API."""
        d: dict[str, object] = {"image": self.image}
        if self.build_args:
            d["buildArgs"] = self.build_args
        if self.platforms:
            d["platforms"] = self.platforms
        if self.skip_push:
            d["skipPush"] = self.skip_push
        return d


@dataclass
class BuildResult:
    """Result returned by the builder API, for both non-streaming and streaming
    responses.

    Args:
        log: Ordered list of log lines produced during the build.
        image: Fully-qualified image name that was built.
        status: One of :data:`BUILD_IN_PROGRESS`, :data:`BUILD_SUCCESS`, or
            :data:`BUILD_FAILED`.
        error: Human-readable error message, present only when the build
            failed.
    """

    log: list[str]
    image: str
    status: str
    error: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BuildResult:
        """Construct a :class:`BuildResult` from a parsed JSON dict."""
        return cls(
            log=list(data.get("log") or []),
            image=str(data.get("image") or ""),
            status=str(data.get("status") or ""),
            error=str(data.get("error") or ""),
        )
