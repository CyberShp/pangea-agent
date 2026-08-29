from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from pangea_agent.agent_io import read_json


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


def methodology_manifest(task_path: str | Path) -> dict:
    """Describe rubrics already frozen in one task without affecting selection."""
    path_to_task = Path(task_path)
    if not path_to_task.is_file():
        return {"unit_id": None, "items": []}
    task = read_json(path_to_task)
    source_catalog = None
    items = []
    for raw_path in task.get("rubric_paths", []):
        path = Path(raw_path)
        metadata = GENERAL_METHODOLOGIES.get(path.name)
        selection_kind = "general"
        if metadata is None:
            metadata = SPECIALIZED_METHODOLOGIES.get(path.name)
            selection_kind = "specialized"
        if not path.is_file():
            continue
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
                source_catalog if selection_kind == "specialized" else None
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
