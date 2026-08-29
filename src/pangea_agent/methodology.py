from __future__ import annotations

import shutil
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import analysis_asset_inputs
from pangea_agent.models.methodology import (
    FrozenMethodologyCatalog,
    FrozenMethodologyManifest,
    MethodologyCandidate,
    MethodologyCandidateFile,
    MethodologyDerivationReceipt,
    MethodologyDerivationTask,
    MethodologyRecord,
    MethodologyRegistry,
    MethodologyStatus,
    utc_now,
)


GENERAL_METHODOLOGIES = {
    "c_cpp_analysis.md": (
        "C/C++ 源码分析",
        "每个分析单元默认加载",
        "项目内置通用方法",
    ),
    "dfx.md": (
        "六维 DFX 分析",
        "每个分析单元默认加载",
        "项目内置通用方法",
    ),
    "risk_reproducibility.md": (
        "风险可复现性",
        "每个分析单元默认加载",
        "项目内置通用方法",
    ),
    "test_case_generation.md": (
        "测试用例生成",
        "每个分析单元默认加载",
        "项目内置通用方法",
    ),
}

SPECIALIZED_METHODOLOGIES = {
    "storage_iscsi.md": (
        "iSCSI 专项分析",
        "源码范围命中 iSCSI 协议信号",
        "iSCSI 规范方向与固定 open-iscsi/SPDK 参考实现",
    ),
    "storage_nvme.md": (
        "NVMe 核心专项分析",
        "源码范围命中 NVMe controller、namespace、queue 或 command 信号",
        "NVMe Base 2.4、NVM Command Set 1.3 与固定 libnvme/nvme-cli/SPDK/blktests",
    ),
    "storage_nvmeof.md": (
        "NVMe-oF 专项分析",
        "源码范围命中 NVMe-oF transport、discovery 或认证信号",
        "NVMe 2.4 transport 规范方向与固定 libnvme/SPDK/blktests",
    ),
    "storage_sas_scsi.md": (
        "SAS / SCSI 磁盘专项分析",
        "源码范围命中 SAS transport、SCSI command、sense 或 EH 信号",
        "SAS-4.1、SPC-6、SBC-5 与固定 Linux/sg3_utils/smartmontools",
    ),
    "storage_resource_recovery.md": (
        "资源与恢复专项分析",
        "源码或上下文范围命中成对生命周期、引用计数或资源池信号",
        "项目内置生命周期方法与固定 SPDK/DPDK/RDMA 参考实现",
    ),
    "vendor_dpdk.md": (
        "DPDK 专项分析",
        "源码范围命中明确 DPDK、rte_ 或 ethdev 信号",
        "固定 DPDK main 参考实现",
    ),
    "vendor_mlx_rdma.md": (
        "MLX / RDMA 专项分析",
        "源码范围命中 mlx、verbs、RDMA CM、RoCE 或 InfiniBand 信号",
        "固定 rdma-core mlx provider 参考实现",
    ),
    "vendor_nvidia_doca.md": (
        "NVIDIA DOCA 专项分析",
        "源码范围命中 DOCA 或 BlueField 信号",
        "固定 NVIDIA DOCA 3.4.0 samples",
    ),
}


def methodology_registry_path(data_root: str | Path) -> Path:
    return Path(data_root) / "methodologies" / "registry.json"


def _derivation_task_path(data_root: str | Path, task_id: str) -> Path:
    if (
        not task_id
        or task_id in {".", ".."}
        or Path(task_id).name != task_id
    ):
        raise ValueError("方法论提炼 task_id 无效")
    return Path(data_root) / "methodologies" / "tasks" / task_id / "task.json"


def _derivation_view(
    task_path: Path,
    *,
    include_completion: bool = False,
) -> dict:
    task = MethodologyDerivationTask.model_validate(read_json(task_path))
    result_path = Path(task.result_path)
    receipt_path = task_path.parent / "completion.json"
    receipt = None
    if receipt_path.is_file():
        receipt = MethodologyDerivationReceipt.model_validate(
            read_json(receipt_path)
        )
        status = "completed"
    elif result_path.is_file():
        status = "ready"
    else:
        status = "pending"
    view = {
        "task_id": task.task_id,
        "action_id": task.action_id,
        "status": status,
        "created_at": (
            task.created_at.isoformat()
            if task.created_at is not None
            else datetime.fromtimestamp(
                task_path.stat().st_mtime
            ).astimezone().isoformat()
        ),
        "completed_at": (
            receipt.completed_at.isoformat() if receipt is not None else None
        ),
        "source_asset_ids": task.source_asset_ids,
        "task_path": str(task_path),
        "result_path": task.result_path,
    }
    if include_completion and receipt is not None:
        view["completion"] = receipt.model_dump(mode="json")
    return view


