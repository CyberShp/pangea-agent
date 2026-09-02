from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.analysis import FrozenSkillRef, SkillTaskContext


SKILL_ID = "codetalks-skill"
SKILL_VERSION = "1.0.0"
DERIVED_FROM = "codetalks-fused-v2.4"
SOURCE_ROOT = Path(__file__).resolve().parent / "skill_packages" / SKILL_ID


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _validated_package(root: Path) -> dict:
    manifest = read_json(root / "workflow-manifest.json")
    integration = read_json(root / "pangea-integration.json")
    if manifest.get("version") != SKILL_VERSION:
        raise ValueError(
            f"Skill manifest version 不匹配：{manifest.get('version')} != {SKILL_VERSION}"
        )
    expected = {
        "skill_id": SKILL_ID,
        "version": SKILL_VERSION,
        "derived_from": DERIVED_FROM,
    }
    actual = {key: integration.get(key) for key in expected}
    if actual != expected:
        raise ValueError(f"Skill integration metadata 不匹配：{actual}")
    for relative in [
        "SKILL.md",
        "workflow-manifest.json",
        "pangea-integration.json",
        *integration.get("step_files", {}).values(),
        *(
            path
            for paths in integration.get("references_by_stage", {}).values()
            for path in paths
        ),
    ]:
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"Skill 文件越过包边界：{relative}") from exc
        if not path.is_file():
            raise ValueError(f"Skill 文件不存在：{relative}")
    return integration


def freeze_codetalks_skill(run_dir: Path) -> FrozenSkillRef:
    inputs_root = run_dir / "inputs"
    destination = inputs_root / "skills" / SKILL_ID
    receipt_path = inputs_root / "skill.json"
    if destination.exists():
        _validated_package(destination)
        frozen = _frozen_reference(destination)
        if receipt_path.is_file():
            recorded = FrozenSkillRef.model_validate(read_json(receipt_path))
            if recorded != frozen:
                raise ValueError("Run 中冻结的 Skill 与 skill.json 回执不一致")
        else:
            write_json(receipt_path, frozen.model_dump(mode="json"))
        return frozen

    _validated_package(SOURCE_ROOT)
    staging = inputs_root / ".skill-staging" / SKILL_ID
    if staging.parent.exists():
        shutil.rmtree(staging.parent)
    staging.parent.mkdir(parents=True)
    shutil.copytree(SOURCE_ROOT, staging)
    _validated_package(staging)
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(destination)
    shutil.rmtree(staging.parent)
    frozen = _frozen_reference(destination)
    write_json(receipt_path, frozen.model_dump(mode="json"))
    return frozen


def _frozen_reference(root: Path) -> FrozenSkillRef:
    return FrozenSkillRef(
        skill_id=SKILL_ID,
        version=SKILL_VERSION,
        derived_from=DERIVED_FROM,
        digest=_tree_digest(root),
        root_path=str(root),
        manifest_path=str(root / "workflow-manifest.json"),
        integration_path=str(root / "pangea-integration.json"),
    )


def task_skill_context(
    frozen: FrozenSkillRef,
    stage: str,
) -> SkillTaskContext:
    integration = read_json(Path(frozen.integration_path))
    step_ids = integration["stage_mapping"][stage]
    root = Path(frozen.root_path)
    return SkillTaskContext(
        **frozen.model_dump(mode="json"),
        stage=stage,
        step_ids=step_ids,
        step_paths=[str(root / integration["step_files"][step_id]) for step_id in step_ids],
        reference_paths=[
            str(root / relative)
            for relative in integration["references_by_stage"][stage]
        ],
    )
