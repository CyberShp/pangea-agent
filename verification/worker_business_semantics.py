"""验证合成夹具及 C/Lua 行为一致性；不执行模型或评判生成用例语义。"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "verification/fixtures/worker-business-semantics"


def oracle():
    return json.loads((FIXTURE / "oracle.json").read_text(encoding="utf-8"))


class SyntheticFixtureContract(unittest.TestCase):
    def test_generator_inputs_exclude_acceptance_answers(self):
        spec = oracle()
        self.assertTrue(spec["synthetic"])
        self.assertEqual(set(spec["generator_inputs"]), {"c", "lua"})
        for language, files in spec["generator_inputs"].items():
            self.assertEqual(files, ["CONTRACT.md", f"queue.{language}"])
            for name in files:
                self.assertTrue((FIXTURE / name).is_file())
                self.assertNotIn("oracle.json", name)
            # Keep each source task small enough for a bounded model comparison.
            self.assertLess(sum((FIXTURE / name).stat().st_size for name in files), 14000)

    def test_acceptance_references_are_present_in_both_languages(self):
        spec = oracle()
        identities = [item["id"] for item in spec["behaviors"]]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len(identities), 7)
        for item in spec["behaviors"]:
            with self.subTest(behavior=item["id"]):
                self.assertTrue(item["requirement"])
                self.assertTrue(item["acceptance"])
                self.assertEqual(set(item["evidence"]), {"CONTRACT.md", "queue.c", "queue.lua"})
                for name, quote in item["evidence"].items():
                    self.assertIn(quote, (FIXTURE / name).read_text(encoding="utf-8"))

    def test_smoke_commands_have_independent_expected_transcripts(self):
        scenarios = oracle()["smoke_scenarios"]
        ids = [scenario["id"] for scenario in scenarios]
        self.assertEqual(len(ids), len(set(ids)))
        for scenario in scenarios:
            self.assertEqual(len(scenario["args"]), len(scenario["stdout"]))
            self.assertTrue(all(isinstance(arg, str) and arg for arg in scenario["args"]))


class SyntheticFixtureExecution(unittest.TestCase):
    def check_transcripts(self, command):
        for scenario in oracle()["smoke_scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                result = subprocess.run(
                    [*command, *scenario["args"]], cwd=FIXTURE,
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), scenario["stdout"])

    def test_c_fixture(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("无 C 编译器，未执行 C 合成夹具")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "queue-demo.exe"
            result = subprocess.run(
                [compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", str(FIXTURE / "queue.c"), "-o", str(executable)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.check_transcripts([str(executable)])

    def test_lua_fixture(self):
        interpreter = next((path for name in ("lua", "lua5.4", "lua5.3", "luajit")
                            if (path := shutil.which(name))), None)
        if interpreter is not None:
            self.check_transcripts([interpreter, str(FIXTURE / "queue.lua")])
            return
        # Optional local verification dependency, never part of the product.
        try:
            from lupa.lua54 import LuaRuntime
        except ImportError:
            self.skipTest("无 Lua 解释器或 Lupa，未执行 Lua 合成夹具；静态检查不等于执行通过")
        source = (FIXTURE / "queue.lua").read_text(encoding="utf-8")
        for scenario in oracle()["smoke_scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                runtime = LuaRuntime()
                output = []
                runtime.globals().arg = runtime.table_from(scenario["args"])
                runtime.globals().print = lambda value: output.append(str(value))
                runtime.execute(source)
                self.assertEqual(output, scenario["stdout"])


if __name__ == "__main__":
    print("仅验证合成夹具、引文和局部程序行为；不证明模型首轮用例质量或产品真实运行结果。", flush=True)
    unittest.main(verbosity=2)
