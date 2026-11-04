"""Run acceptance scripts without implicit script/cwd paths or PYTHONPATH.

Use the installed-dependency interpreter: python verification/embedded_python_imports.py
This reproduces the import boundary of Windows embedded Python's ._pth file;
it is not a replacement for executing the Windows release build.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERIFICATION = ROOT / "verification"
BUILD_SCRIPTS = {
    "incremental_smoke.py", "observability_recovery.py", "test_case_contract.py",
    "branch_behavior_contract.py", "flow_stability.py", "report_delivery_smoke.py",
    "asset_lifecycle.py",
}
BOOTSTRAP = """
import json, runpy, sys
from pathlib import Path
script, runtime_src, dependencies = sys.argv[1:]
# -I -S keeps only stdlib paths and ignores environment/site .pth additions.
sys.path[:0] = [runtime_src, *json.loads(dependencies)]
assert str(Path(script).parent) not in sys.path, sys.path
assert str(Path(script).parent.parent) not in sys.path, sys.path
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
"""


def acceptance_scripts():
    """Cover build scripts and every consumer of a sibling verification helper."""
    paths = list(VERIFICATION.glob("*.py"))
    siblings = {path.stem for path in paths}
    selected = set(BUILD_SCRIPTS)
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any(name.split(".")[0] in siblings for name in imports):
                selected.add(path.name)
    return [VERIFICATION / name for name in sorted(selected)]


def main():
    dependencies = [path for path in sys.path if Path(path).name in {"site-packages", "dist-packages"}]
    if not dependencies:
        raise RuntimeError("Run with the interpreter containing the installed runtime dependencies")
    scripts = acceptance_scripts()
    # CLI subprocesses need the same runtime source tree. Never add verification.
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    with tempfile.TemporaryDirectory(prefix="pangea-embedded-path-") as cwd:
        for script in scripts:
            print(f"ISOLATED ACCEPTANCE: {script.name}", flush=True)
            subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", "-c", BOOTSTRAP,
                            str(script), str(ROOT / "src"), json.dumps(dependencies)],
                           cwd=cwd, env=env, check=True)
    print(f"PASS: {len(scripts)} acceptance scripts with no implicit verification/cwd path")


if __name__ == "__main__":
    main()
