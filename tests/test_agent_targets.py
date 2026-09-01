from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_skills.agent_targets import (
    AgentTarget,
    CustomAgentTargetService,
    get_agent_target,
    list_agent_targets,
    project_target_choices,
    targets_sharing_global_path,
    targets_sharing_project_path,
)
from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError, ValidationError


class AgentTargetTests(unittest.TestCase):
    def test_registry_is_the_single_path_contract_for_discovery_and_projects(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            (home / ".codex").mkdir(parents=True)
            targets = {item["id"]: item for item in list_agent_targets(home)}

        self.assertEqual(
            set(targets),
            {"agents", "claude", "codex", "cursor", "gemini", "opencode"},
        )
        self.assertTrue(targets["codex"]["detected"])
        self.assertFalse(targets["codex"]["exists"])
        self.assertEqual(
            targets["codex"]["detect_path"],
            str(home.resolve() / ".codex"),
        )
        self.assertEqual(
            targets["codex"]["global_path"],
            str(home.resolve() / ".codex" / "skills"),
        )
        self.assertEqual(targets["codex"]["project_path"], ".agents/skills")
        self.assertEqual(
            targets["opencode"]["global_path"],
            str(home.resolve() / ".config" / "opencode" / "skills"),
        )
        self.assertEqual(get_agent_target("auto").id, "agents")
        self.assertEqual(get_agent_target("universal").id, "agents")
        self.assertIn("auto", project_target_choices())

    def test_registry_exposes_scope_sync_and_shared_path_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = {
                item["id"]: item
                for item in list_agent_targets(Path(raw))
            }["codex"]

        self.assertEqual(target["supported_scopes"], ["global", "project"])
        self.assertTrue(target["supports_global"])
        self.assertTrue(target["supports_project"])
        self.assertEqual(target["default_sync_mode"], "symlink")
        self.assertEqual(target["sync_modes"], ["symlink", "copy"])
        self.assertEqual(target["global_group"], ".codex/skills")
        self.assertEqual(target["project_group"], ".agents/skills")
        self.assertTrue(target["built_in"])

        self.assertEqual(
            {item.id for item in targets_sharing_project_path("codex")},
            {"agents", "codex"},
        )
        self.assertEqual(
            [item.id for item in targets_sharing_global_path("codex")],
            ["codex"],
        )

    def test_registry_rejects_unsafe_or_inconsistent_adapter_specs(self) -> None:
        with self.assertRaisesRegex(ValidationError, "relative"):
            AgentTarget(
                id="unsafe",
                label="Unsafe",
                global_parts=(".unsafe", "skills"),
                project_path="../skills",
                preferred_rel_prefixes=("skills",),
            )

        with self.assertRaisesRegex(ValidationError, "default sync mode"):
            AgentTarget(
                id="invalid-mode",
                label="Invalid mode",
                global_parts=(".invalid", "skills"),
                project_path=".agents/skills",
                preferred_rel_prefixes=("skills",),
                sync_modes=("copy",),
                default_sync_mode="symlink",
            )

    def test_custom_targets_persist_and_are_removed_without_touching_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            library = home / "skills"
            detect_path = home / ".nova"
            global_path = detect_path / "skills"
            detect_path.mkdir(parents=True)
            settings = Settings.load(library)

            created = CustomAgentTargetService(settings, home=home).create(
                target_id="nova",
                label="Nova Agent",
                global_path=global_path,
                detect_path=detect_path,
                project_path=".nova/skills",
            )

            self.assertEqual(created["id"], "nova")
            self.assertFalse(created["built_in"])
            self.assertTrue(created["detected"])
            self.assertFalse(created["exists"])
            restarted = CustomAgentTargetService(settings, home=home)
            self.assertEqual(restarted.get("nova").project_path, ".nova/skills")
            self.assertIn("nova", {item["id"] for item in restarted.list()})

            global_path.mkdir()
            sentinel = global_path / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            removed = restarted.delete("nova")
            self.assertTrue(removed["deleted"])
            self.assertTrue(detect_path.is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertNotIn("nova", {item["id"] for item in restarted.list()})

    def test_custom_targets_reject_reserved_unsafe_and_managed_removal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            library = home / "skills"
            detect_path = home / ".nova"
            global_path = detect_path / "skills"
            detect_path.mkdir(parents=True)
            settings = Settings.load(library)
            service = CustomAgentTargetService(settings, home=home)

            with self.assertRaisesRegex(ValidationError, "reserved"):
                service.create(
                    target_id="codex",
                    label="Collision",
                    global_path=global_path,
                    detect_path=detect_path,
                    project_path=".nova/skills",
                )
            with self.assertRaisesRegex(ValidationError, "home"):
                service.create(
                    target_id="outside",
                    label="Outside",
                    global_path=root / "outside" / "skills",
                    detect_path=root / "outside",
                    project_path=".outside/skills",
                )
            with self.assertRaisesRegex(ValidationError, "managed Skill library"):
                service.create(
                    target_id="library-target",
                    label="Library Target",
                    global_path=library / "agent-skills",
                    detect_path=library,
                    project_path=".library/skills",
                )
            with self.assertRaisesRegex(ValidationError, "relative"):
                service.create(
                    target_id="project-root",
                    label="Project Root",
                    global_path=home / ".project-root" / "skills",
                    detect_path=home / ".project-root",
                    project_path=".",
                )
            blocked_global = home / ".blocked" / "skills"
            blocked_global.parent.mkdir()
            blocked_global.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "must be a directory"):
                service.create(
                    target_id="blocked-path",
                    label="Blocked Path",
                    global_path=blocked_global,
                    detect_path=blocked_global.parent,
                    project_path=".blocked/skills",
                )

            service.create(
                target_id="nova",
                label="Nova Agent",
                global_path=global_path,
                detect_path=detect_path,
                project_path=".nova/skills",
            )
            with self.assertRaisesRegex(ConflictError, "overlap"):
                service.create(
                    target_id="nova-child",
                    label="Nova Child",
                    global_path=global_path / "nested",
                    detect_path=global_path,
                    project_path=".nova-child/skills",
                )
            manifest_path = global_path / ".adaptive-skills" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                '{"schema":"adaptive-skills-project/1","entries":[{"skill_id":"managed"}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "managed Skills"):
                service.delete("nova")
            with self.assertRaisesRegex(ValidationError, "built-in"):
                service.delete("claude")
