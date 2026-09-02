from __future__ import annotations

from pathlib import Path
from typing import Literal

AnalysisLanguage = Literal["c_cpp", "lua"]

C_CPP_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
LUA_SUFFIXES = {".lua"}
IGNORED_PARTS = {
    ".git",
    ".pangea",
    "build",
    "dist",
    "third_party",
    "node_modules",
    "__pycache__",
}


def detect_analysis_language(
    repositories: list[dict], requested_scopes: list[str]
) -> AnalysisLanguage:
    found: set[AnalysisLanguage] = set()
    for repository in repositories:
        root = Path(repository["source_root"]).resolve()
        for scope in requested_scopes or ["."]:
            scoped_root = (root / _normalize(scope)).resolve()
            _ensure_inside_repository(scoped_root, root)
            if not scoped_root.exists():
                continue
            candidates = [scoped_root] if scoped_root.is_file() else scoped_root.rglob("*")
            for path in candidates:
                _ensure_inside_repository(path.resolve(), root)
                if not path.is_file() or _ignored(path, root):
                    continue
                suffix = path.suffix.lower()
                if suffix in C_CPP_SUFFIXES:
                    found.add("c_cpp")
                elif suffix in LUA_SUFFIXES:
                    found.add("lua")
                if len(found) > 1:
                    raise ValueError(
                        "同一分析模块同时包含 Lua 与 C/C++ 源码，第一阶段暂不支持混合语言模块"
                    )
    if not found:
        raise ValueError("用户指定范围没有可分析的 C/C++ 或 Lua 源码")
    return next(iter(found))


def _normalize(value: str) -> str:
    return value.replace("\\", "/").strip("/") or "."


def _ignored(path: Path, root: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.relative_to(root).parts)


def _ensure_inside_repository(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"源码范围越过仓库边界：{path}") from exc
