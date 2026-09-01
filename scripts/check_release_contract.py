from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml


RELEASE_VERSION = "0.1.16"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise RuntimeError(f"{label} is {actual!r}; expected {expected!r}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from adaptive_skills.app_service import APP_CONTRACT_VERSION
    from adaptive_skills.database import SCHEMA_VERSION

    package = _json(root / "app" / "package.json")
    package_lock = _json(root / "app" / "package-lock.json")
    tauri = _json(root / "app" / "src-tauri" / "tauri.conf.json")
    cargo = _toml(root / "app" / "src-tauri" / "Cargo.toml")
    cargo_lock = _toml(root / "app" / "src-tauri" / "Cargo.lock")
    project = _toml(root / "pyproject.toml")

    versions = {
        "pyproject.toml": project["project"]["version"],
        "app/package.json": package["version"],
        "app/package-lock.json": package_lock["version"],
        "app/package-lock.json root package": package_lock["packages"][""]["version"],
        "app/src-tauri/Cargo.toml": cargo["package"]["version"],
        "app/src-tauri/tauri.conf.json": tauri["version"],
    }
    desktop_package = next(
        item
        for item in cargo_lock["package"]
        if item["name"] == "adaptive-skills-desktop"
    )
    versions["app/src-tauri/Cargo.lock"] = desktop_package["version"]
    for label, version in versions.items():
        _assert_equal(label, version, RELEASE_VERSION)
    _assert_equal(
        "app/package.json build:dmg",
        package["scripts"].get("build:dmg"),
        "tauri build --ci --bundles app,dmg",
    )

    workflow_path = root / ".github" / "workflows" / "ci.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        raise RuntimeError("GitHub Actions workflow has no jobs mapping")
    _assert_equal(
        "GitHub Actions jobs",
        set(workflow["jobs"]),
        {"quality", "bundle-smoke"},
    )
    triggers = workflow.get("on")
    if not isinstance(triggers, dict):
        raise RuntimeError("GitHub Actions workflow has no trigger mapping")
    _assert_equal(
        "GitHub Actions triggers",
        set(triggers),
        {"push", "pull_request", "workflow_dispatch"},
    )
    for job_name in ("quality", "bundle-smoke"):
        job = workflow["jobs"][job_name]
        _assert_equal(f"{job_name} runner", job.get("runs-on"), "macos-15")
    if "refs/tags/v" not in workflow_text:
        raise RuntimeError("GitHub Actions bundle job is not gated to version tags")

    print(
        "release contract ok: "
        f"v{RELEASE_VERSION}, schema {SCHEMA_VERSION}, "
        f"app contract {APP_CONTRACT_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
