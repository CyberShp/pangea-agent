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

_SPECIALIZED_RUBRICS = (
    ("storage_iscsi", "src/pangea_agent/rubrics/builtin/storage_iscsi.md"),
    ("storage_nvmeof", "src/pangea_agent/rubrics/builtin/storage_nvmeof.md"),
    (
        "storage_resource_recovery",
        "src/pangea_agent/rubrics/builtin/storage_resource_recovery.md",
    ),
    ("vendor_dpdk", "src/pangea_agent/rubrics/builtin/vendor_dpdk.md"),
    ("vendor_mlx_rdma", "src/pangea_agent/rubrics/builtin/vendor_mlx_rdma.md"),
    ("vendor_nvidia_doca", "src/pangea_agent/rubrics/builtin/vendor_nvidia_doca.md"),
)
_RESOURCE_KEYWORDS = {
    "alloc",
    "cache",
    "calloc",
    "close",
    "destroy",
    "free",
    "queue",
    "ref",
    "register",
    "release",
    "timer",
    "unregister",
}

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


def checkpoint_rubrics(
    languages: list[str],
    frameworks: list[str],
    *,
    repo_id: str | None = None,
    source_paths: list[str] | None = None,
    inventory: dict | None = None,
) -> list[str]:
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
    paths.extend(
        _specialized_rubric_paths(
            repo_id=repo_id,
            source_paths=source_paths or [],
            inventory=inventory or {},
        )
    )
    return paths


def _specialized_rubric_paths(
    *, repo_id: str | None, source_paths: list[str], inventory: dict
) -> list[str]:
    normalized_paths = [path.replace("\\", "/").lower() for path in source_paths]
    path_text = "\n".join(normalized_paths)
    owned = set(normalized_paths)
    source_items = [
        item
        for item in inventory.get("files", [])
        if item.get("repo_id") == repo_id
        and str(item.get("path", "")).replace("\\", "/").lower() in owned
    ]
    resource_keywords = {
        keyword
        for item in source_items
        for signal in item.get("resource_signals", [])
        for keyword in signal.get("keywords", [])
    }
    matched = {
        "storage_iscsi": "iscsi" in path_text,
        "storage_nvmeof": any(
            token in path_text
            for token in ("/nvmf/", "nvme_tcp", "nvme_rdma", "nvme_fabric")
        ),
        "storage_resource_recovery": bool(resource_keywords & _RESOURCE_KEYWORDS),
        "vendor_dpdk": "dpdk" in path_text,
        "vendor_mlx_rdma": any(
            token in path_text
            for token in ("mlx4", "mlx5", "/rdma", "rdma_", "_rdma")
        ),
        "vendor_nvidia_doca": "doca" in path_text,
    }
    return [path for rubric_id, path in _SPECIALIZED_RUBRICS if matched[rubric_id]]


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
