from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_skills.catalog import Catalog
from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError, ValidationError
from adaptive_skills.source_refresh import SourceRefreshService
from adaptive_skills.sources import SourceManager

from tests.helpers import commit_all, init_repo, write_skill
from tests.test_cli import run_cli


class SourceRefreshServiceTests(unittest.TestCase):
    def test_refresh_all_updates_scans_and_continues_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            origin = init_repo(root / "origin")
            write_skill(origin, "remote-skill", "Initial remote description.")
            initial_head = commit_all(origin, "initial")

            settings = Settings.load(root / "library")
            manager = SourceManager(settings)
            remote = manager.add(origin.as_uri(), name="remote-source")

            local_only = init_repo(settings.library / "local-only")
            write_skill(local_only, "local-skill", "A source without a remote URL.")
            commit_all(local_only, "local")
            manager.register(local_only, name="local-only")

            customized = init_repo(settings.library / "customized")
            skill_path = write_skill(
                customized,
                "custom-skill",
                "A locally maintained customized skill.",
            )
            commit_all(customized, "custom baseline")
            custom_source = manager.register(customized, name="customized")
            (skill_path / "local-note.txt").write_text(
                "intentional local customization", encoding="utf-8"
            )
            configured = manager.set_update_policy(custom_source["id"], "local")
            self.assertEqual(configured["update_policy"], "local")
            with self.assertRaisesRegex(ConflictError, "local-maintained"):
                manager.update(custom_source["id"])
            with self.assertRaises(ValidationError):
                manager.set_update_policy(custom_source["id"], "automatic")

            write_skill(origin, "remote-skill", "Updated remote description.")
            updated_head = commit_all(origin, "update")
            self.assertNotEqual(initial_head, updated_head)

            result = SourceRefreshService(settings).refresh_all()

            self.assertEqual(result["total"], 3)
            self.assertEqual(result["updated"], 1)
            self.assertEqual(result["unchanged"], 0)
            self.assertEqual(result["local"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(
                [item["status"] for item in result["results"]],
                ["local", "failed", "updated"],
            )

            local = next(
                item for item in result["results"] if item["status"] == "local"
            )
            self.assertEqual(local["source"], "customized")
            self.assertEqual(local["scan"]["valid"], 1)

            updated = next(
                item for item in result["results"] if item["status"] == "updated"
            )
            self.assertEqual(updated["source_id"], remote["id"])
            self.assertEqual(updated["before_sha"], initial_head)
            self.assertEqual(updated["after_sha"], updated_head)
            self.assertEqual(updated["scan"]["valid"], 1)

            failed = next(
                item for item in result["results"] if item["status"] == "failed"
            )
            self.assertEqual(failed["source"], "local-only")
            self.assertEqual(failed["type"], "ValidationError")
            self.assertIn("no remote URL", failed["error"])

            skills = Catalog(settings).list_skills()
            self.assertEqual(
                [skill["name"] for skill in skills],
                ["custom-skill", "remote-skill"],
            )
            self.assertEqual(
                skills[1]["description"], "Updated remote description."
            )

            repeated = SourceRefreshService(settings).refresh_all()
            self.assertEqual(repeated["updated"], 0)
            self.assertEqual(repeated["unchanged"], 1)
            self.assertEqual(repeated["local"], 1)
            self.assertEqual(repeated["failed"], 1)

            cli_result = run_cli(settings.library, "source", "refresh-all")
            self.assertEqual(cli_result["total"], 3)
            self.assertEqual(cli_result["unchanged"], 1)
            self.assertEqual(cli_result["local"], 1)
            self.assertEqual(cli_result["failed"], 1)


if __name__ == "__main__":
    unittest.main()
