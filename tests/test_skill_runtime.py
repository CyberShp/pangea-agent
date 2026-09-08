from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pangea_agent.documents.source_snapshot import (
    create_source_snapshot,
    read_source_snapshot_manifest,
)
from pangea_agent.inventory.languages import detect_analysis_language
from pangea_agent.skill_runs import _language_profiles, validate_runtime_skill
from pangea_agent.skills import SKILL_VERSION, SOURCE_ROOT, skill_package_digest


class SkillRuntimeContractTests(unittest.TestCase):
    def test_runtime_skill_reports_exact_version_and_digest(self) -> None:
        runtime = validate_runtime_skill()

        self.assertEqual(runtime["version"], SKILL_VERSION)
        self.assertEqual(runtime["digest"], skill_package_digest(SOURCE_ROOT))
        self.assertRegex(runtime["digest"], r"^sha256:[0-9a-f]{64}$")

    def test_lua_openubmc_profile_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "openubmc" / "services"
            source.mkdir(parents=True)
            (source / "health.lua").write_text("return true\n", encoding="utf-8")

            profiles = _language_profiles(root, [])

        self.assertEqual(profiles["languages"], ["lua"])
        self.assertEqual(profiles["profiles"], ["lua", "openubmc_lua"])
        self.assertEqual(profiles["status"], "detected")

    def test_mixed_scope_is_rejected_by_run_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (root / "plugin.lua").write_text("return {}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不支持.*C/C\\+\\+.*Lua"):
                _language_profiles(root, [])

    def test_mixed_scope_is_rejected_by_inventory_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            (root / "plugin.lua").write_text("return {}\n", encoding="utf-8")

            repositories = [{"source_root": str(root)}]
            with self.assertRaisesRegex(ValueError, "不支持.*C/C\\+\\+.*Lua"):
                detect_analysis_language(repositories, [])

    def test_source_snapshot_is_a_single_copy_inventory_without_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            source = repository / "src"
            source.mkdir(parents=True)
            (source / "模块.c").write_text("int value = 1;\n", encoding="utf-8")
            snapshot_root = root / "run" / "inputs" / "source"

            manifest = create_source_snapshot(
                repository,
                [{"raw": "src", "verified": str(source)}],
                snapshot_root,
                repo_id="repo",
                run_id="run-01",
            )

            self.assertEqual(manifest["schema_version"], "2.0")
            self.assertEqual(manifest["file_count"], 1)
            self.assertEqual(manifest["total_bytes"], len("int value = 1;\n".encode("utf-8")))
            self.assertIsInstance(manifest["snapshot_duration_ms"], int)
            self.assertNotIn("snapshot_digest", manifest)
            self.assertNotIn("sha256", manifest["files"][0])
            self.assertEqual(
                (snapshot_root / "repository" / "src" / "模块.c").read_text(encoding="utf-8"),
                "int value = 1;\n",
            )
            self.assertEqual(
                read_source_snapshot_manifest(snapshot_root, run_id="run-01", repo_id="repo")["file_count"],
                1,
            )

    def test_ack_core_all_records_the_three_bootstrap_rules_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "run"
            source = root / "source"
            source.mkdir()
            script = SOURCE_ROOT / "scripts" / "run_guard.py"
            common = [sys.executable, str(script)]
            subprocess.run([
                *common, "init",
                "--skill-root", str(SOURCE_ROOT),
                "--workspace", str(workspace),
                "--source-raw", str(source),
                "--source-verified", str(source),
                "--scenario", "module-analysis",
                "--mode", "depth",
            ], check=True, capture_output=True, text=True, encoding="utf-8")

            completed = subprocess.run([
                *common, "ack-core", "--workspace", str(workspace), "--all",
            ], check=True, capture_output=True, text=True, encoding="utf-8")
            state = json.loads((workspace / "内部索引" / "运行状态.json").read_text(encoding="utf-8"))

        self.assertEqual(json.loads(completed.stdout)["ack"], [
            "path-fidelity", "evidence-consumption", "narrative-first",
        ])
        self.assertEqual(set(state["core_rules_ack"]), {
            "path-fidelity", "evidence-consumption", "narrative-first",
        })


if __name__ == "__main__":
    unittest.main()
