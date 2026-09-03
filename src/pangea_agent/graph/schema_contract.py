from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.models.analysis import (
    ComparisonReviewResult,
    IndependentReviewResult,
    PlanningResult,
    UnitSemanticResult,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONSTRAINT_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "const",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "pattern",
    "format",
    "oneOf",
    "anyOf",
    "allOf",
    "not",
}


@dataclass(frozen=True)
class ContractSpec:
    contract_id: str
    schema_filename: str
    model: type[BaseModel]
    skeleton_filename: str | None = None
    example_filename: str | None = None


CONTRACT_SPECS: tuple[ContractSpec, ...] = (
    ContractSpec(
        "planning-result-v1",
        "planning_result.schema.json",
        PlanningResult,
        "planning_result.skeleton.json",
        "planning_result.example.json",
    ),
    ContractSpec(
        "analysis-result-v2",
        "analysis_result.schema.json",
        UnitSemanticResult,
        "analysis_result.skeleton.json",
        "analysis_result.example.json",
    ),
    ContractSpec(
        "independent-review-result-v1",
        "independent_review_result.schema.json",
        IndependentReviewResult,
        "independent_review_result.skeleton.json",
    ),
    ContractSpec(
        "comparison-review-result-v1",
        "comparison_review_result.schema.json",
        ComparisonReviewResult,
        "comparison_review_result.skeleton.json",
    ),
)


def _resolve_ref(document: dict[str, Any], node: Any) -> Any:
    if not isinstance(node, dict) or "$ref" not in node:
        return node
    reference = node["$ref"]
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise ValueError(f"不支持的 Schema 引用：{reference!r}")
    value: Any = document
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _sort_json_values(values: list[Any]) -> list[Any]:
    return sorted(values, key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True))


