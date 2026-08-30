from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_skills.bootstrap import BootstrapService
from adaptive_skills.catalog import Catalog
from adaptive_skills.config import Settings
from adaptive_skills.errors import ValidationError
from adaptive_skills.sources import SourceManager

from tests.helpers import write_skill


class BootstrapDiscoveryTests(unittest.TestCase):
    def test_discovery_excludes_provider_owned_claude_and_codex_skills(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            library = home / "library"

            claude_root = home / ".claude" / "skills"
            claude_skill = write_skill(
                claude_root, "docx", "A provider-managed Claude document Skill."
            )
            (claude_skill / "LICENSE.txt").write_text(
                "© 2025 Anthropic, PBC. All rights reserved.\n"
                "These materials may not be retained outside the Services.\n",
                encoding="utf-8",
            )

            codex_root = home / ".codex" / "skills"
            codex_skill = write_skill(
                codex_root, "playwright", "A provider-managed Codex Skill."
            )
            vendor_skill = write_skill(
                home / ".codex" / "vendor_imports" / "skills" / "skills" / ".curated",
                "playwright",
                "A provider-managed Codex Skill.",
            )
            self.assertEqual(
                (codex_skill / "SKILL.md").read_bytes(),
                (vendor_skill / "SKILL.md").read_bytes(),
            )

            result = BootstrapService(Settings.load(library)).discover(
                [claude_root, codex_root]
            )
            by_name = {item["name"]: item for item in result["candidates"]}

            self.assertEqual(by_name["docx"]["kind"], "provider")
            self.assertEqual(by_name["docx"]["provider"], "Claude")
            self.assertFalse(by_name["docx"]["importable"])
            self.assertEqual(by_name["playwright"]["kind"], "provider")
            self.assertEqual(by_name["playwright"]["provider"], "Codex")
            self.assertFalse(by_name["playwright"]["importable"])

    def test_discovery_classifies_system_symlink_and_duplicate_skills(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            scan_root = root / "agent-skills"
            first = write_skill(scan_root, "first-skill", "A reusable local Skill.")
            duplicate = write_skill(
                scan_root,
                "first-skill",
                "A reusable local Skill.",
                body="# Instructions\n\nDo the requested work safely.",
                directory="duplicate-skill",
            )
            system = write_skill(
                scan_root / ".system", "system-skill", "A bundled system Skill."
            )
            linked_target = write_skill(
                root / "linked-target", "linked-skill", "A linked Skill."
            )
            (scan_root / "linked-skill").symlink_to(linked_target, target_is_directory=True)

            result = BootstrapService(Settings.load(library)).discover([scan_root])
            by_path = {item["path"]: item for item in result["candidates"]}

            self.assertEqual(result["root_count"], 1)
            self.assertEqual(result["candidate_count"], 4)
            self.assertEqual(by_path[str(first.resolve())]["kind"], "local")
            self.assertEqual(
                by_path[str(system.resolve())]["kind"], "system"
            )
            self.assertFalse(by_path[str(system.resolve())]["importable"])
            self.assertEqual(
                by_path[str((scan_root / "linked-skill").absolute())]["kind"],
                "symlink",
            )
            self.assertFalse(
                by_path[str((scan_root / "linked-skill").absolute())]["importable"]
            )
            duplicate_items = [
                by_path[str(first.resolve())], by_path[str(duplicate.resolve())]
            ]
            self.assertEqual(sum(item["importable"] for item in duplicate_items), 1)
            self.assertEqual(sum(bool(item["duplicate_of"]) for item in duplicate_items), 1)

    def test_discovery_marks_catalog_content_as_already_managed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            scan_root = root / "agent-skills"
            skill = write_skill(scan_root, "known-skill", "Already imported.")
            settings = Settings.load(library)
            service = BootstrapService(settings)
            preview = service.discover([scan_root])
            service.import_candidates(
                [
                    {
                        "path": preview["candidates"][0]["path"],
                        "tree_hash": preview["candidates"][0]["tree_hash"],
                    }
                ]
            )

            second = service.discover([scan_root])["candidates"][0]
            self.assertEqual(second["path"], str(skill.resolve()))
            self.assertFalse(second["importable"])
            self.assertTrue(second["duplicate_of"].startswith("catalog:"))


class BootstrapImportTests(unittest.TestCase):
    def test_copy_import_preserves_original_and_scans_sqlite_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            scan_root = root / "agent-skills"
            original = write_skill(
                scan_root,
                "local-helper",
                "A local helper Skill.",
                body="# Workflow\n\nKeep this original content.",
            )
            settings = Settings.load(library)
            service = BootstrapService(settings)
            candidate = service.discover([scan_root])["candidates"][0]

            result = service.import_candidates(
                [{"path": candidate["path"], "tree_hash": candidate["tree_hash"]}]
            )

            destination = library / "local-imports" / "local-helper"
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertTrue(original.is_dir())
            self.assertEqual(
                (original / "SKILL.md").read_text(encoding="utf-8"),
                (destination / "SKILL.md").read_text(encoding="utf-8"),
            )
            source = SourceManager(settings).get("local-imports")
            self.assertEqual(source["update_policy"], "local")
            self.assertEqual(Path(source["local_path"]), (library / "local-imports").resolve())
            skill = Catalog(settings).get_skill("local-helper")
            self.assertEqual(skill["source_name"], "local-imports")

    def test_import_refuses_hash_changes_collisions_and_symlinked_trees(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            scan_root = root / "agent-skills"
            changed = write_skill(scan_root, "changed-skill", "Original preview.")
            linked = write_skill(scan_root, "linked-tree", "Contains a symlink.")
            (linked / "outside.txt").symlink_to(root / "outside.txt")
            settings = Settings.load(library)
            service = BootstrapService(settings)
            candidates = {
                item["name"]: item for item in service.discover([scan_root])["candidates"]
            }
            (changed / "SKILL.md").write_text(
                "---\nname: changed-skill\ndescription: Changed after preview.\n---\n",
                encoding="utf-8",
            )
            collision = library / "local-imports" / "changed-skill"
            collision.mkdir(parents=True)
            (collision / "keep.txt").write_text("keep", encoding="utf-8")

            result = service.import_candidates(
                [
                    {
                        "path": candidates["changed-skill"]["path"],
                        "tree_hash": candidates["changed-skill"]["tree_hash"],
                    },
                    {
                        "path": candidates["linked-tree"]["path"],
                        "tree_hash": candidates["linked-tree"]["tree_hash"],
                    },
                ]
            )

            self.assertEqual(result["imported"], 0)
            self.assertEqual(result["failed"], 2)
            self.assertEqual((collision / "keep.txt").read_text(encoding="utf-8"), "keep")
            errors = " ".join(item["error"] for item in result["results"])
            self.assertIn("changed after discovery", errors)
            self.assertIn("symlink", errors.lower())

    def test_import_refuses_a_symlinked_local_import_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            original = write_skill(
                root / "agent-skills", "safe-skill", "A local Skill."
            )
            library.mkdir()
            (library / "local-imports").symlink_to(
                root / "redirected-imports", target_is_directory=True
            )
            service = BootstrapService(Settings.load(library))
            candidate = service.discover([original.parent])["candidates"][0]

            with self.assertRaisesRegex(ValidationError, "cannot be a symlink"):
                service.import_candidates(
                    [{"path": candidate["path"], "tree_hash": candidate["tree_hash"]}]
                )

            self.assertFalse((root / "redirected-imports" / "safe-skill").exists())


class BootstrapStarterTests(unittest.TestCase):
    def test_starter_install_is_explicit_and_continues_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            settings = Settings.load(Path(raw) / "library")
            service = BootstrapService(settings)
            starters = service.status()["starters"]
            self.assertGreaterEqual(len(starters), 3)
            self.assertTrue(all(not item["installed"] for item in starters))

            def add(url: str, name: str | None = None, tracked_ref: str | None = None):
                if name == "anthropic-skills":
                    raise RuntimeError("offline")
                return {"id": name, "name": name, "url": url}

            with patch.object(service.sources, "add", side_effect=add), patch.object(
                service.scanner, "scan", return_value=[{"valid": 1}]
            ):
                result = service.install_starters(
                    ["openai-plugins", "anthropic-skills", "superpowers"]
                )

            self.assertEqual(result["installed"], 2)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(len(result["results"]), 3)


if __name__ == "__main__":
    unittest.main()
