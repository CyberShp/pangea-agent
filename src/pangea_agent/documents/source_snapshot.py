from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _windows_extended_path(value: str) -> str:
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _filesystem_path(path: str | Path) -> str:
    value = os.fspath(path)
    if os.name != "nt":
        return value
    if value.startswith("\\\\?\\"):
        return value
    return _windows_extended_path(os.path.abspath(value))


def _relative_file_paths(repository_root: Path, scope: list[dict[str, str]]) -> list[Path]:
    root = repository_root.resolve()
    filesystem_root = Path(_filesystem_path(root))
    paths: set[Path] = set()
    for item in scope:
        candidate = Path(_filesystem_path(Path(item["verified"])))
        if candidate.is_symlink():
            raise ValueError(f"source_scope 不能是符号链接：{item['raw']}")
        if candidate.is_file():
            paths.add(candidate.relative_to(filesystem_root))
            continue
        for child in candidate.rglob("*"):
            relative = child.resolve().relative_to(filesystem_root)
            if ".git" in relative.parts:
                continue
            if child.is_symlink():
                raise ValueError(f"源码范围包含符号链接，无法安全冻结：{relative}")
            if child.is_file():
                paths.add(relative)
    return sorted(paths, key=lambda value: value.as_posix().casefold())


def _publish_snapshot(temporary: Path, target: Path) -> None:
    source_path = _filesystem_path(temporary)
    target_path = _filesystem_path(target)
    delays = (0.05, 0.1, 0.2, 0.4, 0.8)
    for attempt in range(len(delays) + 1):
        if os.path.lexists(target_path):
            raise FileExistsError(f"源码快照目录已存在：{target}")
        try:
            # Windows rename refuses an existing destination, including one
            # created after the check. Never replace another snapshot.
            os.rename(source_path, target_path)
            return
        except OSError as exc:
            if (
                os.name != "nt"
                or getattr(exc, "winerror", None) not in (5, 32, 33)
                or attempt == len(delays)
            ):
                raise
            # Retry only publication; keep the completed copy and manifest.
            time.sleep(delays[attempt])


def create_source_snapshot(
    repository_root: str | Path,
    scope: list[dict[str, str]],
    destination: str | Path,
    *,
    repo_id: str,
    run_id: str,
    git: dict | None = None,
) -> dict:
    """Copy the verified source scope once and return its inventory manifest."""
    started_at = time.perf_counter()
    root = Path(repository_root).resolve()
    target = Path(destination)
    if not scope:
        raise ValueError("source_scope 不能为空；必须先选择要冻结的源码范围")
    files = _relative_file_paths(root, scope)
    if not files:
        raise ValueError("source_scope 没有可冻结的普通源码文件")
    os.makedirs(_filesystem_path(target.parent), exist_ok=True)
    if os.path.lexists(_filesystem_path(target)):
        raise ValueError(f"源码快照目录已存在：{target}")
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{target.name}-",
        dir=_filesystem_path(target.parent),
    ))
    stage = "copy_source"
    try:
        repository = temporary / "repository"
        manifest_files: list[dict[str, object]] = []
        for relative in files:
            source = (root / relative).resolve()
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"源码路径越过仓库边界：{relative}") from exc
            if os.path.islink(_filesystem_path(source)) or not os.path.isfile(
                _filesystem_path(source)
            ):
                raise ValueError(f"源码文件不可安全冻结：{relative}")
            destination_file = repository / relative
            os.makedirs(_filesystem_path(destination_file.parent), exist_ok=True)
            shutil.copy2(_filesystem_path(source), _filesystem_path(destination_file))
            manifest_files.append({
                "path": relative.as_posix(),
                "size": os.path.getsize(_filesystem_path(destination_file)),
            })
        manifest = {
            "schema_version": "2.0",
            "run_id": run_id,
            "repo_id": repo_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_root": str(root),
            "source_scope": scope,
            "git": git or {"version_status": "unversioned"},
            "files": manifest_files,
            "file_count": len(manifest_files),
            "total_bytes": sum(int(item["size"]) for item in manifest_files),
            "snapshot_duration_ms": round((time.perf_counter() - started_at) * 1000),
        }
        stage = "write_manifest"
        with open(_filesystem_path(temporary / "manifest.json"), "w", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        stage = "publish_snapshot"
        _publish_snapshot(temporary, target)
        return manifest
    except Exception as exc:
        shutil.rmtree(_filesystem_path(temporary), ignore_errors=True)
        if isinstance(exc, OSError):
            raise OSError(
                "源码快照创建失败："
                f"stage={stage}, winerror={getattr(exc, 'winerror', None)}, "
                f"source={root}, destination={target}, "
                f"destination_length={len(str(target))}: {exc}"
            ) from exc
        raise


def read_source_snapshot_manifest(
    snapshot_root: str | Path,
    *,
    run_id: str | None = None,
    repo_id: str | None = None,
) -> dict:
    """Read the snapshot inventory without rereading copied source files."""
    root = Path(snapshot_root)
    manifest_path = root / "manifest.json"
    if not os.path.isfile(_filesystem_path(manifest_path)):
        raise ValueError(f"源码快照清单不存在：{manifest_path}")
    try:
        with open(_filesystem_path(manifest_path), encoding="utf-8") as stream:
            manifest = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ValueError(f"源码快照清单无法解析：{manifest_path}") from exc
    if run_id is not None and manifest.get("run_id") != run_id:
        raise ValueError("源码快照 run_id 与当前 Run 不一致")
    if repo_id is not None and manifest.get("repo_id") != repo_id:
        raise ValueError("源码快照 repo_id 与当前仓库不一致")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("源码快照清单没有文件")
    if manifest.get("file_count") != len(files):
        raise ValueError("源码快照 file_count 与清单不一致")
    for item in files:
        relative = item.get("path") if isinstance(item, dict) else None
        size = item.get("size") if isinstance(item, dict) else None
        if not isinstance(relative, str) or not relative or not isinstance(size, int) or size < 0:
            raise ValueError("源码快照清单包含非法文件项")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"源码快照文件越过边界：{relative}")
    return manifest


def extend_source_snapshot(repository_root: Path, scope: list[dict[str, str]], destination: Path,
                           *, repo_id: str, run_id: str) -> dict:
    """Append explicitly requested source files without replacing frozen content."""
    if not destination.exists():
        return create_source_snapshot(repository_root, scope, destination, repo_id=repo_id, run_id=run_id)
    manifest = read_source_snapshot_manifest(destination, run_id=run_id, repo_id=repo_id)
    known = {item["path"] for item in manifest["files"]}
    for relative in _relative_file_paths(repository_root, scope):
        if relative.as_posix() in known:
            continue
        source = (repository_root / relative).resolve()
        source.relative_to(repository_root.resolve())
        target = destination / "repository" / relative
        target.resolve().relative_to((destination / "repository").resolve())
        os.makedirs(_filesystem_path(target.parent), exist_ok=True)
        # Exclusive creation protects existing frozen files, including interrupted copies.
        with open(_filesystem_path(source), "rb") as reader, open(_filesystem_path(target), "xb") as writer:
            shutil.copyfileobj(reader, writer)
        manifest["files"].append({"path": relative.as_posix(), "size": target.stat().st_size})
    manifest.setdefault("additional_scopes", []).append({
        "scope": scope, "copied_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(item["size"] for item in manifest["files"])
    temporary = destination / "manifest.pending.json"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination / "manifest.json")
    return manifest
