from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import verify_desktop_bundle as verifier


class BundleVerifierTests(unittest.TestCase):
    def test_appimage_prefers_modern_7zip_and_keeps_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = root / "test.AppImage"
            with (
                patch.object(verifier.shutil, "which", return_value="/tools/7zz"),
                patch.object(verifier, "_preflight_7z") as preflight,
                patch.object(verifier.subprocess, "run") as run,
            ):
                extracted = verifier._extract_appimage(artifact, root)
            preflight.assert_called_once_with("/tools/7zz", artifact)
            self.assertEqual(run.call_args.args[0][0], "/tools/7zz")
            self.assertEqual(extracted, root / "appimage")

    def test_archive_listing_failure_includes_tool_diagnostic(self) -> None:
        result = subprocess.CompletedProcess([], 2, "7-Zip 16.02", "E_NOTIMPL")
        with patch.object(verifier.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "(?s)16.02.*E_NOTIMPL"):
                verifier._preflight_7z("7z", Path("test.AppImage"))

    def test_archive_preflight_still_rejects_unsafe_paths(self) -> None:
        for value in ("../outside", "/absolute", "C:\\outside"):
            result = subprocess.CompletedProcess([], 0, f"Path = {value}\n", "")
            with patch.object(verifier.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    verifier._preflight_7z("7zz", Path("test.AppImage"))

    def test_discovers_each_supported_platform_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundle = root / "app" / "src-tauri" / "target" / "release" / "bundle"
            artifacts = {
                "darwin": bundle / "dmg" / "Adaptive Skills_0.1.16_aarch64.dmg",
                "win32": bundle / "nsis" / "Adaptive Skills_0.1.16_x64-setup.exe",
                "linux-appimage": bundle / "appimage" / "Adaptive Skills_0.1.16_amd64.AppImage",
                "linux-deb": bundle / "deb" / "adaptive-skills_0.1.16_amd64.deb",
                "darwin-updater": bundle / "macos" / "Adaptive Skills.app.tar.gz",
                "win32-updater": bundle / "nsis" / "Adaptive Skills_0.1.16_x64-setup.exe",
                "linux-updater": bundle / "appimage" / "Adaptive Skills_0.1.16_amd64.AppImage",
            }
            for artifact in artifacts.values():
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.touch()
            for key in ("darwin-updater", "win32-updater", "linux-updater"):
                artifacts[key].with_name(artifacts[key].name + ".sig").write_text(
                    "test-signature", encoding="utf-8"
                )

            self.assertEqual(
                verifier.bundle_artifacts(root, "darwin", "0.1.16"),
                [artifacts["darwin"]],
            )
            self.assertEqual(
                verifier.bundle_artifacts(root, "win32", "0.1.16"),
                [artifacts["win32"]],
            )
            self.assertEqual(
                verifier.bundle_artifacts(root, "linux", "0.1.16"),
                [artifacts["linux-appimage"], artifacts["linux-deb"]],
            )
            self.assertEqual(
                verifier.updater_artifacts(root, "darwin", "0.1.16"),
                [artifacts["darwin-updater"], artifacts["darwin-updater"].with_name(artifacts["darwin-updater"].name + ".sig")],
            )

    def test_rejects_missing_duplicate_and_unknown_platform_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dmg = root / "app" / "src-tauri" / "target" / "release" / "bundle" / "dmg"
            dmg.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "Expected one macOS DMG"):
                verifier.bundle_artifacts(root, "darwin", "0.1.16")

            (dmg / "Adaptive Skills_0.1.16_aarch64.dmg").touch()
            (dmg / "Adaptive Skills_0.1.16_x64.dmg").touch()
            with self.assertRaisesRegex(RuntimeError, "found 2"):
                verifier.bundle_artifacts(root, "darwin", "0.1.16")

            with self.assertRaisesRegex(RuntimeError, "Unsupported desktop platform"):
                verifier.bundle_artifacts(root, "freebsd", "0.1.16")

    def test_finds_the_platform_specific_packaged_core(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            extracted = Path(raw)
            unix_core = extracted / "usr" / "lib" / "adaptive-skills-core" / "adaptive-skills-core"
            unix_core.parent.mkdir(parents=True)
            unix_core.touch()
            self.assertEqual(verifier.find_packaged_core(extracted, "linux"), unix_core)

            unix_core.unlink()
            windows_core = extracted / "$INSTDIR" / "adaptive-skills-core" / "adaptive-skills-core.exe"
            windows_core.parent.mkdir(parents=True)
            windows_core.touch()
            self.assertEqual(verifier.find_packaged_core(extracted, "win32"), windows_core)

    def test_rejects_linked_packaged_cores(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            extracted = Path(raw)
            core = extracted / "usr" / "lib" / "adaptive-skills-core" / "adaptive-skills-core"
            core.parent.mkdir(parents=True)
            outside = extracted.parent / f"{extracted.name}-outside-core"
            outside.touch()
            try:
                core.symlink_to(outside)
                with self.assertRaisesRegex(RuntimeError, "regular file"):
                    verifier.find_packaged_core(extracted, "linux")
            finally:
                outside.unlink()

        with tempfile.TemporaryDirectory() as raw:
            extracted = Path(raw)
            core = extracted / "usr" / "lib" / "adaptive-skills-core" / "adaptive-skills-core"
            core.parent.mkdir(parents=True)
            source = extracted / "core-source"
            source.touch()
            os.link(source, core)
            with self.assertRaisesRegex(RuntimeError, "single-link"):
                verifier.find_packaged_core(extracted, "linux")

    def test_materializes_both_linux_package_cores(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            appimage_root = root / "appimage"
            deb_root = root / "deb"
            appimage_core = (
                appimage_root / "usr" / "lib" / "adaptive-skills-core" / "adaptive-skills-core"
            )
            deb_core = deb_root / "usr" / "lib" / "adaptive-skills-core" / "adaptive-skills-core"
            for core in (appimage_core, deb_core):
                core.parent.mkdir(parents=True)
                core.touch()

            with (
                patch.object(verifier, "_extract_appimage", return_value=appimage_root),
                patch.object(verifier, "_extract_deb", return_value=deb_root),
            ):
                cores = verifier._materialize_cores(
                    [root / "app.AppImage", root / "app.deb"],
                    "linux",
                    "0.1.16",
                    root / "temporary",
                )

            self.assertEqual(cores, [appimage_core, deb_core])

    def test_stages_and_assembles_only_manifested_release_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = root / "inputs"
            sources = root / "sources"
            sources.mkdir()
            dmg = sources / "Adaptive Skills_0.1.16_aarch64.dmg"
            nsis = sources / "Adaptive Skills_0.1.16_x64-setup.exe"
            appimage = sources / "Adaptive Skills_0.1.16_amd64.AppImage"
            deb = sources / "adaptive-skills_0.1.16_amd64.deb"
            for artifact in (dmg, nsis, appimage, deb):
                artifact.write_bytes(artifact.name.encode())
            mac_updater = sources / "Adaptive Skills.app.tar.gz"
            windows_updater = nsis
            linux_updater = appimage
            updater_files = []
            for updater in (mac_updater, windows_updater, linux_updater):
                updater.write_bytes(updater.name.encode())
                signature = updater.with_name(updater.name + ".sig")
                signature.write_text(f"signature-for-{updater.name}", encoding="utf-8")
                updater_files.append((updater, signature))

            verifier.stage_verified_assets(
                [dmg], list(updater_files[0]), "darwin", "0.1.16", inputs / "desktop-macos-arm64"
            )
            verifier.stage_verified_assets(
                [nsis], list(updater_files[1]), "win32", "0.1.16", inputs / "desktop-windows-x64"
            )
            verifier.stage_verified_assets(
                [appimage, deb], list(updater_files[2]), "linux", "0.1.16", inputs / "desktop-linux-x64"
            )
            output = root / "release-assets"
            verifier.assemble_release_assets(inputs, output, "0.1.16")

            names = sorted(path.name for path in output.iterdir())
            self.assertEqual(
                names,
                [
                    "Adaptive-Skills.app.tar.gz",
                    "Adaptive-Skills.app.tar.gz.sig",
                    "Adaptive-Skills_0.1.16_aarch64.dmg",
                    "Adaptive-Skills_0.1.16_amd64.AppImage",
                    "Adaptive-Skills_0.1.16_amd64.AppImage.sig",
                    "Adaptive-Skills_0.1.16_x64-setup.exe",
                    "Adaptive-Skills_0.1.16_x64-setup.exe.sig",
                    "SHA256SUMS",
                    "adaptive-skills_0.1.16_amd64.deb",
                    "latest.json",
                ],
            )
            checksum_names = {
                line.split("  ", 1)[1]
                for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
            }
            self.assertEqual(checksum_names, set(names) - {"SHA256SUMS"})
            latest = json.loads((output / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["version"], "0.1.16")
            self.assertEqual(
                set(latest["platforms"]),
                {"darwin-aarch64", "windows-x86_64", "linux-x86_64"},
            )
            self.assertTrue(
                latest["platforms"]["darwin-aarch64"]["url"].endswith(
                    "Adaptive-Skills.app.tar.gz"
                )
            )

            for updater, signature in updater_files:
                self.assertEqual(
                    (output / verifier._release_asset_name(updater.name)).read_bytes(),
                    updater.read_bytes(),
                )
                self.assertEqual(
                    (output / verifier._release_asset_name(signature.name)).read_bytes(),
                    signature.read_bytes(),
                )

            manifest = json.loads(
                (inputs / "desktop-linux-x64" / "verified-assets.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest["files"][0]["sha256"] = "0" * 64
            (inputs / "desktop-linux-x64" / "verified-assets.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "digest"):
                verifier.assemble_release_assets(inputs, root / "rejected", "0.1.16")

    def test_runtime_environment_preserves_system_paths_and_isolates_user_directories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "home"
            environment = verifier.runtime_environment(
                home,
                {
                    "PATH": "tool-path",
                    "SYSTEMROOT": "C:\\Windows",
                    "SSH_AUTH_SOCK": "/private/agent.sock",
                    "OPENAI_API_KEY": "secret",
                    "LD_PRELOAD": "/tmp/inject.so",
                },
            )

            self.assertEqual(environment["PATH"], "tool-path")
            self.assertEqual(environment["SYSTEMROOT"], "C:\\Windows")
            self.assertEqual(environment["HOME"], str(home))
            self.assertEqual(environment["USERPROFILE"], str(home))
            self.assertEqual(environment["APPDATA"], str(home / "AppData" / "Roaming"))
            self.assertEqual(environment["LOCALAPPDATA"], str(home / "AppData" / "Local"))
            self.assertEqual(environment["TMPDIR"], str(home / "tmp"))
            self.assertNotIn("SSH_AUTH_SOCK", environment)
            self.assertNotIn("OPENAI_API_KEY", environment)
            self.assertNotIn("LD_PRELOAD", environment)


if __name__ == "__main__":
    unittest.main()
