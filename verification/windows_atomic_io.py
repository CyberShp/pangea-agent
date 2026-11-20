"""Windows transient file-lock retries for atomic file replacement and JSON reads.

Verifies bounded retries for WinError 5/32/33 without rewriting temp payloads,
fail-fast behavior for non-transient or POSIX errors, temp file cleanup, and
(when executed on Windows) a real Win32 handle-lock collision test.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pangea_agent.agent_io import atomic_replace, read_json, write_json
from pangea_agent.agent_io import files as agent_files


def _win_permission_error(winerror: int, message: str = "Access is denied") -> PermissionError:
    exc = PermissionError(13, message)
    exc.winerror = winerror
    return exc


class WindowsAtomicIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pangea-atomic-io-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_transient_lock_classification(self) -> None:
        with patch.object(agent_files, "_IS_WINDOWS", True):
            for code in (5, 32, 33):
                self.assertTrue(agent_files._is_windows_transient_lock(_win_permission_error(code)))
            non_transient = PermissionError(13, "Write protected")
            non_transient.winerror = 19
            self.assertFalse(agent_files._is_windows_transient_lock(non_transient))
            self.assertFalse(agent_files._is_windows_transient_lock(FileNotFoundError("missing")))
        with patch.object(agent_files, "_IS_WINDOWS", False):
            self.assertFalse(agent_files._is_windows_transient_lock(_win_permission_error(5)))

    def test_write_json_retries_transient_winerrors_without_rewriting_temp(self) -> None:
        target = self.root / "progress.json"
        write_json(target, {"stage": "planning"})

        real_replace = os.replace
        seen_sources: list[str] = []
        sleeps: list[float] = []
        errors = [
            _win_permission_error(5, "拒绝访问"),
            _win_permission_error(32, "另一个程序正在使用此文件"),
            _win_permission_error(33, "进程无法访问文件"),
        ]

        def flaky_replace(src, dst):
            seen_sources.append(str(src))
            if errors:
                raise errors.pop(0)
            return real_replace(src, dst)

        with (
            patch.object(agent_files, "_IS_WINDOWS", True),
            patch.object(agent_files.os, "replace", side_effect=flaky_replace),
            patch.object(agent_files.time, "sleep", side_effect=sleeps.append),
        ):
            write_json(target, {"stage": "analyzing", "units": 14})

        self.assertEqual(read_json(target), {"stage": "analyzing", "units": 14})
        self.assertEqual(len(seen_sources), 4)
        self.assertEqual(len(set(seen_sources)), 1, "must retry the same temp file without rewriting")
        self.assertEqual(sleeps, list(agent_files._WINDOWS_REPLACE_RETRY_DELAYS[:3]))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_write_json_raises_last_error_and_cleans_temp_when_exhausted(self) -> None:
        target = self.root / "progress.json"
        write_json(target, {"stage": "planning"})

        sleeps: list[float] = []
        last_error = _win_permission_error(5, "still locked")

        def always_locked(src, dst):
            raise last_error

        with (
            patch.object(agent_files, "_IS_WINDOWS", True),
            patch.object(agent_files.os, "replace", side_effect=always_locked),
            patch.object(agent_files.time, "sleep", side_effect=sleeps.append),
        ):
            with self.assertRaises(PermissionError) as raised:
                write_json(target, {"stage": "analyzing"})

        self.assertIs(raised.exception, last_error)
        self.assertEqual(sleeps, list(agent_files._WINDOWS_REPLACE_RETRY_DELAYS))
        self.assertEqual(read_json(target), {"stage": "planning"})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_write_json_does_not_retry_on_posix_or_non_transient_oserror(self) -> None:
        target = self.root / "progress.json"
        sleeps: list[float] = []

        with (
            patch.object(agent_files, "_IS_WINDOWS", False),
            patch.object(agent_files.os, "replace", side_effect=PermissionError(13, "EACCES")),
            patch.object(agent_files.time, "sleep", side_effect=sleeps.append),
        ):
            with self.assertRaises(PermissionError):
                write_json(target, {"stage": "analyzing"})
        self.assertEqual(sleeps, [])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

        disk_full = OSError(28, "No space left")
        disk_full.winerror = 112
        with (
            patch.object(agent_files, "_IS_WINDOWS", True),
            patch.object(agent_files.os, "replace", side_effect=disk_full),
            patch.object(agent_files.time, "sleep", side_effect=sleeps.append),
        ):
            with self.assertRaises(OSError):
                write_json(target, {"stage": "analyzing"})
        self.assertEqual(sleeps, [])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_read_json_retries_transient_winerror_and_fails_fast_on_decode_error(self) -> None:
        target = self.root / "progress.json"
        write_json(target, {"lifecycle_status": "running"})

        real_read_text = Path.read_text
        errors = [_win_permission_error(5), _win_permission_error(32)]
        sleeps: list[float] = []

        def flaky_read_text(path_self, *args, **kwargs):
            if errors:
                raise errors.pop(0)
            return real_read_text(path_self, *args, **kwargs)

        with (
            patch.object(agent_files, "_IS_WINDOWS", True),
            patch.object(Path, "read_text", autospec=True, side_effect=flaky_read_text),
            patch.object(agent_files.time, "sleep", side_effect=sleeps.append),
        ):
            self.assertEqual(read_json(target), {"lifecycle_status": "running"})
        self.assertEqual(sleeps, list(agent_files._WINDOWS_READ_RETRY_DELAYS[:2]))

        bad = self.root / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        sleeps.clear()
        with (
            patch.object(agent_files, "_IS_WINDOWS", True),
            patch.object(agent_files.time, "sleep", side_effect=sleeps.append),
        ):
            with self.assertRaises(ValueError):
                read_json(bad)
        self.assertEqual(sleeps, [])

    def test_atomic_replace_directory_promotion(self) -> None:
        staging = self.root / ".staging"
        staging.mkdir()
        (staging / "file.txt").write_text("ok", encoding="utf-8")
        dest = self.root / "final"
        atomic_replace(staging, dest)
        self.assertFalse(staging.exists())
        self.assertEqual((dest / "file.txt").read_text(encoding="utf-8"), "ok")

    @unittest.skipUnless(os.name == "nt", "Real Win32 CreateFileW handle lock requires Windows")
    def test_real_windows_concurrent_reader_lock(self) -> None:
        import ctypes

        target = self.root / "progress.json"
        write_json(target, {"version": 1})

        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 0x00000001
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            str(target),
            GENERIC_READ,
            FILE_SHARE_READ,  # Deliberately omit FILE_SHARE_DELETE
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        self.assertNotEqual(handle, INVALID_HANDLE_VALUE)

        def release_soon() -> None:
            time.sleep(0.2)
            kernel32.CloseHandle(handle)

        worker = threading.Thread(target=release_soon, daemon=True)
        worker.start()
        try:
            write_json(target, {"version": 2})
        finally:
            worker.join(timeout=2.0)

        self.assertEqual(read_json(target), {"version": 2})
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
