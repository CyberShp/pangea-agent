"""Best-effort host timings, independent of Run state.

Writes use a nonblocking file lock; a busy or unavailable log drops that
observation. Missing events therefore never prove inactivity or completion.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


def _append(data_root: str, run_id: str, event: dict) -> None:
    try:
        root = (Path(data_root).resolve() / "runs").resolve()
        directory = (root / run_id).resolve()
        if directory.parent != root or not directory.is_dir():
            return
        target = directory / "runtime-timing.jsonl"
        if target.resolve().parent != directory:
            return
        payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        with target.open("ab") as stream:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                stream.write(payload)
                stream.flush()
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError):
        # Observability must not change a successful write or mask its error.
        pass


@contextmanager
def runtime_timing(data_root: str, run_id: str, identity: dict, operation: str, phase: str):
    """Callers supply only bound IDs and fixed phase names, never tool content."""
    started = perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()

    def emit(status: str, **extra):
        _append(data_root, run_id, {
            "schema_version": 1, "run_id": run_id, **identity,
            "operation": operation, "phase": phase, "status": status,
            "at": datetime.now(timezone.utc).isoformat(), "started_at": started_at,
            "duration_clock": "monotonic", **extra,
        })

    emit("start")
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        emit(status, duration_ms=round((perf_counter() - started) * 1000, 3))
