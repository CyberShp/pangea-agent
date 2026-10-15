"""Asset stop/archive race regressions: python verification/asset_lifecycle.py."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from pangea_agent.agent_io import read_json, write_json
from pangea_agent import assets
from pangea_agent.assets import (
    archive_asset, import_asset, load_asset, load_asset_action,
    prepare_asset_extraction, stop_asset,
)
from pangea_agent.cli.adapter_api import bind_asset_action, settle_asset_action


class AssetLifecycleChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pangea-asset-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = str(self.root / "data")
        self.source = self.root / "requirement.txt"
        self.source.write_text("连接成功后显示设备。", encoding="utf-8")
        self.asset = import_asset(self.data, str(self.source), "requirement")
        self.asset_id = self.asset.asset_id

    def prepare(self, *, bind=True):
        prepared = prepare_asset_extraction(self.data, self.asset_id)
        action = prepared["action"]
        if bind:
            bind_asset_action(self.data, self.asset_id, action["action_id"], "original-worker")
        task = read_json(Path(action["task_path"]))
        write_json(Path(task["result_path"]), {
            "asset_id": self.asset_id, "summary": "已提取原文要求", "items": [{
                "item_type": "requirement", "item_id": "R1", "title": "连接显示",
                "acceptance_criteria": ["连接成功后显示设备"],
                "source_references": [{"path": self.asset.source_path, "location": "第 1 行"}],
            }],
        })
        return action, task

    def test_stop_persists_and_fences_late_settle_without_deleting_output(self):
        action, task = self.prepare()
        result_path = Path(task["result_path"])
        before = result_path.read_bytes()
        stopped = stop_asset(self.data, self.asset_id, action_id=action["action_id"])
        self.assertEqual(stopped.status, "imported")
        self.assertEqual(load_asset_action(self.data, self.asset_id).status, "paused")
        with self.assertRaisesRegex(ValueError, "绑定真实 Agent"):
            settle_asset_action(self.data, self.asset_id, action["action_id"])
        self.assertEqual(load_asset(self.data, self.asset_id).status, "imported")
        self.assertEqual(result_path.read_bytes(), before)
        self.assertTrue(Path(self.asset.source_path).is_file())
        self.assertEqual(stop_asset(self.data, self.asset_id).status, "imported")

    def test_pending_action_cannot_bind_after_stop(self):
        action, _ = self.prepare(bind=False)
        stop_asset(self.data, self.asset_id)
        with self.assertRaisesRegex(ValueError, "status=paused"):
            bind_asset_action(self.data, self.asset_id, action["action_id"], "late-worker")

    def test_stale_review_cannot_restore_an_archived_asset(self):
        record = load_asset(self.data, self.asset_id)
        record.asset_type = "historical_defect"
        record.status = "awaiting_review"
        record.review_status = "pending"
        assets._save_record(self.data, record)
        archive_asset(self.data, self.asset_id)
        with self.assertRaisesRegex(ValueError, "没有等待审核"):
            assets.review_asset(self.data, self.asset_id, "approve")
        self.assertEqual(load_asset(self.data, self.asset_id).status, "archived")

    def test_partial_stop_catalog_write_does_not_reuse_fenced_attempt(self):
        action, _ = self.prepare()
        with patch.object(assets, "_save_record", side_effect=OSError("interrupted catalog write")):
            with self.assertRaises(OSError):
                stop_asset(self.data, self.asset_id)
        self.assertEqual(load_asset(self.data, self.asset_id).status, "extracting")
        self.assertEqual(load_asset_action(self.data, self.asset_id).status, "paused")
        recovered = prepare_asset_extraction(self.data, self.asset_id)
        self.assertNotEqual(recovered["action"]["action_id"], action["action_id"])
        self.assertEqual(recovered["action"]["status"], "pending")

    def test_restart_gets_new_attempt_and_stale_stop_cannot_touch_it(self):
        old, old_task = self.prepare()
        stop_asset(self.data, self.asset_id)
        new = prepare_asset_extraction(self.data, self.asset_id)
        self.assertNotEqual(new["action"]["action_id"], old["action_id"])
        self.assertNotEqual(new["action"]["task_path"], old["task_path"])
        self.assertTrue(Path(old_task["result_path"]).is_file())
        self.assertEqual(read_json(Path(new["action"]["task_path"]).parent / "previous-action.json")["status"], "paused")
        with self.assertRaisesRegex(ValueError, "当前提取任务"):
            stop_asset(self.data, self.asset_id, action_id=old["action_id"])
        with self.assertRaisesRegex(ValueError, "不属于资产"):
            settle_asset_action(self.data, self.asset_id, old["action_id"])
        self.assertEqual(load_asset(self.data, self.asset_id).status, "extracting")
        self.assertEqual(load_asset_action(self.data, self.asset_id).status, "pending")

    def test_archive_fences_late_result_and_preserves_original_files(self):
        action, task = self.prepare()
        before = Path(task["result_path"]).read_bytes()
        archive_asset(self.data, self.asset_id)
        self.assertEqual(load_asset_action(self.data, self.asset_id).status, "paused")
        with self.assertRaises(ValueError):
            settle_asset_action(self.data, self.asset_id, action["action_id"])
        self.assertEqual(stop_asset(self.data, self.asset_id).status, "archived")
        self.assertEqual(Path(task["result_path"]).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "已归档"):
            prepare_asset_extraction(self.data, self.asset_id, restart=True)

    def test_completed_asset_is_not_reverted_by_stop_or_replayed_settle(self):
        action, _ = self.prepare()
        settled = settle_asset_action(self.data, self.asset_id, action["action_id"])
        self.assertEqual(settled["asset"]["status"], "available")
        before = load_asset(self.data, self.asset_id).model_dump()
        self.assertEqual(stop_asset(self.data, self.asset_id).model_dump(), before)
        archive_asset(self.data, self.asset_id)
        replay = settle_asset_action(self.data, self.asset_id, action["action_id"])
        self.assertEqual(replay["asset"]["status"], "archived")

    def test_settle_and_archive_are_one_serial_transaction(self):
        action, _ = self.prepare()
        entered = threading.Event()
        release = threading.Event()
        original = assets._accept_extraction_result

        def delayed_accept(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test barrier timed out")
            return original(*args)

        with patch.object(assets, "_accept_extraction_result", delayed_accept), ThreadPoolExecutor(max_workers=2) as executor:
            settled = executor.submit(settle_asset_action, self.data, self.asset_id, action["action_id"])
            self.assertTrue(entered.wait(5))
            archived = executor.submit(archive_asset, self.data, self.asset_id)
            try:
                with self.assertRaises(TimeoutError):
                    archived.result(timeout=0.1)
            finally:
                release.set()
            self.assertEqual(settled.result(timeout=5)["asset"]["status"], "available")
            self.assertEqual(archived.result(timeout=5).status, "archived")
        self.assertEqual(load_asset(self.data, self.asset_id).status, "archived")

    def test_stop_waits_for_other_cli_process_transaction(self):
        action, _ = self.prepare()
        script = (
            "from pathlib import Path\n"
            "import sys\n"
            "from pangea_agent.assets import _mutation_lock\n"
            "with _mutation_lock(Path(sys.argv[1])):\n"
            " print('locked', flush=True)\n"
            " sys.stdin.readline()\n"
        )
        lock = self.root / "data" / "assets" / self.asset_id / ".mutation.lock"
        with subprocess.Popen([sys.executable, "-c", script, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as child:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with ThreadPoolExecutor(max_workers=1) as executor:
                stopped = executor.submit(stop_asset, self.data, self.asset_id, action_id=action["action_id"])
                try:
                    with self.assertRaises(TimeoutError):
                        stopped.result(timeout=0.1)
                finally:
                    child.stdin.write("release\n")
                    child.stdin.flush()
                self.assertEqual(stopped.result(timeout=5).status, "imported")
            self.assertEqual(child.wait(timeout=5), 0)

    def test_concurrent_imports_allocate_distinct_assets_and_preserve_each_source(self):
        script = (
            "from pangea_agent.assets import import_asset\n"
            "import sys\n"
            "print(import_asset(sys.argv[1], sys.argv[2], 'requirement', sys.argv[3]).asset_id)\n"
        )
        children = [subprocess.Popen([sys.executable, "-c", script, self.data, str(self.source), f"file-{i}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
        ids = []
        for i, child in enumerate(children):
            stdout, stderr = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, stderr)
            asset_id = stdout.strip()
            ids.append(asset_id)
            record = load_asset(self.data, asset_id)
            self.assertEqual(record.title, f"file-{i}")
            self.assertEqual(Path(record.source_path).read_bytes(), self.source.read_bytes())
        self.assertEqual(len(set(ids)), 4)
        self.assertNotIn(self.asset_id, ids)

    def test_cli_stop_returns_persisted_state(self):
        action, _ = self.prepare()
        result = subprocess.run([sys.executable, "-m", "pangea_agent.cli.main", "assets", "stop", "--data-root", self.data, "--asset-id", self.asset_id, "--action-id", action["action_id"]], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.splitlines()[-1])
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["result"]["status"], "imported")
        self.assertEqual(load_asset_action(self.data, self.asset_id).status, "paused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
