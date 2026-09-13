"""Identify loaded runtime files without reading user source or credentials."""
from __future__ import annotations

import hashlib
import subprocess
import re
from pathlib import Path


def runtime_identity() -> dict:
    root = Path(__file__).resolve().parents[2]
    commit = None
    dirty = None
    try:
        top = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        if Path(top).resolve() == root:
            commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=3).decode().strip()
            dirty = bool(subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--quiet"], stderr=subprocess.DEVNULL, timeout=3).returncode)
    except (OSError, subprocess.SubprocessError):
        pass
    hashes = {}
    for directory, pattern in [("src/pangea_agent", "*.py"), (".agents/pangea", "*.md"), (".opencode/agents", "*.md"), (".opencode/plugins", "*.ts"), (".opencode/commands", "*.md"), (".opencode/skills", "*.md")]:
        for file in sorted((root / directory).rglob(pattern)):
            hashes[file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    try:
        project = (root / "pyproject.toml").read_text(encoding="utf-8").split("[project]", 1)[1].split("\n[", 1)[0]
        matched = re.search(r'^version\s*=\s*"([^"\n]+)"', project, re.MULTILINE)
        version = matched.group(1) if matched else None
    except (OSError, IndexError):
        version = None
    return {"version": version, "commit": commit, "dirty": dirty, "files_sha256": hashes}
