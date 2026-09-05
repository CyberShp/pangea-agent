from __future__ import annotations

import unittest

from pangea_agent.skill_runs import _skill_request


class AnalysisRequestTests(unittest.TestCase):
    def _raw(self, **overrides: object) -> dict:
        value = {
            "request_version": "2.0",
            "data_root": "pangea-data",
            "repository": "repo",
            "target": "认证恢复",
            "source_scope": ["src"],
            "asset_ids": [],
        }
        value.update(overrides)
        return value

    def test_preserves_scenario_and_mode_with_depth_defaults(self) -> None:
        request = _skill_request(self._raw())

        self.assertEqual(request["scenario"], "module-analysis")
        self.assertEqual(request["mode"], "depth")

        request = _skill_request(self._raw(scenario="root-cause", mode="speed"))
        self.assertEqual(request["scenario"], "root-cause")
        self.assertEqual(request["mode"], "speed")

    def test_rejects_an_unsupported_scenario_or_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "scenario"):
            _skill_request(self._raw(scenario="unknown"))
        with self.assertRaisesRegex(ValueError, "mode"):
            _skill_request(self._raw(mode="preview"))


if __name__ == "__main__":
    unittest.main()
