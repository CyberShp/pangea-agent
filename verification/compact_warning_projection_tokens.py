"""Projection upgrades invalidate old compact cursors without changing results."""
from functools import partial
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import observability_recovery as recovery
from flow_path_diagnostics import flow
from pangea_agent.cli import source_first_api as api
from pangea_agent.cli.adapter_api import settle_action
from pangea_agent.graph import result_store as store


class CompactWarningTokens(unittest.TestCase):
    def setUp(self):
        self.harness = recovery.RecoveryChecks(methodName="test_plan_counts_saved_progress_and_polling_are_facts")
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        h = self.harness
        self.workers = {}
        for action in h.analysis:
            worker = self.workers[action] = h.bind(action)
            broken = flow()
            broken["paths"][0]["edge_ids"] = []
            first = api.result_write(str(h.root), h.run_id, action, worker, expected_revision=0,
                                     records=[dict(kind="flow", body=broken)])
            second = api.result_write(str(h.root), h.run_id, action, worker, expected_revision=1,
                                      records=[dict(kind="flow", body=broken, supersedes=first["record_ids"]),
                                               dict(kind="note", body="retained evidence " * 200)])
            api.work_finish(str(h.root), h.run_id, action, worker, revision=second["revision"])
            settle_action(str(h.root), h.run_id, action)
        self.review = next(a["action_id"] for a in h.progress()["actions"].values() if a["role"] == "review")
        self.reviewer = h.bind(self.review, "reviewer")

    def collect(self, read, **kwargs):
        items, fragments = [], {}
        token = None
        while True:
            page = read(page_token=token, max_chars=1000, **kwargs)
            items.extend(page["items"])
            fragment = page["item_fragment"]
            if fragment is not None:
                index = fragment["item_index"]
                previous = fragments.get(index, "")
                self.assertEqual(fragment["char_start"], len(previous))
                self.assertEqual(fragment["char_end"] - fragment["char_start"], len(fragment["text"]))
                fragments[index] = previous + fragment["text"]
                if fragment["complete"]:
                    items.append(json.loads(fragments.pop(index)))
            token = page["next_page_token"]
            if token is None:
                break
        self.assertFalse(fragments)
        return items, page

    def check_projection_tokens(self, read, pager_module):
        h = self.harness
        paths = [Path(h.task(action)["result_path"]) for action in h.analysis]
        paths.append(h.run / "progress.json")
        original_bytes = [path.read_bytes() for path in paths]
        current_items = None
        for history in (False, True):
            with self.subTest(include_history=history):
                # Reproduce the pre-upgrade context at the same frozen revision.
                original_pager = store.compact_items_page
                def legacy_page(**kwargs):
                    context = dict(kwargs["token_context"])
                    self.assertEqual(context.pop("warning_projection_version"), store.WARNING_PROJECTION_VERSION)
                    return original_pager(**{**kwargs, "token_context": context})

                with patch.object(pager_module, "compact_items_page", side_effect=legacy_page):
                    old = read(include_history=history, max_chars=1000)
                self.assertIsNotNone(old["next_page_token"])
                with self.assertRaisesRegex(store.ResultStoreError, "读取条件或 revision 不一致.*从头读取"):
                    read(include_history=history, page_token=old["next_page_token"], max_chars=1000)
                items, page = self.collect(read, include_history=history)
                whole = read(include_history=history, max_chars=24000)
                self.assertIsNone(whole["next_page_token"])
                self.assertEqual(items, whole["items"])
                records = [item for item in items if item["item_type"] == "record"]
                warnings = [item for item in items if item["item_type"] == "warning"]
                self.assertEqual(len(warnings), page["warning_count"])
                self.assertTrue(warnings)
                self.assertTrue(any(item["item_type"] == "completion" for item in items))
                self.assertEqual(len(records), page["total_record_count"] if history else page["active_record_count"])
                if history:
                    self.assertGreater(len(items), len(current_items))
                    self.assertTrue(any(item["value"].get("record_id") == "rec-000001" for item in warnings))
                else:
                    current_items = items
                    self.assertTrue(all(item["value"].get("record_id") != "rec-000001" for item in warnings))
        self.assertEqual([path.read_bytes() for path in paths], original_bytes)

    def test_result_read_rejects_old_cursor_and_preserves_current_and_history(self):
        h = self.harness
        action = h.analysis[0]
        read = partial(api.result_read, str(h.root), h.run_id, action, self.workers[action], view="compact")
        self.check_projection_tokens(read, store)

    def test_comparison_read_rejects_old_cursor_and_preserves_current_and_history(self):
        h = self.harness
        read = partial(api.comparison_read, str(h.root), h.run_id, self.review, self.reviewer,
                       version_set_id=h.task(self.review)["version_set_id"], view="compact")
        self.check_projection_tokens(read, api)
        items, _ = self.collect(read)
        records = [item for item in items if item["item_type"] == "record"]
        self.assertGreater(len({(item["action_id"], item["value"]["record_id"]) for item in records}),
                           len({item["value"]["record_id"] for item in records}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
