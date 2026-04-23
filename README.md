# OpenFaaS Python SDK

The official Python SDK for [OpenFaaS](https://www.openfaas.com).

## Features

- Full coverage of the OpenFaaS REST API — functions, namespaces, secrets, logs, system info, and function invocation
- Synchronous `Client` backed by `requests`
- Multiple auth strategies: Basic auth, OpenFaaS IAM (token exchange), OAuth2 client credentials
- Pydantic v2 models for all request and response types — validated, typed, IDE-friendly
- Streaming log support via iterators
- `FunctionBuilder` client for the [OpenFaaS Pro Function Builder API](https://docs.openfaas.com/openfaas-pro/builder/) — build and push function images from source
- `FAAS_DEBUG=1` environment variable for request/response logging (auth headers redacted)
- Context manager support for automatic connection cleanup

## Requirements

- Python 3.10+
- [requests](https://requests.readthedocs.io) >= 2.20
- [pydantic](https://docs.pydantic.dev) >= 2.0

## Installation

```bash
pip install openfaas-sdk
```

## Quick start

```python
from openfaas import Client, BasicAuth

client = Client(
    gateway_url="https://gateway.example.com",
    auth=BasicAuth("admin", "secret"),
)

functions = client.get_functions("openfaas-fn")
for fn in functions:
    print(fn.name, fn.replicas)

client.close()
```

Use the client as a context manager to ensure connections are closed:

```python
from openfaas import Client, BasicAuth

with Client("https://gateway.example.com", auth=BasicAuth("admin", "secret")) as client:
    functions = client.get_functions("openfaas-fn")
```

## Authentication

### Basic auth

```python
from openfaas import BasicAuth

auth = BasicAuth(username="admin", password="secret")
```

The password can be read from a file:

```python
from openfaas import BasicAuth

with open("/var/secrets/basic-auth-password") as f:
    password = f.read().strip()

auth = BasicAuth(username="admin", password=password)
```

### Custom auth

Subclass `requests.auth.AuthBase` directly to implement your own strategy:

```python
import requests.auth

class MyTokenAuth(requests.auth.AuthBase):
    def __init__(self, token: str) -> None:
        self._token = token

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        r.headers["Authorization"] = f"Bearer {self._token}"
        return r
```

### OpenFaaS IAM — Kubernetes workload identity

When running inside a Kubernetes cluster with [OpenFaaS IAM](https://docs.openfaas.com/openfaas-pro/iam/overview/) enabled, use `TokenAuth` with `ServiceAccountTokenSource` to exchange the pod's projected service account token for an OpenFaaS gateway JWT automatically:

```python
from openfaas import Client, TokenAuth, ServiceAccountTokenSource

auth = TokenAuth(
    token_url="https://gateway.example.com/oauth/token",
    token_source=ServiceAccountTokenSource(),
)

with Client("https://gateway.example.com", auth=auth) as client:
    functions = client.get_functions("openfaas-fn")
```

`ServiceAccountTokenSource` re-reads `/var/secrets/tokens/openfaas-token` on every call so that Kubernetes token rotation is handled transparently. The path can be overridden with the `token_mount_path` environment variable.

`TokenAuth` caches the exchanged gateway token and refreshes it automatically when it expires (10-second expiry buffer).

`TokenAuth` also implements the `TokenSource` protocol, so it is automatically used as the `function_token_source` for per-function scoped token exchange when calling `get_function_token()`.

### OpenFaaS IAM — external IdP via client credentials

For workloads outside Kubernetes, use `ClientCredentialsTokenSource` to obtain tokens from an external IdP and exchange them for an OpenFaaS gateway JWT:

```python
from openfaas import Client, TokenAuth, ClientCredentialsTokenSource

ts = ClientCredentialsTokenSource(
    client_id="my-app",
    client_secret="secret",
    token_url="https://idp.example.com/realms/master/protocol/openid-connect/token",
    scope="openid",
)
auth = TokenAuth(
    token_url="https://gateway.example.com/oauth/token",
    token_source=ts,
)

with Client("https://gateway.example.com", auth=auth) as client:
    functions = client.get_functions("openfaas-fn")
```

### Per-function scoped tokens

`get_function_token()` exchanges the current identity token for a short-lived token scoped to a specific function (audience `"<namespace>:<function-name>"`). Use this token when invoking functions directly:

```python
token = client.get_function_token("my-func", "openfaas-fn")
# token is a raw JWT string — pass it as a Bearer token when invoking the function
```

## API reference

### System

```python
info = client.get_info()
# info.arch, info.provider.orchestration, info.version.release
```

### Functions

```python
# List all functions in a namespace
functions = client.get_functions("openfaas-fn")

# Get a single function
fn = client.get_function("env", "openfaas-fn")
# fn.name, fn.replicas, fn.available_replicas, fn.invocation_count

# Deploy a new function
from openfaas import FunctionDeployment, FunctionResources

spec = FunctionDeployment(
    service="env",
    image="ghcr.io/openfaas/env:latest",
    namespace="openfaas-fn",
    labels={"com.openfaas.scale.min": "1"},
    limits=FunctionResources(memory="128Mi", cpu="100m"),
)
client.deploy(spec)

# Update an existing function
spec.image = "ghcr.io/openfaas/env:0.2.0"
client.update(spec)

# Scale a function
client.scale_function("env", replicas=3, namespace="openfaas-fn")

# Delete a function
client.delete_function("env", "openfaas-fn")
```

### Namespaces

```python
# List all namespaces
namespaces = client.get_namespaces()  # ["openfaas-fn", "staging"]

# Get namespace details
ns = client.get_namespace("openfaas-fn")

# Create a namespace
from openfaas import FunctionNamespace

client.create_namespace(FunctionNamespace(name="staging", labels={"team": "backend"}))

# Update a namespace
client.update_namespace(FunctionNamespace(name="staging", annotations={"owner": "alice"}))

# Delete a namespace
client.delete_namespace("staging")
```

### Secrets

```python
from openfaas import Secret

# List secrets
secrets = client.get_secrets("openfaas-fn")

# Create a secret
client.create_secret(Secret(name="db-password", namespace="openfaas-fn", value="s3cr3t"))

# Update a secret
client.update_secret(Secret(name="db-password", namespace="openfaas-fn", value="n3w-s3cr3t"))

# Delete a secret
client.delete_secret("db-password", namespace="openfaas-fn")
```

### Logs

`get_logs` returns a lazy iterator that streams NDJSON log lines from the gateway.

```python
# Get the last 100 lines
for msg in client.get_logs("env", "openfaas-fn", tail=100):
    print(f"[{msg.timestamp}] {msg.instance}: {msg.text}")

# Follow (stream) logs
for msg in client.get_logs("env", "openfaas-fn", follow=True):
    print(msg.text)
```

Filter by time:

```python
from datetime import datetime, timezone

since = datetime(2024, 1, 1, tzinfo=timezone.utc)
for msg in client.get_logs("env", namespace="openfaas-fn", since=since):
    print(msg.text)
```

### Function invocation

`invoke_function` returns the raw `requests.Response` from the function.  Non-2xx responses are **not** raised as exceptions — function responses are application-level and the caller decides how to interpret them.

```python
# Simple POST with a bytes or str payload
resp = client.invoke_function("env", payload=b"hello")
print(resp.status_code, resp.text)

# Custom method and namespace
resp = client.invoke_function("env", "staging", method="GET")

# Pass extra headers and query parameters
resp = client.invoke_function(
    "env",
    payload="hello",
    headers={"Content-Type": "text/plain"},
    query_params={"verbose": "1"},
)

# Async (queued) invocation — gateway responds 202 Accepted immediately
client.invoke_function("env", async_invoke=True)

# Async invocation with a callback URL
client.invoke_function(
    "env",
    payload=b"data",
    async_invoke=True,
    callback_url="https://my-service.example.com/callback",
)
```

#### IAM-scoped function invocation

When OpenFaaS IAM is enabled, use `use_function_auth=True` to automatically
obtain a per-function scoped token and attach it as a Bearer token.  This
requires the client to be configured with a `TokenAuth` (or any
`function_token_source`):

```python
from openfaas import Client, TokenAuth, ServiceAccountTokenSource

auth = TokenAuth(
    token_url="https://gateway.example.com/oauth/token",
    token_source=ServiceAccountTokenSource(),
)

with Client("https://gateway.example.com", auth=auth) as client:
    resp = client.invoke_function("my-func", "openfaas-fn", use_function_auth=True)
    print(resp.text)
```

## Error handling

```python
from openfaas import Client, BasicAuth
from openfaas.exceptions import NotFoundError, UnauthorizedError, ForbiddenError, APIConnectionError

with Client("https://gateway.example.com", auth=BasicAuth("admin", "secret")) as client:
    try:
        fn = client.get_function("my-fn", "openfaas-fn")
    except NotFoundError:
        print("Function does not exist")
    except UnauthorizedError:
        print("Invalid credentials")
    except ForbiddenError:
        print("Insufficient permissions")
    except APIConnectionError:
        print("Could not reach the gateway")
```

| Exception | HTTP status |
|---|---|
| `NotFoundError` | 404 |
| `UnauthorizedError` | 401 |
| `ForbiddenError` | 403 |
| `UnexpectedStatusError` | any other non-2xx |
| `APIConnectionError` | network / timeout |

All `APIStatusError` subclasses expose `.status_code` and `.response` (the raw `requests.Response`).

## Configuration

### Timeout

```python
# Default timeout for all requests (seconds)
client = Client("https://gateway.example.com", auth=auth, timeout=60.0)
```

### Custom HTTP client

Pass a pre-configured `requests.Session` to customise proxies, SSL, or other transport options:

```python
import requests
from openfaas import Client

session = requests.Session()
session.verify = "/path/to/ca-bundle.pem"
session.proxies = {"https": "http://proxy.corp.example.com"}
client = Client("https://gateway.example.com", auth=auth, http_client=session)
```

### Debug logging

Set `FAAS_DEBUG=1` to log all requests and responses. The `Authorization` header is automatically redacted.

```bash
FAAS_DEBUG=1 python my_script.py
```

Configure the log level in your application to see the output:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Function Builder

The `FunctionBuilder` client interacts with the [OpenFaaS Pro Function Builder API](https://docs.openfaas.com/openfaas-pro/builder/) to build and push function images from source code.

### Assemble a build context

`create_build_context` prepares a Docker build context on disk from a template and a handler directory.  Templates can be pulled with `faas-cli template store pull <lang>` or fetched from any other source.

```python
from openfaas.builder import create_build_context, BuildConfig, make_tar, FunctionBuilder

# 1. Assemble the build context from template + handler
context_path = create_build_context(
    function_name="hello-world",
    handler="./hello-world",       # directory containing your function code
    language="python3",
    template_dir="./template",     # directory containing pulled templates
    build_dir="./build",
)

# 2. Pack the context into a tar archive
config = BuildConfig(
    image="ttl.sh/hello-world:1h",
    platforms=["linux/amd64"],
)
make_tar("/tmp/req.tar", context_path, config)
```

### Build without streaming

`build()` blocks until the builder returns a complete result:

```python
builder = FunctionBuilder(
    "http://127.0.0.1:8081",
    hmac_secret="my-hmac-secret",  # matches the payload-secret in the cluster
)

result = builder.build("/tmp/req.tar")
print(result.status)   # "success" / "failed"
print(result.image)    # fully-qualified image name
for line in result.log:
    print(line)
```

### Build with streaming

`build_stream()` yields `BuildResult` objects as NDJSON lines arrive, allowing you to display log output in real time:

```python
for result in builder.build_stream("/tmp/req.tar"):
    for line in result.log:
        print(line)
    if result.status in ("success", "failed"):
        print("Final status:", result.status)
```

### Skip push

Set `skip_push=True` on `BuildConfig` to build the image without pushing it to a registry:

```python
config = BuildConfig(image="ttl.sh/hello-world:1h", skip_push=True)
```

### HMAC request signing

When `hmac_secret` is provided, every request is signed with an HMAC-SHA256 digest sent in the `X-Build-Signature` header.  The secret must match the `payload-secret` configured in the builder deployment:

```bash
kubectl get secret -n openfaas payload-secret \
    -o jsonpath='{.data.payload-secret}' | base64 --decode
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run python -m pytest -v
```

## License

MIT
