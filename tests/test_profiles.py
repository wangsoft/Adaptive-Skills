from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError, ValidationError
from adaptive_skills.profiles import SkillProfileService
from adaptive_skills.scanner import CatalogScanner
from adaptive_skills.sources import SourceManager

from tests.helpers import commit_all, init_repo, write_skill


class SkillProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.library = root / "library"
        self.library.mkdir()
        self.repo = init_repo(self.library / "source")
        document = """---
name: shared-skill
description: A reusable workflow.
---

# Workflow

Run the reusable workflow.
"""
        for prefix in (Path(".agents/skills"), Path(".claude/skills")):
            destination = self.repo / prefix / "shared-skill"
            destination.mkdir(parents=True)
            (destination / "SKILL.md").write_text(document, encoding="utf-8")
        commit_all(self.repo)
        self.settings = Settings.load(self.library)
        source = SourceManager(self.settings).register(self.repo, name="source")
        CatalogScanner(self.settings).scan(source["id"])
        self.service = SkillProfileService(self.settings)
        skills = self.service.catalog.list_skills()
        self.agents_skill = next(
            skill
            for skill in skills
            if skill["rel_path"] == ".agents/skills/shared-skill"
        )
        self.claude_skill = next(
            skill
            for skill in skills
            if skill["rel_path"] == ".claude/skills/shared-skill"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profile_resolves_target_variant_previews_and_applies(self) -> None:
        profile = self.service.save(
            name="共享工作流",
            skill_ids=[self.agents_skill["id"]],
            description="跨 Agent 使用",
        )
        project = Path(self.temporary.name) / "project"
        project.mkdir()

        preview = self.service.preview(
            profile["id"], project, target="claude"
        )
        self.assertTrue(preview["can_apply"])
        self.assertEqual(preview["items"][0]["skill_id"], self.claude_skill["id"])
        self.assertEqual(preview["items"][0]["action"], "install")

        applied = self.service.apply(profile["id"], project, target="claude")
        self.assertTrue(applied["changed"])
        self.assertTrue((project / ".claude" / "skills" / "shared-skill").is_symlink())
        second = self.service.preview(profile["id"], project, target="claude")
        self.assertEqual(second["items"][0]["action"], "already-installed")

        deleted = self.service.delete(profile["id"])
        self.assertTrue(deleted["deleted"])
        self.assertTrue((project / ".claude" / "skills" / "shared-skill").exists())

    def test_capture_uses_portable_locators_and_conflict_blocks_all_changes(self) -> None:
        project = Path(self.temporary.name) / "project"
        project.mkdir()
        self.service.projects.apply(
            project, [self.agents_skill["id"]], target="auto", mode="symlink"
        )
        captured = self.service.capture(project, name="已捕获")
        self.assertNotIn(str(self.library), str(captured["entries"]))

        other = Path(self.temporary.name) / "other"
        collision = other / ".agents" / "skills" / "shared-skill"
        collision.mkdir(parents=True)
        (collision / "SKILL.md").write_text("external", encoding="utf-8")
        preview = self.service.preview(captured["id"], other, target="auto")
        self.assertFalse(preview["can_apply"])
        self.assertEqual(preview["items"][0]["action"], "conflict")
        with self.assertRaises(ConflictError):
            self.service.apply(captured["id"], other, target="auto")
        self.assertEqual((collision / "SKILL.md").read_text(), "external")

    def test_profile_can_apply_to_a_system_root_without_bypassing_manifest(self) -> None:
        profile = self.service.save(
            name="全局共享", skill_ids=[self.agents_skill["id"]]
        )
        global_root = Path(self.temporary.name) / "home" / ".codex" / "skills"
        global_root.mkdir(parents=True)
        scopes = [{
            "id": "codex",
            "label": "Codex",
            "path": str(global_root),
            "global_path": str(global_root),
            "project_path": ".agents/skills",
            "exists": True,
            "aliases": [],
            "preferred_rel_prefixes": [
                ".codex/skills", ".agents/skills", "skills", "plugin/skills"
            ],
        }]
        with patch(
            "adaptive_skills.projects.default_agent_roots", return_value=scopes
        ):
            preview = self.service.preview(profile["id"], global_root, target="root")
            self.assertTrue(preview["can_apply"])
            self.service.apply(profile["id"], global_root, target="root")
            self.assertTrue((global_root / "shared-skill").is_symlink())
            self.assertTrue(
                (global_root / ".adaptive-skills" / "manifest.json").is_file()
            )

    def test_portable_profile_export_preview_import_round_trip(self) -> None:
        profile = self.service.save(
            name="共享工作流",
            skill_ids=[self.agents_skill["id"]],
            description="跨目录复用",
        )
        output = Path(self.temporary.name) / "shared-profile.json"

        exported = self.service.export_file(profile["id"], output)
        document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exported["schema"], "adaptive-skills-profile/1")
        self.assertEqual(document["schema"], "adaptive-skills-profile/1")
        self.assertNotIn("skill_id", document["profile"]["entries"][0])
        self.assertNotIn(str(self.library), output.read_text(encoding="utf-8"))

        self.service.delete(profile["id"])
        preview = self.service.preview_import(output)
        self.assertEqual(preview["action"], "create")
        self.assertEqual(preview["counts"]["exact"], 1)
        self.assertTrue(preview["can_import"])

        imported = self.service.import_file(output)
        self.assertTrue(imported["changed"])
        self.assertNotEqual(imported["profile"]["id"], profile["id"])
        self.assertIsNone(imported["profile"]["entries"][0]["skill_id"])

        repeated = self.service.import_file(output)
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["action"], "already-exists")
        self.assertEqual(repeated["profile"]["id"], imported["profile"]["id"])

    def test_profile_transfer_rejects_unsafe_input_and_implicit_overwrite(self) -> None:
        profile = self.service.save(
            name="安全导出", skill_ids=[self.agents_skill["id"]]
        )
        output = Path(self.temporary.name) / "existing.json"
        output.write_text("keep me", encoding="utf-8")

        with self.assertRaises(ConflictError):
            self.service.export_file(profile["id"], output)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep me")
        overwritten = self.service.export_file(profile["id"], output, overwrite=True)
        self.assertTrue(overwritten["written"])

        before = len(self.service.list())
        unsafe = Path(self.temporary.name) / "unsafe.json"
        unsafe.write_text(
            json.dumps(
                {
                    "schema": "adaptive-skills-profile/1",
                    "profile": {
                        "name": "不安全",
                        "description": "",
                        "entries": [
                            {
                                "skill_name": "escape",
                                "source_name": "source",
                                "source_url": None,
                                "rel_path": "../../outside",
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValidationError):
            self.service.preview_import(unsafe)
        with self.assertRaises(ValidationError):
            self.service.import_file(unsafe)
        malformed = Path(self.temporary.name) / "malformed.json"
        malformed.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.service.preview_import(malformed)
        oversized = Path(self.temporary.name) / "oversized.json"
        oversized.write_bytes(b" " * 1_000_001)
        with self.assertRaises(ValidationError):
            self.service.preview_import(oversized)
        self.assertEqual(len(self.service.list()), before)

    def test_unresolved_portable_profile_can_be_imported_for_later_resolution(self) -> None:
        document = Path(self.temporary.name) / "future-profile.json"
        document.write_text(
            json.dumps(
                {
                    "schema": "adaptive-skills-profile/1",
                    "profile": {
                        "name": "未来能力",
                        "description": "来源稍后添加",
                        "entries": [
                            {
                                "skill_name": "future-skill",
                                "source_name": "future-source",
                                "source_url": "https://example.test/future.git",
                                "rel_path": "skills/future-skill",
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        preview = self.service.preview_import(document)
        self.assertTrue(preview["can_import"])
        self.assertEqual(preview["counts"]["missing"], 1)
        document.write_text(
            document.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ConflictError):
            self.service.import_file(document, expected_sha256=preview["sha256"])
        refreshed = self.service.preview_import(document)
        imported = self.service.import_file(
            document, expected_sha256=refreshed["sha256"]
        )
        project = Path(self.temporary.name) / "future-project"
        project.mkdir()
        apply_preview = self.service.preview(imported["profile"]["id"], project)
        self.assertFalse(apply_preview["can_apply"])
        self.assertEqual(apply_preview["counts"]["unresolved"], 1)
