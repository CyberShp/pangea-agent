from __future__ import annotations

import shutil
from pathlib import Path

from pangea_agent.agent_io import read_json


SKILL_ID = "codetalks-skill"
SKILL_VERSION = "1.2.0"
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