def _behavioral_schema(document: dict[str, Any], node: Any, seen: set[str] | None = None) -> Any:
    seen = set() if seen is None else seen
    if isinstance(node, dict) and "$ref" in node:
        reference = node["$ref"]
        if reference in seen:
            return {"$ref": reference}
        return _behavioral_schema(document, _resolve_ref(document, node), {*seen, reference})
    if isinstance(node, list):
        return [_behavioral_schema(document, item, seen) for item in node]
    if not isinstance(node, dict):
        return node

    result: dict[str, Any] = {}
    for key in _CONSTRAINT_KEYS:
        if key not in node:
            continue
        value = node[key]
        if key == "properties":
            result[key] = {
                name: _behavioral_schema(document, child, seen)
                for name, child in value.items()
            }
        elif key in {"items", "not"}:
            result[key] = _behavioral_schema(document, value, seen)
        elif key in {"oneOf", "anyOf", "allOf"}:
            alternatives = [_behavioral_schema(document, child, seen) for child in value]
            result[key] = sorted(
                alternatives,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        elif key == "required":
            result[key] = sorted(value)
        elif key == "enum":
            result[key] = _sort_json_values(value)
        else:
            result[key] = value
    return result


def verify_schema_contract_equivalence(
    model: type[BaseModel],
    checked_schema: dict[str, Any],
) -> None:
    """Raise when a checked-in JSON Schema changes model validation behavior."""

    generated = model.model_json_schema()
    expected = _behavioral_schema(generated, generated)
    actual = _behavioral_schema(checked_schema, checked_schema)
    if expected != actual:
        raise ValueError(
            "Schema/Pydantic contract mismatch for "
            f"{model.__module__}.{model.__name__}"
        )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_node(document: dict[str, Any], node: Any) -> dict[str, Any]:
    resolved = _resolve_ref(document, node)
    return resolved if isinstance(resolved, dict) else {}


def _describe_type(document: dict[str, Any], node: Any) -> str:
    resolved = _schema_node(document, node)
    if "const" in resolved:
        return f"constant {json.dumps(resolved['const'], ensure_ascii=False)}"
    if "enum" in resolved:
        values = " | ".join(json.dumps(item, ensure_ascii=False) for item in resolved["enum"])
        return f"one of {values}"
    alternatives = resolved.get("anyOf")
    if alternatives:
        non_null = [item for item in alternatives if _schema_node(document, item).get("type") != "null"]
        has_null = len(non_null) != len(alternatives)
        if len(non_null) == 1:
            description = _describe_type(document, non_null[0])
            return f"{description}, null allowed" if has_null else description
        return " | ".join(_describe_type(document, item) for item in alternatives)
    schema_type = resolved.get("type", "value")
    if schema_type == "array":
        item_type = _describe_type(document, resolved.get("items", {}))
        description = f"array[{item_type}]"
        if "minItems" in resolved:
            description += f", minItems={resolved['minItems']}"
        if "maxItems" in resolved:
            description += f", maxItems={resolved['maxItems']}"
        return description
    if schema_type == "string":
        description = "string"
        if "minLength" in resolved:
            description += f", minLength={resolved['minLength']}"
        if "maxLength" in resolved:
            description += f", maxLength={resolved['maxLength']}"
        return description
    return str(schema_type)


def contract_card_from_schema(schema: dict[str, Any]) -> str:
    """Render a compact field/type card from a validated JSON Schema."""

    sections = [
        "# MACHINE-VALIDATED RESULT CONTRACT",
        "",
        "This contract is blocking and case-sensitive.",
        "Unknown fields are forbidden unless explicitly listed.",
        "Samples are illustrative; field names and types are exact.",
        "",
    ]
    root_title = schema.get("title", "Result")
    objects: list[tuple[str, dict[str, Any]]] = [(f"Root: {root_title}", schema)]
    for name, definition in schema.get("$defs", {}).items():
        resolved = _schema_node(schema, definition)
        if resolved.get("type") == "object":
            objects.append((name, resolved))

    for title, node in objects:
        sections.append(f"## {title}")
        required = set(node.get("required", []))
        sections.append("Required:")
        for name, field in node.get("properties", {}).items():
            if name in required:
                description = _describe_type(schema, field)
                sections.append(f"- {name}: {description}")
        optional = [
            (name, field)
            for name, field in node.get("properties", {}).items()
            if name not in required
        ]
        if optional:
            sections.append("Optional:")
            for name, field in optional:
                sections.append(f"- {name}: {_describe_type(schema, field)}")
        additional = node.get("additionalProperties", True)
        sections.append(
            "Additional fields: forbidden."
            if additional is False
            else "Additional fields: allowed."
        )
        sections.append("")
    card = "\n".join(sections).rstrip() + "\n"
    if len(card.encode("utf-8")) > 16 * 1024:
        raise ValueError("Contract Card 超过 16 KB UTF-8 限制")
    return card


def _copy_optional(source: Path, destination: Path) -> str | None:
    if not source.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def _copy_required(source: Path, destination: Path) -> str:
    if not source.is_file():
        raise ValueError(f"冻结结果契约输入不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def freeze_run_contracts(
    run_directory: str | Path,
    run_id: str,
    *,
    planning_skeleton_path: str | Path | None = None,
) -> dict[str, dict[str, str | None]]:
    """Copy and manifest every result contract used by a newly created Run."""

    run_directory = Path(run_directory)
    references: dict[str, dict[str, str | None]] = {}
    for spec in CONTRACT_SPECS:
        source_schema = PROJECT_ROOT / "schemas" / spec.schema_filename
        checked_schema = read_json(source_schema)
        verify_schema_contract_equivalence(spec.model, checked_schema)
        destination_root = run_directory / "contracts" / spec.contract_id
        destination_root.mkdir(parents=True, exist_ok=True)
        schema_path = destination_root / "schema.json"
        _copy_required(source_schema, schema_path)

        if spec.contract_id == "planning-result-v1" and planning_skeleton_path:
            skeleton_source = Path(planning_skeleton_path)
        else:
            skeleton_source = (
                PROJECT_ROOT / "schemas" / spec.skeleton_filename
                if spec.skeleton_filename
                else None
            )
        if spec.contract_id == "planning-result-v1" and skeleton_source:
            if not skeleton_source.is_file() and planning_skeleton_path is None:
                skeleton_path = destination_root / "skeleton.json"
                write_json(skeleton_path, {
                    "schema_version": "2.0",
                    "summary": "<非空规划摘要>",
                    "units": [],
                    "source_ownership": {},
                    "unresolved": [],
                })
                skeleton_path = str(skeleton_path)
            else:
                skeleton_path = _copy_required(
                    skeleton_source,
                    destination_root / "skeleton.json",
                )
        elif skeleton_source:
            skeleton_path = _copy_required(
                skeleton_source,
                destination_root / "skeleton.json",
            )
        else:
            skeleton_path = None
        example_source = (
            PROJECT_ROOT / "schemas" / spec.example_filename
            if spec.example_filename
            else None
        )
        example_path = (
            _copy_optional(example_source, destination_root / "example.json")
            if example_source
            else None
        )

        card_path = destination_root / "contract-card.md"
        card_path.write_text(contract_card_from_schema(checked_schema), encoding="utf-8")
        manifest_path = destination_root / "manifest.json"
        manifest = {
            "contract_version": "1.0",
            "contract_id": spec.contract_id,
            "validator_model": f"{spec.model.__module__}.{spec.model.__name__}",
            "schema_sha256": sha256_file(schema_path),
            "skeleton_sha256": sha256_file(skeleton_path) if skeleton_path else None,
            "example_sha256": sha256_file(example_path) if example_path else None,
            "contract_card_sha256": sha256_file(card_path),
            "created_for_run_id": run_id,
        }
        write_json(manifest_path, manifest)
        references[spec.contract_id] = {
            "result_schema_path": str(schema_path),
            "result_skeleton_path": skeleton_path,
            "result_example_path": example_path,
            "result_contract_path": str(card_path),
            "result_contract_manifest_path": str(manifest_path),
        }
    references["closure-result-v1"] = references["analysis-result-v2"]
    return references


def frozen_contract_paths(run_directory: str | Path, contract_id: str) -> dict[str, str | None]:
    root = Path(run_directory) / "contracts" / contract_id
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Run 缺少冻结结果契约：{manifest_path}")
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("contract_id") != contract_id:
        raise ValueError(f"Run 结果契约 manifest 不匹配：{manifest_path}")
    for filename, hash_key in (
        ("schema.json", "schema_sha256"),
        ("skeleton.json", "skeleton_sha256"),
        ("example.json", "example_sha256"),
        ("contract-card.md", "contract_card_sha256"),
    ):
        expected_hash = manifest.get(hash_key)
        artifact = root / filename
        if expected_hash is None:
            if artifact.exists():
                raise ValueError(f"Run 结果契约 manifest 多出 Artifact：{artifact}")
            continue
        if not artifact.is_file() or sha256_file(artifact) != expected_hash:
            raise ValueError(f"Run 冻结结果契约完整性校验失败：{artifact}")
    return {
        "result_schema_path": str(root / "schema.json"),
        "result_skeleton_path": str(root / "skeleton.json") if (root / "skeleton.json").is_file() else None,
        "result_example_path": str(root / "example.json") if (root / "example.json").is_file() else None,
        "result_contract_path": str(root / "contract-card.md"),
        "result_contract_manifest_path": str(manifest_path),
    }
