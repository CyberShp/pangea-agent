from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


C_SUFFIXES = {".c", ".h"}
CPP_SUFFIXES = {".cc", ".cpp", ".cxx", ".hpp", ".hh"}
C_CPP_SUFFIXES = C_SUFFIXES | CPP_SUFFIXES
C_CPP_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
C_CPP_HEADER_SUFFIXES = {".h", ".hpp", ".hh"}
LUA_SUFFIXES = {".lua"}

SourceLanguage = Literal["c", "cpp", "lua"]


@dataclass(frozen=True)
class LanguageCapability:
    analysis_id: Literal["c_cpp", "lua"]
    source_languages: tuple[SourceLanguage, ...]
    suffixes: frozenset[str]
    inventory_provider: Literal["c_cpp", "lua"]
    scope_provider: Literal["lua"] | None
    semantic_provider: Literal["c_cpp", "lua"]
    coverage_provider: Literal["exact", "lua_method"]
    rubric_path: str
    inventory_context: bool


@dataclass(frozen=True)
class FrameworkCapability:
    framework_id: Literal["openubmc"]
    base_language: Literal["lua"]
    semantic_provider: Literal["openubmc"]
    rubric_path: str


LANGUAGE_CAPABILITIES = (
    LanguageCapability(
        analysis_id="c_cpp",
        source_languages=("c", "cpp"),
        suffixes=frozenset(C_CPP_SUFFIXES),
        inventory_provider="c_cpp",
        scope_provider=None,
        semantic_provider="c_cpp",
        coverage_provider="exact",
        rubric_path="src/pangea_agent/rubrics/builtin/c_cpp_analysis.md",
        inventory_context=False,
    ),
    LanguageCapability(
        analysis_id="lua",
        source_languages=("lua",),
        suffixes=frozenset(LUA_SUFFIXES),
        inventory_provider="lua",
        scope_provider="lua",
        semantic_provider="lua",
        coverage_provider="lua_method",
        rubric_path="src/pangea_agent/rubrics/builtin/lua_analysis.md",
        inventory_context=True,
    ),
)

FRAMEWORK_CAPABILITIES = (
    FrameworkCapability(
        framework_id="openubmc",
        base_language="lua",
        semantic_provider="openubmc",
        rubric_path="src/pangea_agent/rubrics/builtin/openubmc_analysis.md",
    ),
)

CODE_SUFFIXES = set().union(*(
    capability.suffixes for capability in LANGUAGE_CAPABILITIES
))

_CAPABILITY_BY_SOURCE_LANGUAGE = {
    language: capability
    for capability in LANGUAGE_CAPABILITIES
    for language in capability.source_languages
}
_CAPABILITY_BY_ANALYSIS_ID = {
    capability.analysis_id: capability for capability in LANGUAGE_CAPABILITIES
}
_CAPABILITY_BY_FRAMEWORK_ID = {
    capability.framework_id: capability for capability in FRAMEWORK_CAPABILITIES
}


def language_for_path(path: Path) -> SourceLanguage | None:
    suffix = path.suffix.lower()
    for capability in LANGUAGE_CAPABILITIES:
        if suffix not in capability.suffixes:
            continue
        for language in capability.source_languages:
            if language == "c" and suffix in C_SUFFIXES:
                return language
            if language == "cpp" and suffix in CPP_SUFFIXES:
                return language
            if language not in {"c", "cpp"}:
                return language
    return None


def capability_for_source_language(language: SourceLanguage) -> LanguageCapability:
    return _CAPABILITY_BY_SOURCE_LANGUAGE[language]


def capability_for_analysis_id(analysis_id: str) -> LanguageCapability:
    return _CAPABILITY_BY_ANALYSIS_ID[analysis_id]


def analysis_language_for_path(path: Path) -> str | None:
    language = language_for_path(path)
    if language is None:
        return None
    return capability_for_source_language(language).analysis_id


def inventory_context_for_path(path: Path) -> bool:
    language = language_for_path(path)
    return bool(
        language is not None
        and capability_for_source_language(language).inventory_context
    )


def checkpoint_rubrics(languages: list[str], frameworks: list[str]) -> list[str]:
    framework_capabilities = _framework_capabilities(languages, frameworks)
    paths = [
        capability.rubric_path
        for capability in LANGUAGE_CAPABILITIES
        if capability.analysis_id in languages
    ]
    paths.extend(
        capability.rubric_path
        for capability in framework_capabilities
    )
    return paths


def semantic_providers(languages: list[str], frameworks: list[str]) -> set[str]:
    providers = {
        capability_for_analysis_id(language).semantic_provider
        for language in languages
    }
    providers.update(
        capability.semantic_provider
        for capability in _framework_capabilities(languages, frameworks)
    )
    return providers


def _framework_capabilities(
    languages: list[str], frameworks: list[str]
) -> list[FrameworkCapability]:
    capabilities = []
    for framework in frameworks:
        try:
            capability = _CAPABILITY_BY_FRAMEWORK_ID[framework]
        except KeyError as exc:
            raise ValueError(f"unsupported framework capability: {framework}") from exc
        if capability.base_language not in languages:
            raise ValueError(
                f"framework {framework} requires language {capability.base_language}"
            )
        capabilities.append(capability)
    return capabilities


def coverage_symbol_aliases(
    symbol: str,
    language: SourceLanguage | Literal["c_cpp"] | None,
    *,
    path_scoped: bool,
) -> set[str]:
    aliases = {symbol}
    if language in {None, "c_cpp"}:
        return aliases
    capability = capability_for_source_language(language)
    if capability.coverage_provider == "lua_method":
        aliases.add(symbol.replace(":", "."))
        if path_scoped:
            aliases.add(symbol.rsplit(":", 1)[-1].rsplit(".", 1)[-1])
    return aliases
