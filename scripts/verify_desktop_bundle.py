from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(core: Path, library: Path, *arguments: str) -> object:
    environment = {
        "HOME": str(library.parent / "home"),
        "LANG": "en_US.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": tempfile.gettempdir(),
    }
    result = subprocess.run(
        [
            str(core),
            "--library",
            str(library),
            "--compact",
            *arguments,
        ],
        check=True,
        capture_output=True,
        cwd=library.parent,
        env=environment,
        text=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def _materialize_bundled_app(
    root: Path, release_version: str, temporary: Path
) -> tuple[Path, Path]:
    dmg_directory = root / "app" / "src-tauri" / "target" / "release" / "bundle" / "dmg"
    disk_images = list(dmg_directory.glob(f"Adaptive Skills_{release_version}_*.dmg"))
    if len(disk_images) == 1:
        disk_image = disk_images[0]
        mountpoint = temporary / "mounted"
        mountpoint.mkdir()
        subprocess.run(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                str(mountpoint),
                str(disk_image),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        try:
            mounted_app = mountpoint / "Adaptive Skills.app"
            if not mounted_app.is_dir():
                raise RuntimeError(f"The DMG does not contain the app: {disk_image}")
            copied_app = temporary / "Adaptive Skills.app"
            subprocess.run(
                ["/usr/bin/ditto", str(mounted_app), str(copied_app)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            subprocess.run(
                ["/usr/bin/hdiutil", "detach", str(mountpoint)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        return copied_app, disk_image
    if disk_images:
        raise RuntimeError(
            f"Expected one v{release_version} DMG, found {len(disk_images)}"
        )
    app = (
        root
        / "app"
        / "src-tauri"
        / "target"
        / "release"
        / "bundle"
        / "macos"
        / "Adaptive Skills.app"
    )
    return app, app


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from adaptive_skills.app_service import APP_CONTRACT_VERSION
    from adaptive_skills.database import SCHEMA_VERSION

    tauri_config = json.loads(
        (root / "app" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    release_version = tauri_config["version"]
    bundle_workspace = tempfile.TemporaryDirectory(
        prefix="adaptive-skills-bundle-artifact-"
    )
    app, verified_artifact = _materialize_bundled_app(
        root, release_version, Path(bundle_workspace.name)
    )
    core = (
        app
        / "Contents"
        / "Resources"
        / "adaptive-skills-core"
        / "adaptive-skills-core"
    )
    if not core.is_file():
        raise RuntimeError(f"The packaged app is missing its desktop core: {core}")
    info_plist = app / "Contents" / "Info.plist"
    with info_plist.open("rb") as handle:
        bundle_info = plistlib.load(handle)
    if bundle_info.get("CFBundleShortVersionString") != release_version:
        raise RuntimeError("The packaged app returned an unexpected release version")
    icon_name = bundle_info.get("CFBundleIconFile")
    if not icon_name:
        raise RuntimeError("The packaged app does not declare CFBundleIconFile")
    icon_file = app / "Contents" / "Resources" / str(icon_name)
    if icon_file.suffix != ".icns":
        icon_file = icon_file.with_suffix(".icns")
    if not icon_file.is_file() or icon_file.stat().st_size < 1024:
        raise RuntimeError(f"The packaged app is missing its macOS icon: {icon_file}")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    with tempfile.TemporaryDirectory(prefix="adaptive-skills-bundle-check-") as raw:
        library = Path(raw) / "library"
        project = Path(raw) / "ordinary-project"
        project.mkdir()
        home = library.parent / "home"
        detect_path = home / ".bundle-agent"
        global_path = detect_path / "skills"
        detect_path.mkdir(parents=True)
        initialized = _run(core, library, "init")
        manual_source = library / "manual-source"
        manual_skill = manual_source / "manual-skill"
        manual_skill.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet", str(manual_source)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        manual_skill.joinpath("SKILL.md").write_text(
            "---\nname: manual-skill\ndescription: Verify a manually cloned source.\n---\n",
            encoding="utf-8",
        )
        reconciled = _run(core, library, "source", "reconcile")
        snapshot = _run(core, library, "app", "snapshot", "--limit", "10")
        project_status = _run(core, library, "project", "status", str(project))
        created_target = _run(
            core,
            library,
            "agent",
            "add",
            "--id",
            "bundle-agent",
            "--name",
            "Bundle Agent",
            "--global-path",
            str(global_path),
            "--detect-path",
            str(detect_path),
            "--project-path",
            ".bundle-agent/skills",
        )
        targets = _run(core, library, "agent", "list")
        removed_target = _run(core, library, "agent", "remove", "bundle-agent")
        if initialized.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("The packaged core returned an unexpected schema version")
        if snapshot.get("contract_version") != APP_CONTRACT_VERSION:
            raise RuntimeError("The packaged core returned an unexpected app contract")
        if not snapshot.get("capabilities", {}).get("bootstrap"):
            raise RuntimeError("The packaged core does not include bootstrap support")
        if not snapshot.get("capabilities", {}).get("source_forget"):
            raise RuntimeError("The packaged core does not include source forget support")
        if not snapshot.get("capabilities", {}).get("custom_agent_targets"):
            raise RuntimeError("The packaged core does not include custom Agent targets")
        if snapshot.get("capabilities", {}).get("inventory_import") or snapshot.get(
            "capabilities", {}
        ).get("inventory_export"):
            raise RuntimeError("The desktop contract must remain SQLite-only")
        if reconciled.get("discovered") != 1 or reconciled.get("scanned") != 1:
            raise RuntimeError("The packaged core cannot discover a manual Git clone")
        if project_status.get("managed") is not False:
            raise RuntimeError("The packaged core cannot distinguish an ordinary project")
        if created_target.get("id") != "bundle-agent" or created_target.get("built_in"):
            raise RuntimeError("The packaged core cannot create a custom Agent target")
        if "bundle-agent" not in {item.get("id") for item in targets}:
            raise RuntimeError("The packaged core cannot list a custom Agent target")
        if not removed_target.get("deleted") or removed_target.get(
            "filesystem_changed"
        ):
            raise RuntimeError("The packaged core cannot safely remove an Agent target")

    excel_runtime = [
        path
        for path in core.parent.rglob("*")
        if "openpyxl" in path.name.casefold() or "et_xmlfile" in path.name.casefold()
    ]
    if excel_runtime:
        raise RuntimeError(f"The desktop bundle includes Excel runtime files: {excel_runtime[:3]}")

    print(f"verified bundled core: {verified_artifact}")
    bundle_workspace.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
