from __future__ import annotations

from pathlib import Path
import re
import shlex

from pangea_agent.models.analysis import AnalysisLanguage

C_CPP_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
LUA_SUFFIXES = {".lua"}
PYTHON_SUFFIXES = {".py", ".pyw"}
SHELL_SUFFIXES = {".sh", ".bash"}
SUPPORTED_LANGUAGES = ("c_cpp", "lua", "python", "shell")
LANGUAGE_LABELS = {"c_cpp": "C/C++", "lua": "Lua", "python": "Python", "shell": "sh/Bash", "mixed": "多语言"}


def file_language(path: Path) -> str | None:
    """Identify source without importing or executing it; shebangs override script suffixes."""
    suffix = path.suffix.lower()
    if suffix in C_CPP_SUFFIXES:
        return "c_cpp"
    if suffix in LUA_SUFFIXES:
        return "lua"
    # Bound reads even for extensionless binaries and very long first lines.
    with path.open("rb") as stream:
        first = stream.readline(4096)
    if first.startswith(b"#!"):
        try:
            words = shlex.split(first[2:].decode("utf-8").strip())
        except (ValueError, UnicodeError):
            return None
        if not words:
            return None
        interpreter = Path(words[0]).name
        if interpreter == "env":
            rest = words[1:]
            while rest and (rest[0].startswith("-") or "=" in rest[0]):
                flag = rest.pop(0)
                if flag in {"-u", "--unset", "-C", "--chdir"} and rest:
                    rest.pop(0)
            interpreter = Path(rest[0]).name if rest else ""
        if interpreter in {"sh", "bash"}:
            return "shell"
        if re.fullmatch(r"python(?:[23](?:\.\d+)?)?", interpreter):
            return "python"
        # Do not mislabel zsh/fish/PowerShell as sh solely because of a .sh suffix.
        return None
    if suffix in PYTHON_SUFFIXES:
        return "python"
    if suffix in SHELL_SUFFIXES:
        return "shell"
    return None


def source_files(repositories: list[dict], scopes: list[str]):
    """Yield unique in-bound source files, retaining repository identity."""
    seen = set()
    for repository in repositories:
        root = Path(repository["source_root"]).resolve()
        for scope in scopes or ["."]:
            candidate = root / _normalize(scope)
            _ensure_inside_repository(candidate.resolve(), root)
            if not candidate.exists():
                continue
            paths = [candidate] if candidate.is_file() else candidate.rglob("*")
            for path in paths:
                if _ignored(path, root):
                    continue
                _ensure_inside_repository(path.resolve(), root)
                if not path.is_file():
                    continue
                key = (repository["repo_id"], path.relative_to(root).as_posix())
                if key in seen:
                    continue
                seen.add(key)
                language = file_language(path)
                if language:
                    yield repository, path, language


def detect_analysis_languages(repositories: list[dict], requested_scopes: list[str]) -> list[str]:
    found = {language for _, _, language in source_files(repositories, requested_scopes)}
    if not found:
        raise ValueError("用户指定范围没有可分析的 C/C++、Lua、Python 或 sh/Bash 源码")
    return sorted(found)


def language_mode(languages: list[str]) -> str:
    return languages[0] if len(languages) == 1 else "mixed"

IGNORED_PARTS = {
    ".git",
    ".pangea",
    "build",
    "dist",
    "third_party",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
}


def detect_analysis_language(
    repositories: list[dict], requested_scopes: list[str]
) -> AnalysisLanguage:
    return language_mode(detect_analysis_languages(repositories, requested_scopes))


def _normalize(value: str) -> str:
    return value.replace("\\", "/").strip("/") or "."


def _ignored(path: Path, root: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.relative_to(root).parts)


def _ensure_inside_repository(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"源码范围越过仓库边界：{path}") from exc
