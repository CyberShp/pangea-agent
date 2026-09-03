from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
