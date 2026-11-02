"""语料/装载/结构验证，不执行模型，不判断生成用例的黑盒语义质量。

运行：.venv/bin/python verification/blackbox_semantic_examples.py
语义正反标签是人工标注的评测资料，不是分类器；所有正反例均允许通过现有结构合同。
模型验证必须另行执行，给模型未标注候选与 context，人工检查字段级反证及定向修正。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

from pangea_agent.agent_io import write_json
from pangea_agent.analysis_scenarios import SCENES, scene_rubric_paths, scene_spec
from pangea_agent.graph.nodes.source_first import _freeze_source_first_rubrics
from pangea_agent.rubrics.loader import load_builtin_rubric
from pangea_agent.test_case_schema import check_test_case_structure


FIXTURE = Path(__file__).parent / "fixtures" / "blackbox-semantic-examples-v1.json"


def field_value(body, path):
    """仅解析本语料已标注的字段位置，不分类、不纠正文案。"""
    parts = path.replace("[", ".").replace("]", "").split(".")
    assert parts.pop(0) == "body"
    value = body
    for part in parts:
        value = value[int(part)] if part.isdecimal() else value[part]
    return value


class BlackboxExampleCorpus(unittest.TestCase):
    def setUp(self):
        self.corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_corpus_pairs_have_traceable_annotations(self):
        self.assertEqual(self.corpus["format_version"], "blackbox-semantic-examples-v1")
        self.assertEqual(set(self.corpus["verification_boundary"]),
                         {"automated", "model_semantics", "acceptance"})
        pairs = self.corpus["pairs"]
        ids = [pair["id"] for pair in pairs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {"internal-oracle", "empty-paraphrase", "unknown-skeleton",
                                    "graybox-injection", "missing-instrumentation", "public-api-boundary",
                                    "auth-mixed-oracle", "auth-automatic-exchange",
                                    "auth-message-public-state", "auth-resource-substitution"})
        for pair in pairs:
            with self.subTest(pair=pair["id"]):
                self.assertTrue(pair["context"])
                self.assertTrue(pair["repair_scope"])
                negative, positive = pair["negative"], pair["positive"]
                self.assertNotEqual(negative["record_id"], positive["record_id"])
                review = negative["expected_review"]
                self.assertEqual(review["disposition"], "finding")
                self.assertTrue(review["counterevidence"])
                self.assertTrue(review["correction"])
                self.assertTrue(review["field_paths"])
                for path in review["field_paths"]:
                    self.assertTrue(field_value(negative["body"], path))
                    self.assertNotEqual(field_value(negative["body"], path),
                                        field_value(positive["body"], path))
                self.assertEqual(positive["expected_review"]["disposition"], "pass")
                self.assertTrue(positive["expected_review"]["reason"])

    def test_structure_accepts_both_semantic_positive_and_negative_examples(self):
        # 此处刻意不把人工语义标签转为 Python 拒写逻辑。
        for pair in self.corpus["pairs"]:
            for side in ("negative", "positive"):
                with self.subTest(pair=pair["id"], side=side):
                    self.assertEqual(check_test_case_structure(pair[side]["body"]), [])

    def test_corpus_retains_objective_and_readiness_contrasts(self):
        positives = {pair["id"]: pair["positive"]["body"] for pair in self.corpus["pairs"]}
        self.assertEqual(positives["public-api-boundary"]["test_level"], "interface_contract")
        self.assertEqual(positives["graybox-injection"]["test_level"], "developer_assisted")
        self.assertEqual(positives["unknown-skeleton"]["execution_readiness"], "unknown")
        self.assertEqual(positives["missing-instrumentation"]["execution_readiness"], "needs_instrumentation")


class BlackboxRuleLoading(unittest.TestCase):
    def test_all_scenes_load_current_common_rules_and_do_not_refresh_frozen_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            frozen = _freeze_source_first_rubrics(run)
            write_json(run / "inputs/methodologies/manifest.json", {"enabled_user_methodologies": []})
            for scene in SCENES:
                for role, name in (("analysis", "scene_common_generation"),
                                   ("review", "scene_common_review")):
                    with self.subTest(scene=scene, role=role):
                        paths = scene_rubric_paths(run, scene_spec(scene), role, [])
                        self.assertIn(frozen[name], paths)
                        self.assertEqual(Path(frozen[name]).read_text(encoding="utf-8"),
                                         load_builtin_rubric(name + ".md"))
            old = Path(frozen["scene_common_generation"])
            old.write_text("旧 Run 冻结文本", encoding="utf-8")
            before = {path: Path(path).read_bytes() for path in frozen.values()}
            _freeze_source_first_rubrics(run)
            self.assertEqual({path: Path(path).read_bytes() for path in frozen.values()}, before)

    def test_documented_test_case_contract_remains_structurally_valid(self):
        text = load_builtin_rubric("scene_common_generation.md")
        contract = text.split("<!-- TEST-CASE-CONTRACT:BEGIN -->", 1)[1].split(
            "<!-- TEST-CASE-CONTRACT:END -->", 1)[0]
        examples = re.findall(r"```json\n(.*?)\n```", contract, re.DOTALL)
        self.assertEqual(len(examples), 1)
        self.assertEqual(check_test_case_structure(json.loads(examples[0])), [])


if __name__ == "__main__":
    print("仅验证语料、结构合同及规则装载；模型语义质量未由此脚本验证。", flush=True)
    unittest.main(verbosity=2)
