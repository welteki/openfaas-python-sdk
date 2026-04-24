"""
Tests for the openfaas.builder subpackage.

Covers:
- BuildConfig serialisation
- BuildResult deserialisation
- make_tar archive structure
- create_build_context directory assembly
- FunctionBuilder.build (non-streaming)
- FunctionBuilder.build_stream (NDJSON streaming)
- HMAC signing header
- Non-2xx error propagation
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tarfile
from pathlib import Path

import pytest
import requests
import requests_mock as req_mock

from openfaas.builder import (
    BUILD_FAILED,
    BUILD_IN_PROGRESS,
    BUILD_SUCCESS,
    BUILDER_CONFIG_FILE_NAME,
    BuildConfig,
    BuildResult,
    FunctionBuilder,
    create_build_context,
    make_tar,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BUILDER_URL = "http://builder.example.com"
_BUILD_URL = f"{_BUILDER_URL}/build"


@pytest.fixture()
def tmp(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def simple_context(tmp: Path) -> Path:
    """A minimal build context directory with one file."""
    ctx = tmp / "context"
    ctx.mkdir()
    (ctx / "handler.py").write_text("def handle(req): return req\n")
    return ctx


@pytest.fixture()
def tar_file(tmp: Path, simple_context: Path) -> Path:
    """A tar archive built from simple_context."""
    config = BuildConfig(image="ttl.sh/hello:1h")
    path = str(tmp / "req.tar")
    make_tar(path, str(simple_context), config)
    return Path(path)


# ---------------------------------------------------------------------------
# BuildConfig
# ---------------------------------------------------------------------------


class TestBuildConfig:
    def test_minimal_to_dict(self) -> None:
        cfg = BuildConfig(image="ttl.sh/hello:1h")
        d = cfg.to_dict()
        assert d == {"image": "ttl.sh/hello:1h"}

    def test_build_args_included(self) -> None:
        cfg = BuildConfig(image="img", build_args={"PY_VERSION": "3.12"})
        assert cfg.to_dict()["buildArgs"] == {"PY_VERSION": "3.12"}

    def test_empty_build_args_omitted(self) -> None:
        cfg = BuildConfig(image="img", build_args={})
        assert "buildArgs" not in cfg.to_dict()

    def test_platforms_included(self) -> None:
        cfg = BuildConfig(image="img", platforms=["linux/amd64", "linux/arm64"])
        assert cfg.to_dict()["platforms"] == ["linux/amd64", "linux/arm64"]

    def test_empty_platforms_omitted(self) -> None:
        cfg = BuildConfig(image="img")
        assert "platforms" not in cfg.to_dict()

    def test_skip_push_included_when_true(self) -> None:
        cfg = BuildConfig(image="img", skip_push=True)
        assert cfg.to_dict()["skipPush"] is True

    def test_skip_push_omitted_when_false(self) -> None:
        cfg = BuildConfig(image="img", skip_push=False)
        assert "skipPush" not in cfg.to_dict()


# ---------------------------------------------------------------------------
# BuildResult
# ---------------------------------------------------------------------------


class TestBuildResult:
    def test_from_dict_full(self) -> None:
        data = {
            "log": ["step 1", "step 2"],
            "image": "ttl.sh/hello:1h",
            "status": BUILD_SUCCESS,
            "error": "",
        }
        result = BuildResult.from_dict(data)
        assert result.log == ["step 1", "step 2"]
        assert result.image == "ttl.sh/hello:1h"
        assert result.status == BUILD_SUCCESS
        assert result.error == ""

    def test_from_dict_missing_optional_fields(self) -> None:
        result = BuildResult.from_dict({"status": BUILD_FAILED})
        assert result.log == []
        assert result.image == ""
        assert result.error == ""

    def test_from_dict_error_present(self) -> None:
        result = BuildResult.from_dict({"status": BUILD_FAILED, "error": "push failed"})
        assert result.error == "push failed"

    def test_status_constants(self) -> None:
        assert BUILD_IN_PROGRESS == "in_progress"
        assert BUILD_SUCCESS == "success"
        assert BUILD_FAILED == "failed"


# ---------------------------------------------------------------------------
# make_tar
# ---------------------------------------------------------------------------


class TestMakeTar:
    def test_config_file_present(self, tar_file: Path) -> None:
        with tarfile.open(str(tar_file)) as tar:
            names = tar.getnames()
        assert BUILDER_CONFIG_FILE_NAME in names

    def test_config_file_content(self, tar_file: Path, simple_context: Path) -> None:
        with tarfile.open(str(tar_file)) as tar:
            member = tar.extractfile(BUILDER_CONFIG_FILE_NAME)
            assert member is not None
            data = json.loads(member.read())
        assert data["image"] == "ttl.sh/hello:1h"

    def test_context_dir_present(self, tar_file: Path) -> None:
        with tarfile.open(str(tar_file)) as tar:
            names = tar.getnames()
        # context dir entries should be rooted under "context/"
        assert any(n.startswith("context") for n in names)

    def test_handler_file_in_context(self, tar_file: Path) -> None:
        with tarfile.open(str(tar_file)) as tar:
            names = tar.getnames()
        assert "context/handler.py" in names

    def test_all_build_args_serialised(self, tmp: Path, simple_context: Path) -> None:
        config = BuildConfig(
            image="img",
            build_args={"FOO": "bar"},
            platforms=["linux/amd64"],
            skip_push=True,
        )
        path = str(tmp / "full.tar")
        make_tar(path, str(simple_context), config)
        with tarfile.open(path) as tar:
            member = tar.extractfile(BUILDER_CONFIG_FILE_NAME)
            assert member is not None
            data = json.loads(member.read())
        assert data["buildArgs"] == {"FOO": "bar"}
        assert data["platforms"] == ["linux/amd64"]
        assert data["skipPush"] is True


# ---------------------------------------------------------------------------
# create_build_context
# ---------------------------------------------------------------------------


class TestCreateBuildContext:
    def _make_template(self, root: Path, lang: str) -> Path:
        tmpl = root / "template" / lang
        tmpl.mkdir(parents=True)
        (tmpl / "Dockerfile").write_text("FROM python:3.12\n")
        handler_dir = tmpl / "function"
        handler_dir.mkdir()
        (handler_dir / "requirements.txt").write_text("")
        return tmpl

    def _make_handler(self, root: Path) -> Path:
        handler = root / "my-handler"
        handler.mkdir()
        (handler / "handler.py").write_text("def handle(req): return req\n")
        return handler

    def test_context_created(self, tmp: Path) -> None:
        self._make_template(tmp, "python3")
        handler = self._make_handler(tmp)
        ctx = create_build_context(
            "my-fn",
            str(handler),
            "python3",
            build_dir=str(tmp / "build"),
            template_dir=str(tmp / "template"),
        )
        assert Path(ctx).is_dir()

    def test_template_files_copied(self, tmp: Path) -> None:
        self._make_template(tmp, "python3")
        handler = self._make_handler(tmp)
        ctx = create_build_context(
            "my-fn",
            str(handler),
            "python3",
            build_dir=str(tmp / "build"),
            template_dir=str(tmp / "template"),
        )
        assert (Path(ctx) / "Dockerfile").exists()

    def test_handler_overlaid(self, tmp: Path) -> None:
        self._make_template(tmp, "python3")
        handler = self._make_handler(tmp)
        ctx = create_build_context(
            "my-fn",
            str(handler),
            "python3",
            build_dir=str(tmp / "build"),
            template_dir=str(tmp / "template"),
        )
        assert (Path(ctx) / "function" / "handler.py").exists()

    def test_handler_build_subdir_skipped(self, tmp: Path) -> None:
        self._make_template(tmp, "python3")
        handler = self._make_handler(tmp)
        (handler / "build").mkdir()
        (handler / "build" / "artifact.bin").write_bytes(b"\x00")
        ctx = create_build_context(
            "my-fn",
            str(handler),
            "python3",
            build_dir=str(tmp / "build"),
            template_dir=str(tmp / "template"),
        )
        assert not (Path(ctx) / "function" / "build").exists()

    def test_handler_template_subdir_skipped(self, tmp: Path) -> None:
        self._make_template(tmp, "python3")
        handler = self._make_handler(tmp)
        (handler / "template").mkdir()
        (handler / "template" / "tmpl.yaml").write_text("lang: python3\n")
        ctx = create_build_context(
            "my-fn",
            str(handler),
            "python3",
            build_dir=str(tmp / "build"),
            template_dir=str(tmp / "template"),
        )
        assert not (Path(ctx) / "function" / "template").exists()

    def test_missing_template_raises(self, tmp: Path) -> None:
        handler = self._make_handler(tmp)
        with pytest.raises(FileNotFoundError, match="Template directory not found"):
            create_build_context(
                "my-fn",
                str(handler),
                "nonexistent",
                build_dir=str(tmp / "build"),
                template_dir=str(tmp / "template"),
            )

    def test_dockerfile_skips_template_copy(self, tmp: Path) -> None:
        handler = self._make_handler(tmp)
        (handler / "Dockerfile").write_text("FROM scratch\n")
        ctx = create_build_context(
            "my-fn",
            str(handler),
            "dockerfile",
            build_dir=str(tmp / "build"),
            template_dir=str(tmp / "template"),
        )
        # Only the handler overlay should be present — no template Dockerfile
        assert (Path(ctx) / "function" / "Dockerfile").exists()

    def test_traversal_in_function_name_raises(self, tmp: Path) -> None:
        handler = self._make_handler(tmp)
        with pytest.raises(ValueError, match="function_name must not contain path separators"):
            create_build_context(
                "../../../etc",
                str(handler),
                "dockerfile",
                build_dir=str(tmp / "build"),
                template_dir=str(tmp / "template"),
            )

    def test_traversal_in_language_raises(self, tmp: Path) -> None:
        handler = self._make_handler(tmp)
        with pytest.raises(ValueError, match="language must not contain path separators"):
            create_build_context(
                "my-fn",
                str(handler),
                "../../../etc",
                build_dir=str(tmp / "build"),
                template_dir=str(tmp / "template"),
            )

    def test_traversal_in_handler_overlay_raises(self, tmp: Path) -> None:
        self._make_template(tmp, "python3")
        handler = self._make_handler(tmp)
        with pytest.raises(ValueError, match="handler_overlay must not contain path separators"):
            create_build_context(
                "my-fn",
                str(handler),
                "python3",
                build_dir=str(tmp / "build"),
                template_dir=str(tmp / "template"),
                handler_overlay="../../../etc",
            )


# ---------------------------------------------------------------------------
# FunctionBuilder
# ---------------------------------------------------------------------------


class TestFunctionBuilder:
    _SUCCESS_BODY = json.dumps(
        {
            "log": ["Building...", "Done."],
            "image": "ttl.sh/hello:1h",
            "status": BUILD_SUCCESS,
        }
    )

    def test_build_success(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=self._SUCCESS_BODY, status_code=200)
            builder = FunctionBuilder(_BUILDER_URL)
            result = builder.build(str(tar_file))
        assert result.status == BUILD_SUCCESS
        assert result.image == "ttl.sh/hello:1h"
        assert result.log == ["Building...", "Done."]

    def test_build_202_accepted(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=self._SUCCESS_BODY, status_code=202)
            builder = FunctionBuilder(_BUILDER_URL)
            result = builder.build(str(tar_file))
        assert result.status == BUILD_SUCCESS

    def test_build_non_2xx_raises(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text="Unauthorized", status_code=401)
            builder = FunctionBuilder(_BUILDER_URL)
            with pytest.raises(requests.HTTPError):
                builder.build(str(tar_file))

    def test_build_sets_content_type(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=self._SUCCESS_BODY)
            builder = FunctionBuilder(_BUILDER_URL)
            builder.build(str(tar_file))
            assert m.last_request.headers["Content-Type"] == "application/octet-stream"

    def test_build_no_hmac_header_without_secret(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=self._SUCCESS_BODY)
            builder = FunctionBuilder(_BUILDER_URL)
            builder.build(str(tar_file))
            assert "X-Build-Signature" not in m.last_request.headers

    def test_build_hmac_header_present_with_secret(self, tar_file: Path) -> None:
        secret = "my-hmac-secret"
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=self._SUCCESS_BODY)
            builder = FunctionBuilder(_BUILDER_URL, hmac_secret=secret)
            builder.build(str(tar_file))
            sig = m.last_request.headers.get("X-Build-Signature", "")
        assert sig.startswith("sha256=")

    def test_build_hmac_header_correct_digest(self, tar_file: Path) -> None:
        secret = "my-hmac-secret"
        body = tar_file.read_bytes()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=self._SUCCESS_BODY)
            builder = FunctionBuilder(_BUILDER_URL, hmac_secret=secret)
            builder.build(str(tar_file))
            sig = m.last_request.headers["X-Build-Signature"]
        assert sig == f"sha256={expected}"

    def test_build_stream_yields_results(self, tar_file: Path) -> None:
        lines = [
            json.dumps({"log": ["step 1"], "image": "", "status": BUILD_IN_PROGRESS}),
            json.dumps({"log": ["step 2"], "image": "", "status": BUILD_IN_PROGRESS}),
            json.dumps({"log": [], "image": "ttl.sh/hello:1h", "status": BUILD_SUCCESS}),
        ]
        ndjson_body = "\n".join(lines)
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text=ndjson_body)
            builder = FunctionBuilder(_BUILDER_URL)
            results = list(builder.build_stream(str(tar_file)))
        assert len(results) == 3
        assert results[0].status == BUILD_IN_PROGRESS
        assert results[0].log == ["step 1"]
        assert results[2].status == BUILD_SUCCESS
        assert results[2].image == "ttl.sh/hello:1h"

    def test_build_stream_sets_accept_header(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text="")
            builder = FunctionBuilder(_BUILDER_URL)
            list(builder.build_stream(str(tar_file)))
            assert m.last_request.headers["Accept"] == "application/x-ndjson"

    def test_build_stream_non_2xx_raises(self, tar_file: Path) -> None:
        with req_mock.Mocker() as m:
            m.post(_BUILD_URL, text="Forbidden", status_code=403)
            builder = FunctionBuilder(_BUILDER_URL)
            with pytest.raises(requests.HTTPError):
                list(builder.build_stream(str(tar_file)))
