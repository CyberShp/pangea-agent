from __future__ import annotations

from pathlib import Path
from typing import Literal


C_SUFFIXES = {".c", ".h"}
CPP_SUFFIXES = {".cc", ".cpp", ".cxx", ".hpp", ".hh"}
C_CPP_SUFFIXES = C_SUFFIXES | CPP_SUFFIXES
C_CPP_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
C_CPP_HEADER_SUFFIXES = {".h", ".hpp", ".hh"}
LUA_SUFFIXES = {".lua"}
CODE_SUFFIXES = C_CPP_SUFFIXES | LUA_SUFFIXES

SourceLanguage = Literal["c", "cpp", "lua"]


def language_for_path(path: Path) -> SourceLanguage | None:
    suffix = path.suffix.lower()
    if suffix in LUA_SUFFIXES:
        return "lua"
    if suffix in CPP_SUFFIXES:
        return "cpp"
    if suffix in C_SUFFIXES:
        return "c"
    return None
