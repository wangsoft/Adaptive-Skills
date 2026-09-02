from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml


RELEASE_VERSION = "0.1.17"
UPDATER_PUBLIC_KEY = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEE1RTNBRUI2NDk1N0VCMTAKUldRUTYxZEp0cTdqcFNOSDdvd0xHOXdwaFZuTDNqQzFnWjZBVFpCZnhUdjJQWlh6My9iTE9RMnQK"
UPDATER_ENDPOINT = "https://github.com/wangsoft/Adaptive-Skills/releases/latest/download/latest.json"
CHECKOUT_ACTION = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SETUP_PYTHON_ACTION = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
SETUP_NODE_ACTION = "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_ARTIFACT_ACTION = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
JOB_ACTIONS = {
    "quality": [CHECKOUT_ACTION, SETUP_PYTHON_ACTION, SETUP_NODE_ACTION],
    "bundle": [
        CHECKOUT_ACTION,
        SETUP_PYTHON_ACTION,
        SETUP_NODE_ACTION,
        UPLOAD_ARTIFACT_ACTION,
    ],
    "release": [CHECKOUT_ACTION, DOWNLOAD_ARTIFACT_ACTION],
}
PLATFORMS = {
    "macos-15": {"bundles": "app,dmg", "artifact": "desktop-macos-arm64"},
    "windows-latest": {"bundles": "nsis", "artifact": "desktop-windows-x64"},
    "ubuntu-22.04": {"bundles": "appimage,deb", "artifact": "desktop-linux-x64"},
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise RuntimeError(f"{label} is {actual!r}; expected {expected!r}")




def _named_step(job: dict[str, Any], name: str) -> tuple[int, dict[str, Any]]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise RuntimeError("GitHub Actions job has no steps list")
    matches = [
        (index, step)
        for index, step in enumerate(steps)
        if isinstance(step, dict) and step.get("name") == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one GitHub Actions step named {name!r}")
    return matches[0]


def validate_workflow(workflow: object, release_version: str) -> None:
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        raise RuntimeError("GitHub Actions workflow has no jobs mapping")
    _assert_equal(
        "GitHub Actions jobs",
        set(workflow["jobs"]),
        {"quality", "bundle", "release"},
    )
    triggers = workflow.get("on")
    if not isinstance(triggers, dict):
        raise RuntimeError("GitHub Actions workflow has no trigger mapping")
    _assert_equal(
        "GitHub Actions triggers",
        set(triggers),
        {"push", "pull_request", "workflow_dispatch"},
    )
    _assert_equal(
        "GitHub Actions release version",
        workflow.get("env", {}).get("RELEASE_VERSION"),
        release_version,
    )
    _assert_equal(
        "GitHub Actions UTF-8 Python mode",
        workflow.get("env", {}).get("PYTHONUTF8"),
        "1",
    )
    _assert_equal(
        "GitHub Actions default permission",
        workflow.get("permissions"),
        {"contents": "read"},
    )
    for job_name, expected_actions in JOB_ACTIONS.items():
        steps = workflow["jobs"][job_name].get("steps")
        if not isinstance(steps, list):
            raise RuntimeError(f"{job_name} job has no steps")
        action_steps = [
            step
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("uses"), str)
        ]
        actual_actions = [step["uses"] for step in action_steps]
        if actual_actions != expected_actions:
            raise RuntimeError(
                f"{job_name} job must use only the approved immutable actions; "
                f"found {actual_actions!r}"
            )
        checkout = next(
            (step for step in action_steps if step["uses"] == CHECKOUT_ACTION),
            None,
        )
        if checkout is not None and checkout.get("with", {}).get(
            "persist-credentials"
        ) != "false":
            raise RuntimeError(f"{job_name} checkout must not persist credentials")

    quality = workflow["jobs"]["quality"]
    _assert_equal(
        "quality platform matrix",
        quality.get("strategy", {}).get("matrix", {}).get("platform"),
        list(PLATFORMS),
    )
    _assert_equal("quality runner", quality.get("runs-on"), "${{ matrix.platform }}")

    bundle = workflow["jobs"]["bundle"]
    _assert_equal("bundle dependency", bundle.get("needs"), "quality")
    _assert_equal("bundle runner", bundle.get("runs-on"), "${{ matrix.platform }}")
    _assert_equal(
        "bundle platform matrix",
        bundle.get("strategy", {}).get("matrix", {}).get("include"),
        [
            {
                "platform": platform,
                "bundles": config["bundles"],
                "artifact": config["artifact"],
            }
            for platform, config in PLATFORMS.items()
        ],
    )
    _assert_equal(
        "bundle gate",
        bundle.get("if"),
        "github.event_name == 'workflow_dispatch' || startsWith(github.ref, 'refs/tags/v')",
    )
    build_index, build = _named_step(bundle, "Build native desktop packages")
    verify_index, verify = _named_step(bundle, "Verify packaged core")
    upload_index, upload = _named_step(bundle, "Stage verified packages")
    if not build_index < verify_index < upload_index:
        raise RuntimeError("bundle step order must be build, verify, then upload")
    _assert_equal(
        "native bundle command",
        build.get("run"),
        "npm run tauri -- build --ci --bundles ${{ matrix.bundles }}",
    )
    _assert_equal(
        "native bundle signing secret",
        build.get("env", {}).get("TAURI_SIGNING_PRIVATE_KEY"),
        "${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}",
    )
    _assert_equal(
        "packaged core verifier",
        verify.get("run"),
        "python scripts/verify_desktop_bundle.py",
    )
    _assert_equal("verified package upload action", upload.get("uses"), UPLOAD_ARTIFACT_ACTION)
    _assert_equal(
        "verified package upload name",
        upload.get("with", {}).get("name"),
        "${{ matrix.artifact }}",
    )
    _assert_equal(
        "verified package upload path",
        upload.get("with", {}).get("path"),
        "app/src-tauri/target/release/verified-assets",
    )

    release = workflow["jobs"]["release"]
    _assert_equal("release dependency", release.get("needs"), "bundle")
    _assert_equal("release runner", release.get("runs-on"), "ubuntu-22.04")
    _assert_equal("release permission", release.get("permissions"), {"contents": "write"})
    _assert_equal(
        "release gate",
        release.get("if"),
        "startsWith(github.ref, 'refs/tags/v')",
    )
    download_index, download = _named_step(
        release, "Download verified platform packages"
    )
    assemble_index, assemble = _named_step(release, "Assemble exact release assets")
    publish_index, publish = _named_step(
        release, "Publish one release after every platform passes"
    )
    if not download_index < assemble_index < publish_index:
        raise RuntimeError("release step order must be download, assemble, then publish")
    _assert_equal(
        "verified package download action",
        download.get("uses"),
        DOWNLOAD_ARTIFACT_ACTION,
    )
    _assert_equal(
        "verified package download pattern",
        download.get("with", {}).get("pattern"),
        "desktop-*",
    )
    _assert_equal(
        "verified package download path",
        download.get("with", {}).get("path"),
        "release-inputs",
    )
    if download.get("with", {}).get("merge-multiple") not in {None, "false"}:
        raise RuntimeError("release download must not enable merge-multiple")
    _assert_equal(
        "release asset assembler",
        assemble.get("run"),
        'python3 scripts/verify_desktop_bundle.py --assemble-release release-inputs release-assets --version "$RELEASE_VERSION"',
    )
    _assert_equal(
        "release repository context",
        publish.get("env", {}).get("GH_REPO"),
        "${{ github.repository }}",
    )
    _assert_equal(
        "release token context",
        publish.get("env", {}).get("GH_TOKEN"),
        "${{ github.token }}",
    )
    publish_command = str(publish.get("run", "")).strip()
    if not publish_command.startswith(
        'test "$GITHUB_REF_NAME" = "v${RELEASE_VERSION}"'
    ):
        raise RuntimeError("release tag version check changed")
    expected_publish = r"""test "$GITHUB_REF_NAME" = "v${RELEASE_VERSION}"
tag="${GITHUB_REF_NAME}"
existing="$(gh release view "$tag" --json isDraft --jq '.isDraft' 2>/dev/null || true)"
if [ "$existing" = "true" ]; then
  gh release delete "$tag" --yes
elif [ "$existing" = "false" ]; then
  echo "Refusing to replace published release $tag" >&2
  exit 1
fi
created=0
cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$created" = "1" ]; then
    gh release delete "$tag" --yes || true
  fi
  exit "$status"
}
trap cleanup EXIT
gh release create "$tag" --verify-tag --draft --generate-notes --title "Adaptive Skills $tag"
created=1
gh release upload "$tag" release-assets/*
expected="$(sed 's/^[0-9a-f]\{64\}  //' release-assets/SHA256SUMS; printf '%s\n' SHA256SUMS)"
actual="$(gh release view "$tag" --json assets --jq '.assets[].name')"
test "$(printf '%s\n' "$actual" | sort)" = "$(printf '%s\n' "$expected" | sort)"
gh release edit "$tag" --draft=false
created=0
trap - EXIT"""
    if publish_command != expected_publish:
        raise RuntimeError("release exact asset publication contract changed")


def _validate_release_ref(release_version: str) -> None:
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        return
    expected = f"v{release_version}"
    actual = os.environ.get("GITHUB_REF_NAME")
    if actual != expected:
        raise RuntimeError(f"release tag is {actual!r}; expected {expected!r}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from adaptive_skills.app_service import APP_CONTRACT_VERSION
    from adaptive_skills import __version__
    from adaptive_skills.database import SCHEMA_VERSION

    package = _json(root / "app" / "package.json")
    package_lock = _json(root / "app" / "package-lock.json")
    tauri = _json(root / "app" / "src-tauri" / "tauri.conf.json")
    capabilities = _json(root / "app" / "src-tauri" / "capabilities" / "default.json")
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
        "src/adaptive_skills/__init__.py": __version__,
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
        "Tauri updater artifacts",
        tauri.get("bundle", {}).get("createUpdaterArtifacts"),
        True,
    )
    _assert_equal(
        "Tauri updater public key",
        tauri.get("plugins", {}).get("updater", {}).get("pubkey"),
        UPDATER_PUBLIC_KEY,
    )
    _assert_equal(
        "Tauri updater endpoint",
        tauri.get("plugins", {}).get("updater", {}).get("endpoints"),
        [UPDATER_ENDPOINT],
    )
    required_permissions = {"updater:default", "process:allow-restart"}
    if not required_permissions.issubset(set(capabilities.get("permissions", []))):
        raise RuntimeError("Tauri updater permissions are incomplete")
    for dependency in ("@tauri-apps/plugin-updater", "@tauri-apps/plugin-process"):
        if dependency not in package.get("dependencies", {}):
            raise RuntimeError(f"app/package.json is missing {dependency}")
    for dependency in ("tauri-plugin-updater", "tauri-plugin-process"):
        if dependency not in cargo.get("dependencies", {}):
            raise RuntimeError(f"app/src-tauri/Cargo.toml is missing {dependency}")

    expected_scripts = {
        "build:macos": "tauri build --ci --bundles app,dmg",
        "build:windows": "tauri build --ci --bundles nsis",
        "build:linux": "tauri build --ci --bundles appimage,deb",
    }
    for name, command in expected_scripts.items():
        _assert_equal(f"app/package.json {name}", package["scripts"].get(name), command)
    if "build:dmg" in package["scripts"]:
        raise RuntimeError("app/package.json still exposes obsolete build:dmg")

    workflow_path = root / ".github" / "workflows" / "ci.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    validate_workflow(workflow, RELEASE_VERSION)
    _validate_release_ref(RELEASE_VERSION)

    print(
        "release contract ok: "
        f"v{RELEASE_VERSION}, schema {SCHEMA_VERSION}, "
        f"app contract {APP_CONTRACT_VERSION}, platforms {len(PLATFORMS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
