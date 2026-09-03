from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_file_paths(repository_root: Path, scope: list[dict[str, str]]) -> list[Path]:
    root = repository_root.resolve()
    paths: set[Path] = set()
    for item in scope:
        candidate = Path(item["verified"])
        if candidate.is_symlink():
            raise ValueError(f"source_scope 不能是符号链接：{item['raw']}")
        if candidate.is_file():
            paths.add(candidate.relative_to(root))
            continue
        for child in candidate.rglob("*"):
            relative = child.resolve().relative_to(root)
            if ".git" in relative.parts:
                continue
            if child.is_symlink():
                raise ValueError(f"源码范围包含符号链接，无法安全冻结：{relative}")
            if child.is_file():
                paths.add(relative)
    return sorted(paths, key=lambda value: value.as_posix().casefold())


def _digest_manifest(files: list[dict[str, object]]) -> str:
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_source_snapshot(
    repository_root: str | Path,
    scope: list[dict[str, str]],
    destination: str | Path,
    *,
    repo_id: str,
    run_id: str,
    git: dict | None = None,
) -> dict:
    """Copy only the verified source scope and return its immutable manifest."""
    root = Path(repository_root).resolve()
    target = Path(destination)
    if not scope:
        raise ValueError("source_scope 不能为空；必须先选择要冻结的源码范围")
    files = _relative_file_paths(root, scope)
    if not files:
        raise ValueError("source_scope 没有可冻结的普通源码文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError(f"源码快照目录已存在：{target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        repository = temporary / "repository"
        manifest_files: list[dict[str, object]] = []
        for relative in files:
            source = (root / relative).resolve()
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"源码路径越过仓库边界：{relative}") from exc
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"源码文件不可安全冻结：{relative}")
            destination_file = repository / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination_file)
            manifest_files.append({
                "path": relative.as_posix(),
                "size": destination_file.stat().st_size,
                "sha256": _sha256(destination_file),
            })
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "repo_id": repo_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_root": str(root),
            "source_scope": scope,
            "git": git or {"version_status": "unversioned"},
            "files": manifest_files,
            "file_count": len(manifest_files),
            "snapshot_digest": f"sha256:{_digest_manifest(manifest_files)}",
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_source_snapshot(
    snapshot_root: str | Path,
    *,
    run_id: str | None = None,
    repo_id: str | None = None,
) -> dict:
    root = Path(snapshot_root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"源码快照清单不存在：{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"源码快照清单无法解析：{manifest_path}") from exc
    if run_id is not None and manifest.get("run_id") != run_id:
        raise ValueError("源码快照 run_id 与当前 Run 不一致")
    if repo_id is not None and manifest.get("repo_id") != repo_id:
        raise ValueError("源码快照 repo_id 与当前仓库不一致")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("源码快照清单没有文件")
    for item in files:
        relative = item.get("path") if isinstance(item, dict) else None
        expected = item.get("sha256") if isinstance(item, dict) else None
        if not isinstance(relative, str) or not relative or not isinstance(expected, str):
            raise ValueError("源码快照清单包含非法文件项")
        path = (root / "repository" / relative).resolve()
        try:
            path.relative_to((root / "repository").resolve())
        except ValueError as exc:
            raise ValueError(f"源码快照文件越过边界：{relative}") from exc
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"源码快照完整性校验失败：{relative}")
    actual = f"sha256:{_digest_manifest(files)}"
    if actual != manifest.get("snapshot_digest"):
        raise ValueError("源码快照清单 digest 不匹配")
    return manifest