def list_methodology_derivations(
    data_root: str | Path,
    *,
    cursor: int = 0,
    limit: int = 50,
) -> dict:
    if cursor < 0:
        raise ValueError("cursor 不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    root = Path(data_root) / "methodologies" / "tasks"
    task_paths = sorted(
        root.glob("*/task.json") if root.is_dir() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    page = task_paths[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "items": [_derivation_view(path) for path in page],
        "next_cursor": next_cursor if next_cursor < len(task_paths) else None,
        "total": len(task_paths),
    }


def show_methodology_derivation(
    data_root: str | Path,
    task_id: str,
) -> dict:
    task_path = _derivation_task_path(data_root, task_id)
    if not task_path.is_file():
        raise ValueError(f"方法论提炼任务不存在：{task_id}")
    return _derivation_view(task_path, include_completion=True)


def prepare_methodology_derivation(
    data_root: str | Path,
    asset_ids: list[str],
) -> dict:
    selected_asset_ids = list(dict.fromkeys(
        asset_id.strip() for asset_id in asset_ids if asset_id.strip()
    ))
    if not selected_asset_ids:
        raise ValueError("至少选择一个已批准的历史缺陷资产")
    items = analysis_asset_inputs(str(data_root), selected_asset_ids)["items"]
    historical_items = {
        item_id: item
        for item_id, item in items.items()
        if item.get("item_type") == "historical_defect"
    }
    historical_asset_ids = {
        item["asset_id"] for item in historical_items.values()
    }
    unavailable_asset_ids = sorted(
        set(selected_asset_ids) - historical_asset_ids
    )
    if unavailable_asset_ids:
        raise ValueError(
            "以下资产不是含有效条目的已批准历史缺陷："
            + ", ".join(unavailable_asset_ids)
        )

    task_id = f"methodology-{uuid4()}"
    action_id = f"{task_id}:derive"
    task_root = Path(data_root) / "methodologies" / "tasks" / task_id
    source_items_path = task_root / "source-items.json"
    existing_methodologies_path = task_root / "existing-methodologies.json"
    result_path = task_root / "result.json"
    result_schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "methodology_candidate.schema.json"
    )
    write_json(source_items_path, {
        "source": "approved_historical_defects",
        "items": historical_items,
    })
    write_json(existing_methodologies_path, {
        "items": [
            item.model_dump(mode="json")
            for item in _read_registry(data_root).methodologies
        ]
    })
    task = MethodologyDerivationTask(
        task_id=task_id,
        action_id=action_id,
        created_at=utc_now(),
        data_root=str(data_root),
        source_asset_ids=selected_asset_ids,
        source_items_path=str(source_items_path),
        existing_methodologies_path=str(existing_methodologies_path),
        result_schema_path=str(result_schema_path),
        result_path=str(result_path),
    )
    task_path = task_root / "task.json"
    write_json(task_path, task.model_dump(mode="json"))
    return {
        "action": {
            "action_id": action_id,
            "action": "dispatch_agent",
            "role": "methodology",
            "stage": "candidate_derivation",
            "task_path": str(task_path),
            "task_id": task_id,
        }
    }


def complete_methodology_derivation(task_path: str | Path) -> dict:
    path = Path(task_path)
    task = MethodologyDerivationTask.model_validate(read_json(path))
    result_path = Path(task.result_path)
    if not result_path.is_file():
        raise ValueError(f"方法论 Agent 尚未写入结果：{result_path}")
    result_sha256 = sha256(result_path.read_bytes()).hexdigest()
    receipt_path = path.parent / "completion.json"
    if receipt_path.is_file():
        receipt = MethodologyDerivationReceipt.model_validate(
            read_json(receipt_path)
        )
        if receipt.result_sha256 != result_sha256:
            raise ValueError("已完成的方法论提炼结果发生变化，请创建新任务")
        return {
            "task_id": receipt.task_id,
            "status": "completed",
            "imported": receipt.imported,
        }
    candidate_file = MethodologyCandidateFile.model_validate(read_json(result_path))
    allowed_source_ids = set(
        read_json(Path(task.source_items_path)).get("items", {})
    )
    unknown_source_ids = sorted({
        source_item_id
        for candidate in candidate_file.candidates
        for source_item_id in candidate.source_item_ids
        if source_item_id not in allowed_source_ids
    })
    if unknown_source_ids:
        raise ValueError(
            "方法论候选引用了本任务未提供的历史缺陷条目："
            + ", ".join(unknown_source_ids)
        )
    imported = import_methodology_candidates(task.data_root, result_path)
    receipt = MethodologyDerivationReceipt(
        task_id=task.task_id,
        completed_at=utc_now(),
        result_sha256=result_sha256,
        imported=imported,
    )
    write_json(receipt_path, receipt.model_dump(mode="json"))
    return {
        "task_id": task.task_id,
        "status": "completed",
        "imported": imported,
    }


def _read_registry(data_root: str | Path) -> MethodologyRegistry:
    path = methodology_registry_path(data_root)
    if not path.is_file():
        return MethodologyRegistry()
    return MethodologyRegistry.model_validate(read_json(path))


def _write_registry(
    data_root: str | Path,
    registry: MethodologyRegistry,
) -> Path:
    path = methodology_registry_path(data_root)
    write_json(path, registry.model_dump(mode="json"))
    return path


def _candidate_values(candidate: MethodologyCandidate) -> dict:
    return {
        field_name: getattr(candidate, field_name)
        for field_name in MethodologyCandidate.model_fields
    }


def _approved_historical_items(data_root: str | Path) -> set[str]:
    items = analysis_asset_inputs(str(data_root))["items"]
    return {
        item_id
        for item_id, item in items.items()
        if item.get("item_type") == "historical_defect"
    }


def import_methodology_candidates(
    data_root: str | Path,
    input_path: str | Path,
) -> dict:
    path = Path(input_path)
    if not path.is_file():
        raise ValueError(f"方法论候选文件不存在：{path}")
    candidate_file = MethodologyCandidateFile.model_validate(read_json(path))
    approved_items = _approved_historical_items(data_root)
    unknown_items = sorted({
        item_id
        for candidate in candidate_file.candidates
        for item_id in candidate.source_item_ids
        if item_id not in approved_items
    })
    if unknown_items:
        raise ValueError(
            "方法论候选引用了未批准或不存在的历史缺陷条目："
            + ", ".join(unknown_items)
        )

    registry = _read_registry(data_root)
    records = {item.methodology_id: item for item in registry.methodologies}
    changes = []
    for candidate in candidate_file.candidates:
        existing = records.get(candidate.methodology_id)
        values = _candidate_values(candidate)
        now = utc_now()
        if existing is None:
            records[candidate.methodology_id] = MethodologyRecord(
                **values,
                status="candidate",
                created_at=now,
                updated_at=now,
            )
            change = "created"
        elif _candidate_values(existing) == values:
            change = "unchanged"
        else:
            records[candidate.methodology_id] = MethodologyRecord(
                **values,
                status="candidate",
                created_at=existing.created_at,
                updated_at=now,
            )
            change = "updated_requires_confirmation"
        changes.append({
            "methodology_id": candidate.methodology_id,
            "change": change,
            "status": records[candidate.methodology_id].status,
        })
    updated = MethodologyRegistry(
        methodologies=sorted(records.values(), key=lambda item: item.methodology_id)
    )
    registry_path = _write_registry(data_root, updated)
    return {
        "registry_path": str(registry_path),
        "items": changes,
        "total": len(updated.methodologies),
    }


def list_methodologies(
    data_root: str | Path,
    *,
    cursor: int = 0,
    limit: int = 50,
    status: MethodologyStatus | None = None,
    query: str | None = None,
) -> dict:
    if cursor < 0:
        raise ValueError("cursor 不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    normalized_query = (query or "").strip().casefold()
    records = [
        item
        for item in _read_registry(data_root).methodologies
        if (status is None or item.status == status)
        and (
            not normalized_query
            or normalized_query in "\n".join((item.methodology_id, item.title)).casefold()
        )
    ]
    page = records[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "items": [item.model_dump(mode="json") for item in page],
        "next_cursor": next_cursor if next_cursor < len(records) else None,
        "total": len(records),
    }


def show_methodology(data_root: str | Path, methodology_id: str) -> dict:
    for item in _read_registry(data_root).methodologies:
        if item.methodology_id == methodology_id:
            return item.model_dump(mode="json")
    raise ValueError(f"方法论不存在：{methodology_id}")


def set_methodology_status(
    data_root: str | Path,
    methodology_id: str,
    status: Literal["enabled", "disabled"],
) -> dict:
    registry = _read_registry(data_root)
    for index, item in enumerate(registry.methodologies):
        if item.methodology_id != methodology_id:
            continue
        changed = item.status != status
        if changed:
            item = item.model_copy(update={"status": status, "updated_at": utc_now()})
            registry.methodologies[index] = item
            _write_registry(data_root, registry)
        return {"item": item.model_dump(mode="json"), "changed": changed}
    raise ValueError(f"方法论不存在：{methodology_id}")


def _methodology_markdown(record: MethodologyRecord) -> str:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {item}" for item in values) or "- 无"

    return "\n".join([
        f"# {record.title}",
        "",
        f"- 方法论 ID：{record.methodology_id}",
        "- 来源：用户确认的历史缺陷资产",
        "- 使用要求：只提供分析方向，当前风险和用例仍须绑定当前源码或现行资料证据。",
        "",
        "## 适用条件",
        "",
        bullets(record.applicable_when),
        "",
        "## 分析检查",
        "",
        "\n".join(f"{number}. {item}" for number, item in enumerate(record.checks, 1)),
        "",
        "## 正常信号",
        "",
        bullets(record.expected_signals),
        "",
        "## 失败信号",
        "",
        bullets(record.failure_signals),
        "",
        "## 例外与限制",
        "",
        bullets(record.exceptions),
        "",
        "## 来源条目",
        "",
        bullets(record.source_item_ids),
        "",
    ])


def _validate_frozen_manifest(
    manifest: FrozenMethodologyManifest,
) -> FrozenMethodologyManifest:
    for item in manifest.enabled_user_methodologies:
        path = Path(item.path)
        if not path.is_file():
            raise ValueError(f"Run 冻结方法论不存在：{path}")
        if sha256(path.read_bytes()).hexdigest() != item.content_sha256:
            raise ValueError(f"Run 冻结方法论内容校验失败：{path}")
    return manifest


def _selection_catalog(
    manifest: FrozenMethodologyManifest,
) -> FrozenMethodologyCatalog:
    return FrozenMethodologyCatalog(
        run_id=manifest.run_id,
        frozen_at=manifest.frozen_at,
        enabled_user_methodologies=[{
            "methodology_id": item.methodology_id,
            "origin": item.origin,
            "title": item.title,
            "source_item_ids": item.source_item_ids,
            "applicable_when": item.applicable_when,
            "exceptions": item.exceptions,
        } for item in manifest.enabled_user_methodologies],
    )


def _ensure_selection_catalog(
    destination_root: Path,
    manifest: FrozenMethodologyManifest,
) -> Path:
    catalog_path = destination_root / "catalog.json"
    expected = _selection_catalog(manifest)
    if catalog_path.is_file():
        current = FrozenMethodologyCatalog.model_validate(read_json(catalog_path))
        if current != expected:
            raise ValueError("Run 冻结方法论精简目录与冻结清单不一致")
    else:
        write_json(catalog_path, expected.model_dump(mode="json"))
    return catalog_path


def freeze_enabled_methodologies(
    data_root: str | Path,
    run_dir: str | Path,
    run_id: str,
) -> FrozenMethodologyManifest:
    destination_root = Path(run_dir) / "inputs" / "methodologies"
    manifest_path = destination_root / "manifest.json"
    if manifest_path.is_file():
        manifest = _validate_frozen_manifest(
            FrozenMethodologyManifest.model_validate(read_json(manifest_path))
        )
        _ensure_selection_catalog(destination_root, manifest)
        return manifest

    staging_root = destination_root.parent / ".methodology-staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    user_root = staging_root / "user"
    user_root.mkdir(parents=True)
    enabled = []
    excluded = []
    for record in sorted(
        _read_registry(data_root).methodologies,
        key=lambda item: item.methodology_id,
    ):
        if record.status != "enabled":
            excluded.append({
                "methodology_id": record.methodology_id,
                "title": record.title,
                "status": record.status,
            })
            continue
        relative_path = Path("user") / f"{record.methodology_id}.md"
        staging_path = staging_root / relative_path
        content = _methodology_markdown(record)
        staging_path.write_text(content, encoding="utf-8")
        enabled.append({
            "methodology_id": record.methodology_id,
            "origin": "user",
            "title": record.title,
            "path": str(destination_root / relative_path),
            "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
            "source_item_ids": record.source_item_ids,
            "applicable_when": record.applicable_when,
            "exceptions": record.exceptions,
        })
    manifest = FrozenMethodologyManifest(
        run_id=run_id,
        frozen_at=utc_now(),
        enabled_user_methodologies=enabled,
        excluded_user_methodologies=excluded,
    )
    write_json(staging_root / "manifest.json", manifest.model_dump(mode="json"))
    write_json(
        staging_root / "catalog.json",
        _selection_catalog(manifest).model_dump(mode="json"),
    )
    if destination_root.exists():
        shutil.rmtree(destination_root)
    staging_root.replace(destination_root)
    return _validate_frozen_manifest(manifest)


def frozen_methodology_paths(run_dir: str | Path) -> list[str]:
    manifest_path = Path(run_dir) / "inputs" / "methodologies" / "manifest.json"
    if not manifest_path.is_file():
        return []
    manifest = _validate_frozen_manifest(
        FrozenMethodologyManifest.model_validate(read_json(manifest_path))
    )
    return [item.path for item in manifest.enabled_user_methodologies]


def _frozen_user_methodology(path: Path):
    manifest_path = path.parent.parent / "manifest.json"
    if path.parent.name != "user" or not manifest_path.is_file():
        return None, None
    manifest = FrozenMethodologyManifest.model_validate(read_json(manifest_path))
    item = next(
        (
            candidate
            for candidate in manifest.enabled_user_methodologies
            if Path(candidate.path) == path
        ),
        None,
    )
    return item, manifest_path


def methodology_manifest(task_path: str | Path) -> dict:
    """Describe rubrics already frozen in one task without affecting selection."""
    path_to_task = Path(task_path)
    if not path_to_task.is_file():
        return {"unit_id": None, "items": []}
    task = read_json(path_to_task)
    selection_reasons = task.get("unit", {}).get(
        "methodology_selection_reasons",
        {},
    )
    source_catalog = None
    items = []
    for raw_path in task.get("rubric_paths", []):
        path = Path(raw_path)
        if not path.is_file():
            continue
        frozen_user, frozen_manifest_path = _frozen_user_methodology(path)
        if frozen_user is not None:
            metadata = (
                frozen_user.title,
                selection_reasons.get(
                    frozen_user.methodology_id,
                    "Planning Agent 选择用于当前单元",
                ),
                "用户确认的历史缺陷资产",
            )
            selection_kind = "user"
        else:
            metadata = GENERAL_METHODOLOGIES.get(path.name)
            selection_kind = "general"
            if metadata is None:
                metadata = SPECIALIZED_METHODOLOGIES.get(path.name)
                selection_kind = "specialized"
        if metadata is None:
            heading = next(
                (
                    line.removeprefix("# ").strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("# ")
                ),
                path.stem,
            )
            metadata = (
                heading,
                "当前任务契约显式冻结",
                "任务提供的方法论；来源由对应资产或任务契约追溯",
            )
            selection_kind = "task"
        if selection_kind == "specialized" and source_catalog is None:
            candidate = path.parent / "SOURCES.md"
            source_catalog = str(candidate) if candidate.is_file() else None
        title, selection_reason, source_baseline = metadata
        items.append({
            "methodology_id": path.stem,
            "title": title,
            "path": str(path),
            "content_sha256": sha256(path.read_bytes()).hexdigest(),
            "selection_kind": selection_kind,
            "selection_reason": selection_reason,
            "source_baseline": source_baseline,
            "source_catalog_path": (
                source_catalog
                if selection_kind == "specialized"
                else str(frozen_manifest_path)
                if selection_kind == "user"
                else None
            ),
            "source_item_ids": (
                frozen_user.source_item_ids if frozen_user is not None else []
            ),
        })
    return {
        "unit_id": task.get("unit", {}).get("unit_id"),
        "items": items,
    }


def run_methodology_manifests(run_dir: str | Path) -> list[dict]:
    analysis_dir = Path(run_dir) / "agent-tasks" / "analysis"
    if not analysis_dir.is_dir():
        return []
    return [
        methodology_manifest(path)
        for path in sorted(analysis_dir.glob("*.json"))
    ]
