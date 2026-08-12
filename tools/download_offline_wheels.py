from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import zipfile


def _wheel_metadata(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        return archive.read(metadata_name).decode("utf-8", errors="replace")


def _download_for(version: str, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=f"pangea-win-{version}-") as temporary:
        staging = Path(temporary)
        requirements = ["."]
        if version == "310":
            requirements.append("exceptiongroup>=1.0.2")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--platform=win_amd64",
                "--implementation=cp",
                f"--python-version={version}",
                f"--dest={staging}",
                *requirements,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _copy_wheels(staging, destination)


def _download_build_requirements(destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pangea-build-wheels-") as temporary:
        staging = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                f"--dest={staging}",
                "setuptools>=68",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _copy_wheels(staging, destination)


def _copy_wheels(source: Path, destination: Path) -> None:
    for wheel in source.glob("*.whl"):
        target = destination / wheel.name
        if target.exists() and target.read_bytes() != wheel.read_bytes():
            raise RuntimeError(f"wheel filename collision across Python versions: {wheel.name}")
        if not target.exists():
            target.write_bytes(wheel.read_bytes())


def _build_project_wheel(destination: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="pangea-project-wheel-") as temporary:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                f"--wheel-dir={temporary}",
                ".",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        wheels = list(Path(temporary).glob("pangea_agent-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("project wheel build did not produce exactly one pangea-agent wheel")
        target = destination / wheels[0].name
        target.write_bytes(wheels[0].read_bytes())
        return target


def main() -> None:
    destination = Path("vendor/wheels/win_amd64")
    destination.mkdir(parents=True, exist_ok=True)
    _download_build_requirements(destination)
    for version in ("310", "311", "312"):
        _download_for(version, destination)
    project_wheel = _build_project_wheel(destination)
    if "Version: 0.1.0" not in _wheel_metadata(project_wheel):
        raise RuntimeError("offline wheel set contains an unexpected pangea-agent version")
    print(f"offline wheel set ready: {len(list(destination.glob('*.whl')))} files")


if __name__ == "__main__":
    main()
