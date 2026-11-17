"""Windows extended paths at the I/O boundary; contracts keep ordinary paths.

Do not use extended paths as repository identities or persist them in manifests.
These helpers do not authorize paths: callers still enforce repository boundaries.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path


def _windows_path(value: str) -> str:
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        return value
    value = ntpath.abspath(value)
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def long_path(path: str | os.PathLike[str]) -> Path:
    """Return an absolute extended Windows path, or the unchanged POSIX path."""
    return Path(_windows_path(os.fspath(path))) if os.name == "nt" else Path(path)


def ordinary_path(path: str | os.PathLike[str]) -> Path:
    value = os.fspath(path)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def resolve_path(path: str | os.PathLike[str], *, strict: bool = False) -> Path:
    return ordinary_path(long_path(path).resolve(strict=strict))


def glob_paths(path: Path, pattern: str, *, recursive: bool = False):
    base = long_path(path)
    matches = base.rglob(pattern) if recursive else base.glob(pattern)
    for match in matches:
        yield path / match.relative_to(base)


def iterdir_paths(path: Path):
    for child in long_path(path).iterdir():
        yield path / child.name


def walk_paths(path: Path, **kwargs):
    base = long_path(path)
    for directory, directories, files in os.walk(base, **kwargs):
        # Preserve os.walk's mutable directory list (callers prune it in place).
        yield str(path / Path(directory).relative_to(base)), directories, files
