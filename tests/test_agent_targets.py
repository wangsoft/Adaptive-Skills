from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_skills.agent_targets import (
    get_agent_target,
    list_agent_targets,
    project_target_choices,
)


class AgentTargetTests(unittest.TestCase):
    def test_registry_is_the_single_path_contract_for_discovery_and_projects(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            (home / ".codex" / "skills").mkdir(parents=True)
            targets = {item["id"]: item for item in list_agent_targets(home)}

        self.assertEqual(
            set(targets),
            {"agents", "claude", "codex", "cursor", "gemini", "opencode"},
        )
        self.assertTrue(targets["codex"]["exists"])
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
