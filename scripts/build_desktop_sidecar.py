from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


CORE_NAME = "adaptive-skills-core"


def _target_triple() -> str:
    configured = os.environ.get("TAURI_ENV_TARGET_TRIPLE", "").strip()
    if configured:
        return configured
    result = subprocess.run(
        ["rustc", "--print", "host-tuple"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("rustc did not report a target triple")
    return value


def _assert_native_target(target: str) -> None:
    architecture = platform.machine().lower()
    expected = {
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(architecture)
    if expected and not target.startswith(f"{expected}-"):
        raise RuntimeError(
            "Desktop sidecars must be built natively for the Tauri target; "
            f"host is {architecture}, target is {target}"
        )


def main() -> int:
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError as exc:
        raise RuntimeError(
            "PyInstaller is required for desktop packaging. "
            "Run: uv pip install --python .venv/bin/python -e '.[desktop]'"
        ) from exc

    root = Path(__file__).resolve().parents[1]
    target = _target_triple()
    _assert_native_target(target)
    build_root = root / "app" / "src-tauri" / "target" / "pyinstaller" / target
    resources = root / "app" / "src-tauri" / "resources"
    entrypoint = root / "scripts" / "adaptive_skills_core.py"
    distribution = build_root / "dist"

    pyinstaller.run(
        [
            str(entrypoint),
            "--name",
            CORE_NAME,
            "--onedir",
            "--console",
            "--noconfirm",
            "--clean",
            "--paths",
            str(root / "src"),
            "--collect-submodules",
            "keyring.backends",
            "--distpath",
            str(distribution),
            "--workpath",
            str(build_root / "work"),
            "--specpath",
            str(build_root / "spec"),
        ]
    )

    built = distribution / CORE_NAME
    executable_name = f"{CORE_NAME}.exe" if target.endswith("windows-msvc") else CORE_NAME
    if not built.joinpath(executable_name).is_file():
        raise RuntimeError(f"PyInstaller did not create the desktop core: {built}")
    resources.mkdir(parents=True, exist_ok=True)
    destination = resources / CORE_NAME
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(built, destination, symlinks=True)
    destination.joinpath(executable_name).chmod(0o755)
    destination.joinpath(".gitkeep").touch()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
