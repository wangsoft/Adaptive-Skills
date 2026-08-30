from __future__ import annotations

import json
import plistlib
import subprocess
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
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
        if initialized.get("schema_version") != 7:
            raise RuntimeError("The packaged core returned an unexpected schema version")
        if snapshot.get("contract_version") != 8:
            raise RuntimeError("The packaged core returned an unexpected app contract")
        if not snapshot.get("capabilities", {}).get("bootstrap"):
            raise RuntimeError("The packaged core does not include bootstrap support")
        if not snapshot.get("capabilities", {}).get("source_forget"):
            raise RuntimeError("The packaged core does not include source forget support")
        if snapshot.get("capabilities", {}).get("inventory_import") or snapshot.get(
            "capabilities", {}
        ).get("inventory_export"):
            raise RuntimeError("The desktop contract must remain SQLite-only")
        if reconciled.get("discovered") != 1 or reconciled.get("scanned") != 1:
            raise RuntimeError("The packaged core cannot discover a manual Git clone")
        if project_status.get("managed") is not False:
            raise RuntimeError("The packaged core cannot distinguish an ordinary project")

    excel_runtime = [
        path
        for path in core.parent.rglob("*")
        if "openpyxl" in path.name.casefold() or "et_xmlfile" in path.name.casefold()
    ]
    if excel_runtime:
        raise RuntimeError(f"The desktop bundle includes Excel runtime files: {excel_runtime[:3]}")

    print(f"verified bundled core: {core}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
