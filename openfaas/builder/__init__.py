"""
OpenFaaS Function Builder subpackage.

Provides a client and helpers for interacting with the OpenFaaS Pro Function
Builder API.

Quickstart::

    from openfaas.builder import FunctionBuilder, BuildConfig, make_tar

    config = BuildConfig(
        image="ttl.sh/hello-world:1h",
        platforms=["linux/amd64"],
    )
    make_tar("/tmp/req.tar", "./build/hello-world", config)

    builder = FunctionBuilder("http://127.0.0.1:8081", hmac_secret="s3cr3t")

    # Non-streaming — wait for the final result.
    result = builder.build("/tmp/req.tar")
    print(result.status, result.image)

    # Streaming — receive log lines as they arrive.
    for result in builder.build_stream("/tmp/req.tar"):
        for line in result.log:
            print(line)
"""

from openfaas.builder.client import FunctionBuilder
from openfaas.builder.models import (
    BUILDER_CONFIG_FILE_NAME,
    BUILD_FAILED,
    BUILD_IN_PROGRESS,
    BUILD_SUCCESS,
    BuildConfig,
    BuildResult,
)
from openfaas.builder.tar import create_build_context, make_tar

__all__ = [
    # Client
    "FunctionBuilder",
    # Models
    "BuildConfig",
    "BuildResult",
    # Constants
    "BUILDER_CONFIG_FILE_NAME",
    "BUILD_IN_PROGRESS",
    "BUILD_SUCCESS",
    "BUILD_FAILED",
    # Tar helpers
    "make_tar",
    "create_build_context",
]
