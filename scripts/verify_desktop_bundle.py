from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from collections.abc import Mapping
from pathlib import Path, PurePosixPath


def runtime_environment(home: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    environment = {
        key: source[key]
        for key in ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
        if key in source
    }
    temporary = home / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "LANG": "en_US.UTF-8",
            "TMPDIR": str(temporary),
            "TEMP": str(temporary),
            "TMP": str(temporary),
        }
    )
    return environment


def _run(core: Path, library: Path, *arguments: str) -> object:
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
        env=runtime_environment(library.parent / "home"),
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return json.loads(result.stdout)


def _require_regular_file(path: Path, root: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f"The {label} is missing: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"The {label} must be a regular file: {path}")
    if metadata.st_nlink != 1:
        raise RuntimeError(f"The {label} must be a single-link file: {path}")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise RuntimeError(f"The {label} escapes its verified root: {path}") from exc
    return path


def _safe_archive_path(value: str, label: str) -> None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise RuntimeError(f"Unsafe {label} path in package: {value}")


def _preflight_7z(seven_zip: str, artifact: Path) -> None:
    result = subprocess.run(
        [seven_zip, "l", "-slt", str(artifact)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" = ")
        if not separator or key not in {"Path", "Symbolic Link", "Hard Link"}:
            continue
        if value == str(artifact) or value == artifact.name:
            continue
        _safe_archive_path(value, key)


def _assert_safe_core_tree(core_directory: Path, extracted_root: Path) -> None:
    for path in [core_directory, *core_directory.rglob("*")]:
        metadata = path.lstat()
        if path.is_symlink():
            raise RuntimeError(f"The packaged core contains a symbolic link: {path}")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise RuntimeError(f"The packaged core contains a hard link: {path}")
        try:
            path.resolve(strict=True).relative_to(extracted_root.resolve(strict=True))
        except ValueError as exc:
            raise RuntimeError(f"The packaged core escapes its extracted root: {path}") from exc


def _one_artifact(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {label}, found {len(matches)} in {directory}")
    return _require_regular_file(matches[0], directory, label)


def bundle_artifacts(root: Path, platform: str, release_version: str) -> list[Path]:
    bundle = root / "app" / "src-tauri" / "target" / "release" / "bundle"
    if platform == "darwin":
        return [
            _one_artifact(
                bundle / "dmg",
                f"Adaptive Skills_{release_version}_*.dmg",
                "macOS DMG",
            )
        ]
    if platform == "win32":
        return [
            _one_artifact(
                bundle / "nsis",
                f"*{release_version}*-setup.exe",
                "Windows NSIS installer",
            )
        ]
    if platform == "linux":
        return [
            _one_artifact(
                bundle / "appimage",
                f"*{release_version}*.AppImage",
                "Linux AppImage",
            ),
            _one_artifact(
                bundle / "deb",
                f"*{release_version}*.deb",
                "Linux DEB package",
            ),
        ]
    raise RuntimeError(f"Unsupported desktop platform: {platform}")


def updater_artifacts(root: Path, platform: str, release_version: str) -> list[Path]:
    bundle = root / "app" / "src-tauri" / "target" / "release" / "bundle"
    version = re.escape(release_version)
    if platform == "darwin":
        directory = bundle / "macos"
        archive_pattern = re.compile(r".+\.app\.tar\.gz")
    elif platform == "win32":
        directory = bundle / "nsis"
        archive_pattern = re.compile(rf".+_{version}_[A-Za-z0-9._-]+-setup\.exe")
    elif platform == "linux":
        directory = bundle / "appimage"
        archive_pattern = re.compile(rf".+_{version}_[A-Za-z0-9._-]+\.AppImage")
    else:
        raise RuntimeError(f"Unsupported desktop platform: {platform}")
    archives = sorted(
        path
        for path in directory.glob("*")
        if path.is_file() and archive_pattern.fullmatch(path.name)
    )
    if len(archives) != 1:
        raise RuntimeError(
            f"Expected one signed {platform} updater archive, found {len(archives)} in {directory}"
        )
    archive = _require_regular_file(archives[0], directory, "updater archive")
    signature = _require_regular_file(
        archive.with_name(archive.name + ".sig"),
        directory,
        "updater signature",
    )
    _read_signature(signature)
    return [archive, signature]


def find_packaged_core(extracted: Path, platform: str) -> Path:
    executable = "adaptive-skills-core.exe" if platform == "win32" else "adaptive-skills-core"
    matches = sorted(
        path
        for path in extracted.rglob(executable)
        if path.parent.name == "adaptive-skills-core"
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one packaged core named {executable}, found {len(matches)} in {extracted}"
        )
    core = _require_regular_file(matches[0], extracted, "packaged core")
    _assert_safe_core_tree(core.parent, extracted)
    return core


def _extract_dmg(artifact: Path, temporary: Path) -> Path:
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
            str(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        mounted_app = mountpoint / "Adaptive Skills.app"
        if not mounted_app.is_dir():
            raise RuntimeError(f"The DMG does not contain the app: {artifact}")
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
    return copied_app


def _verify_macos_app(app: Path, release_version: str) -> None:
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


def _extract_nsis(artifact: Path, temporary: Path) -> Path:
    seven_zip = shutil.which("7z") or shutil.which("7z.exe")
    if not seven_zip:
        raise RuntimeError("7-Zip is required to verify the Windows NSIS installer")
    _preflight_7z(seven_zip, artifact)
    extracted = temporary / "nsis"
    extracted.mkdir()
    subprocess.run(
        [seven_zip, "x", "-y", f"-o{extracted}", str(artifact)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return extracted


def _extract_appimage(artifact: Path, temporary: Path) -> Path:
    seven_zip = shutil.which("7z") or shutil.which("7z.exe")
    if not seven_zip:
        raise RuntimeError("7-Zip is required to verify the Linux AppImage")
    _preflight_7z(seven_zip, artifact)
    extracted = temporary / "appimage"
    extracted.mkdir()
    subprocess.run(
        [seven_zip, "x", "-y", f"-o{extracted}", str(artifact)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return extracted


def _extract_deb(
    artifact: Path, temporary: Path, release_version: str
) -> Path:
    version = subprocess.run(
        ["dpkg-deb", "--field", str(artifact), "Version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if version != release_version:
        raise RuntimeError(
            f"The DEB version is {version!r}; expected {release_version!r}"
        )
    extracted = temporary / "deb"
    extracted.mkdir()
    payload = temporary / "deb-payload.tar"
    with payload.open("wb") as handle:
        subprocess.run(
            ["dpkg-deb", "--fsys-tarfile", str(artifact)],
            check=True,
            stdout=handle,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    with tarfile.open(payload, mode="r:") as archive:
        for member in archive.getmembers():
            _safe_archive_path(member.name, "DEB member")
            if member.issym() or member.islnk():
                _safe_archive_path(member.linkname, "DEB link target")
    subprocess.run(
        ["dpkg-deb", "--extract", str(artifact), str(extracted)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return extracted


def _materialize_cores(
    artifacts: list[Path],
    platform: str,
    release_version: str,
    temporary: Path,
) -> list[Path]:
    if platform == "darwin":
        app = _extract_dmg(artifacts[0], temporary)
        _verify_macos_app(app, release_version)
        return [find_packaged_core(app / "Contents" / "Resources", platform)]
    if platform == "win32":
        return [find_packaged_core(_extract_nsis(artifacts[0], temporary), platform)]
    if platform == "linux":
        return [
            find_packaged_core(_extract_appimage(artifacts[0], temporary), platform),
            find_packaged_core(
                _extract_deb(artifacts[1], temporary, release_version),
                platform,
            ),
        ]
    raise RuntimeError(f"Unsupported desktop platform: {platform}")


def _verify_core_contract(root: Path, core: Path, release_version: str) -> None:
    sys.path.insert(0, str(root / "src"))
    from adaptive_skills.app_service import APP_CONTRACT_VERSION
    from adaptive_skills.database import SCHEMA_VERSION

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
        if initialized.get("release_version") != release_version:
            raise RuntimeError("The packaged core returned an unexpected release version")
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
        if not removed_target.get("deleted") or removed_target.get("filesystem_changed"):
            raise RuntimeError("The packaged core cannot safely remove an Agent target")

    excel_runtime = [
        path
        for path in core.parent.rglob("*")
        if "openpyxl" in path.name.casefold() or "et_xmlfile" in path.name.casefold()
    ]
    if excel_runtime:
        raise RuntimeError(f"The desktop bundle includes Excel runtime files: {excel_runtime[:3]}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_signature(path: Path) -> str:
    if path.stat().st_size > 32 * 1024:
        raise RuntimeError(f"Updater signature is unexpectedly large: {path}")
    signature = path.read_text(encoding="utf-8").strip()
    if not signature or "\0" in signature:
        raise RuntimeError(f"Updater signature is empty or malformed: {path}")
    return signature


def _expected_asset_patterns(platform: str, release_version: str) -> list[re.Pattern[str]]:
    version = re.escape(release_version)
    if platform == "darwin":
        return [
            re.compile(rf"Adaptive Skills_{version}_[A-Za-z0-9._-]+\.dmg"),
            re.compile(r".+\.app\.tar\.gz"),
            re.compile(r".+\.app\.tar\.gz\.sig"),
        ]
    if platform == "win32":
        return [
            re.compile(rf"Adaptive Skills_{version}_[A-Za-z0-9._-]+-setup\.exe"),
            re.compile(rf"Adaptive Skills_{version}_[A-Za-z0-9._-]+-setup\.exe\.sig"),
        ]
    if platform == "linux":
        return [
            re.compile(rf".+_{version}_[A-Za-z0-9._-]+\.AppImage"),
            re.compile(rf".+_{version}_[A-Za-z0-9._-]+\.AppImage\.sig"),
            re.compile(rf".+_{version}_[A-Za-z0-9._-]+\.deb"),
        ]
    raise RuntimeError(f"Unsupported desktop platform: {platform}")


def _validate_asset_names(
    names: list[str], platform: str, release_version: str
) -> None:
    patterns = _expected_asset_patterns(platform, release_version)
    if len(names) != len(patterns):
        raise RuntimeError(
            f"Expected {len(patterns)} verified {platform} assets, found {len(names)}"
        )
    unmatched = list(names)
    for pattern in patterns:
        matches = [name for name in unmatched if pattern.fullmatch(name)]
        if len(matches) != 1:
            raise RuntimeError(
                f"Verified {platform} assets do not match {pattern.pattern}: {names}"
            )
        unmatched.remove(matches[0])


def stage_verified_assets(
    artifacts: list[Path],
    updater: list[Path],
    platform: str,
    release_version: str,
    destination: Path,
) -> None:
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(f"Verified asset staging directory already exists: {destination}")
    destination.mkdir(parents=True)
    staged: list[Path] = []
    for artifact in [*artifacts, *updater]:
        _require_regular_file(artifact, artifact.parent, "verified package")
        if "\n" in artifact.name or "\r" in artifact.name:
            raise RuntimeError(f"Unsafe verified package name: {artifact.name!r}")
        target = destination / artifact.name
        if target.exists():
            if _sha256(target) == _sha256(artifact):
                continue
            raise RuntimeError(f"Duplicate verified package name: {artifact.name}")
        shutil.copy2(artifact, target)
        staged.append(target)
    names = [path.name for path in staged]
    _validate_asset_names(names, platform, release_version)
    manifest = {
        "schema": "adaptive-skills-verified-assets/1",
        "platform": platform,
        "version": release_version,
        "files": [
            {"name": path.name, "sha256": _sha256(path)}
            for path in sorted(staged, key=lambda item: item.name)
        ],
    }
    destination.joinpath("verified-assets.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assemble_release_assets(inputs: Path, output: Path, release_version: str) -> None:
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"Release asset directory already exists: {output}")
    manifests = sorted(inputs.glob("*/verified-assets.json"))
    if len(manifests) != 3:
        raise RuntimeError(f"Expected three platform manifests, found {len(manifests)}")
    platforms: set[str] = set()
    verified: list[Path] = []
    names: set[str] = set()
    update_platforms: dict[str, tuple[Path, Path]] = {}
    for manifest_path in manifests:
        _require_regular_file(manifest_path, inputs, "verified asset manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "adaptive-skills-verified-assets/1":
            raise RuntimeError(f"Unexpected verified asset manifest: {manifest_path}")
        platform = manifest.get("platform")
        if platform not in {"darwin", "win32", "linux"} or platform in platforms:
            raise RuntimeError(f"Unexpected or duplicate platform manifest: {platform}")
        if manifest.get("version") != release_version:
            raise RuntimeError(f"Verified asset version mismatch: {manifest_path}")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise RuntimeError(f"Malformed verified asset manifest: {manifest_path}")
        manifest_names = [item.get("name") for item in files if isinstance(item, dict)]
        if len(manifest_names) != len(files) or not all(
            isinstance(name, str)
            and Path(name).name == name
            and "\n" not in name
            and "\r" not in name
            for name in manifest_names
        ):
            raise RuntimeError(f"Unsafe verified asset manifest names: {manifest_path}")
        _validate_asset_names(manifest_names, platform, release_version)
        actual_names = {
            path.name
            for path in manifest_path.parent.iterdir()
            if path.name != manifest_path.name
        }
        if actual_names != set(manifest_names):
            raise RuntimeError(f"Unmanifested files in verified asset directory: {manifest_path.parent}")
        for item in files:
            name = item["name"]
            if name in names:
                raise RuntimeError(f"Duplicate release asset name: {name}")
            asset = _require_regular_file(
                manifest_path.parent / name,
                manifest_path.parent,
                "verified release asset",
            )
            if _sha256(asset) != item.get("sha256"):
                raise RuntimeError(f"Verified release asset digest mismatch: {asset}")
            names.add(name)
            verified.append(asset)
        updater_suffix = {
            "darwin": ".app.tar.gz",
            "win32": "-setup.exe",
            "linux": ".AppImage",
        }[platform]
        updater_archives = [
            manifest_path.parent / name
            for name in manifest_names
            if name.endswith(updater_suffix)
        ]
        if len(updater_archives) != 1:
            raise RuntimeError(f"Expected one updater archive in {manifest_path.parent}")
        updater_archive = updater_archives[0]
        updater_signature = updater_archive.with_name(updater_archive.name + ".sig")
        if updater_signature.name not in manifest_names:
            raise RuntimeError(f"Updater signature is not manifested: {updater_signature}")
        update_platforms[platform] = (updater_archive, updater_signature)
        platforms.add(platform)
    if platforms != {"darwin", "win32", "linux"}:
        raise RuntimeError(f"Missing verified platform assets: {platforms}")
    output.mkdir(parents=True)
    for asset in verified:
        shutil.copy2(asset, output / asset.name)
    target_names = {
        "darwin": "darwin-aarch64",
        "win32": "windows-x86_64",
        "linux": "linux-x86_64",
    }
    base_url = f"https://github.com/wangsoft/Adaptive-Skills/releases/download/v{release_version}/"
    latest = {
        "version": release_version,
        "notes": f"Adaptive Skills v{release_version}",
        "platforms": {
            target_names[platform]: {
                "signature": _read_signature(signature),
                "url": base_url + urllib.parse.quote(archive.name),
            }
            for platform, (archive, signature) in sorted(update_platforms.items())
        },
    }
    output.joinpath("latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(output.iterdir(), key=lambda item: item.name)
    ]
    output.joinpath("SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assemble-release",
        nargs=2,
        metavar=("INPUTS", "OUTPUT"),
        type=Path,
    )
    parser.add_argument("--version")
    options = parser.parse_args(arguments)
    if options.assemble_release:
        if not options.version:
            parser.error("--version is required with --assemble-release")
        inputs, output = options.assemble_release
        assemble_release_assets(inputs, output, options.version)
        print(f"assembled verified release assets: {output}")
        return 0

    root = Path(__file__).resolve().parents[1]
    tauri_config = json.loads(
        (root / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    release_version = tauri_config["version"]
    platform = sys.platform
    artifacts = bundle_artifacts(root, platform, release_version)
    updater = updater_artifacts(root, platform, release_version)
    staging = root / "app" / "src-tauri" / "target" / "release" / "verified-assets"
    if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
        raise RuntimeError(f"Unsafe verified asset staging path: {staging}")
    if staging.exists():
        shutil.rmtree(staging)

    with tempfile.TemporaryDirectory(
        prefix="adaptive-skills-bundle-artifact-"
    ) as raw:
        cores = _materialize_cores(artifacts, platform, release_version, Path(raw))
        for core in cores:
            _verify_core_contract(root, core, release_version)
        stage_verified_assets(
            artifacts,
            updater,
            platform,
            release_version,
            staging,
        )

    print("verified bundled core: " + ", ".join(str(path) for path in artifacts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
