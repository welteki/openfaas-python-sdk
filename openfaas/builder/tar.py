"""
Tar archive helpers for the OpenFaaS Function Builder.

:func:`make_tar` packs an on-disk build context into a tar file ready to POST
to the builder API.

:func:`create_build_context` assembles that on-disk context from a template
directory and a function handler directory.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
from pathlib import Path

from openfaas.builder.models import BUILDER_CONFIG_FILE_NAME, BuildConfig


def make_tar(tar_path: str, context: str, build_config: BuildConfig) -> None:
    """Create a tar archive at *tar_path* containing the build context.

    The archive contains:

    * The ``context`` directory tree, rooted as ``context/`` inside the tar.
    * The JSON-serialised *build_config* as ``com.openfaas.docker.config`` at
      the tar root.

    Args:
        tar_path: Destination path for the ``.tar`` file.
        context: Path to the build context directory on disk.
        build_config: Build configuration to embed in the archive.
    """
    config_bytes = json.dumps(build_config.to_dict()).encode()

    with tarfile.open(tar_path, "w") as tar:
        # Add the build config file.
        import io

        info = tarfile.TarInfo(name=BUILDER_CONFIG_FILE_NAME)
        info.size = len(config_bytes)
        info.mode = 0o664
        tar.addfile(info, io.BytesIO(config_bytes))

        # Add the context directory, rooted as "context/" in the archive.
        context_path = Path(context).resolve()
        tar.add(str(context_path), arcname="context")


def create_build_context(
    function_name: str,
    handler: str,
    language: str,
    copy_extra_paths: list[str] | None = None,
    *,
    build_dir: str = "./build",
    template_dir: str = "./template",
    handler_overlay: str = "function",
) -> str:
    """Prepare a Docker build context on disk and return its path.

    The context is assembled as follows:

    1. ``<build_dir>/<function_name>`` is cleared and re-created.
    2. For non-``dockerfile`` languages the template from
       ``<template_dir>/<language>`` is copied into the context.
    3. The function *handler* directory is overlaid onto
       ``<context>/<handler_overlay>/``, skipping any ``build/`` or
       ``template/`` sub-directories inside the handler.
    4. Any paths listed in *copy_extra_paths* are copied into the context root.
       Each path must be relative and resolve within the current directory.

    Args:
        function_name: Name used for the build context subdirectory.
        handler: Path to the function handler directory.
        language: Template language, e.g. ``"node20"``.  Pass
            ``"dockerfile"`` to skip template copying.
        copy_extra_paths: Additional paths to copy into the context root.
        build_dir: Root directory for build contexts.  Defaults to
            ``"./build"``.
        template_dir: Directory containing language templates.  Defaults to
            ``"./template"``.
        handler_overlay: Sub-path within the context where the handler is
            placed.  Defaults to ``"function"``.

    Returns:
        Absolute path to the assembled build context directory.

    Raises:
        FileNotFoundError: If the template directory for *language* does not
            exist (and *language* is not ``"dockerfile"``).
        ValueError: If *function_name* resolves outside *build_dir*, if
            *language* resolves outside *template_dir*, if *handler_overlay*
            resolves outside the context directory, or if a path in
            *copy_extra_paths* resolves outside the current working directory.
    """
    abs_build_dir = str(Path(build_dir).resolve())
    context_path = Path(build_dir) / function_name
    context_path = context_path.resolve()
    try:
        _path_in_scope(str(context_path), abs_build_dir)
    except ValueError:
        raise ValueError(
            f"function_name must not contain path separators or traversal sequences: {function_name!r}"
        ) from None

    # Clear and re-create the context directory.
    if context_path.exists():
        shutil.rmtree(context_path)
    context_path.mkdir(parents=True, exist_ok=True)

    # Copy the template into the context (skip for raw dockerfile builds).
    if language.lower() != "dockerfile":
        template_src = Path(template_dir) / language
        abs_template_dir = str(Path(template_dir).resolve())
        try:
            _path_in_scope(str(template_src.resolve()), abs_template_dir)
        except ValueError:
            raise ValueError(
                f"language must not contain path separators or traversal sequences: {language!r}"
            ) from None
        if not template_src.exists():
            raise FileNotFoundError(f"Template directory not found: {template_src}")
        _copy_tree(str(template_src), str(context_path))

    # Overlay the handler directory, skipping build/ and template/ subdirs.
    overlay_dest = context_path / handler_overlay
    try:
        _path_in_scope(str(overlay_dest.resolve()), str(context_path))
    except ValueError:
        raise ValueError(
            f"handler_overlay must not contain path separators or traversal sequences: {handler_overlay!r}"
        ) from None
    overlay_dest.mkdir(parents=True, exist_ok=True)
    handler_path = Path(handler).resolve()
    _copy_handler(str(handler_path), str(overlay_dest))

    # Copy any extra paths into the context root.
    for extra in copy_extra_paths or []:
        abs_extra = _path_in_scope(extra, str(Path(".").resolve()))
        dest = context_path / Path(extra).name
        if Path(abs_extra).is_dir():
            _copy_tree(abs_extra, str(dest))
        else:
            shutil.copy2(abs_extra, str(dest))

    return str(context_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _copy_tree(src: str, dst: str) -> None:
    """Recursively copy *src* directory into *dst*, creating *dst* if needed."""
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.mkdir(parents=True, exist_ok=True)
    for item in src_path.iterdir():
        s = src_path / item.name
        d = dst_path / item.name
        if s.is_dir():
            _copy_tree(str(s), str(d))
        else:
            shutil.copy2(str(s), str(d))


def _copy_handler(src: str, dst: str) -> None:
    """Copy handler directory into *dst*, skipping ``build/`` and ``template/``
    subdirectories."""
    _SKIP = {"build", "template"}
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.mkdir(parents=True, exist_ok=True)
    for item in src_path.iterdir():
        if item.is_dir() and item.name in _SKIP:
            continue
        s = src_path / item.name
        d = dst_path / item.name
        if s.is_dir():
            _copy_tree(str(s), str(d))
        else:
            shutil.copy2(str(s), str(d))


def _path_in_scope(path: str, scope: str) -> str:
    """Return the absolute path for *path* and verify it is within *scope*.

    Raises:
        ValueError: If the resolved path equals *scope* or falls outside it.
    """
    abs_path = str(Path(path).resolve())
    abs_scope = str(Path(scope).resolve())

    if abs_path == abs_scope:
        raise ValueError(f"Path must not be the scope root itself: {path!r}")
    if not abs_path.startswith(abs_scope + os.sep):
        raise ValueError(f"Path {path!r} resolves outside the allowed scope {scope!r}")
    return abs_path
