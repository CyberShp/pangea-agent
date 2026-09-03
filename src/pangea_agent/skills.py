from __future__ import annotations

import shutil
from hashlib import sha256
from pathlib import Path

from pangea_agent.agent_io import read_json


SKILL_ID = "codetalks-skill"
SKILL_VERSION = "1.3.0"
DERIVED_FROM = "codetalks-fused-v2.4"
SOURCE_ROOT = Path(__file__).resolve().parent / "skill_packages" / SKILL_ID


def validate_skill_package(root: Path) -> dict:
    manifest = read_json(root / "workflow-manifest.json")
    if manifest.get("version") != SKILL_VERSION:
        raise ValueError(
            f"Skill manifest version 不匹配：{manifest.get('version')} != {SKILL_VERSION}"
        )
    for relative in [
        "SKILL.md",
        "workflow-manifest.json",
        *(step["file"] for step in manifest.get("steps", [])),
        *manifest.get("required_core_rules", {}).values(),
        *(path for paths in manifest.get("language_profiles", {}).values() for path in paths),
    ]:
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"Skill 文件越过包边界：{relative}") from exc
        if not path.is_file():
            raise ValueError(f"Skill 文件不存在：{relative}")
    return manifest


def freeze_skill_package(destination: Path) -> Path:
    validate_skill_package(SOURCE_ROOT)
    if destination.exists():
        raise ValueError(f"Skill 冻结目录已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_ROOT, destination)
    validate_skill_package(destination)
    return destination.resolve()


def skill_package_digest(root: Path) -> str:
    """Return a stable digest of the exact files an Agent will execute."""
    validate_skill_package(root)
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"
