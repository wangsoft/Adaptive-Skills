from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_skills.catalog import Catalog
from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError, ValidationError
from adaptive_skills.source_refresh import SourceRefreshService
from adaptive_skills.source_removal import SourceRemovalService
from adaptive_skills.scanner import CatalogScanner
from adaptive_skills.sources import SourceManager

from tests.helpers import commit_all, init_repo, remove_tree, write_skill
from tests.test_cli import run_cli


class SourceRefreshServiceTests(unittest.TestCase):
    def test_reconcile_explains_reused_removed_path_then_accepts_it_after_forget(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            library = Path(raw) / "library"
            original = init_repo(library / "shared")
            write_skill(original, "old-skill", "Old repository.")
            commit_all(original, "old")
            settings = Settings.load(library)
            manager = SourceManager(settings)
            source = manager.register(original)
            CatalogScanner(settings).scan(source["id"])
            removal_service = SourceRemovalService(settings)
            removal = removal_service.preview(source["id"])
            removal_service.remove(
                source["id"],
                cleanup_references=True,
                expected_digest=removal["preview_digest"],
            )
            remove_tree(original)
            replacement = init_repo(library / "shared")
            write_skill(replacement, "new-skill", "Different repository.")
            commit_all(replacement, "new")

            blocked = SourceRefreshService(settings).reconcile()

            self.assertEqual(blocked["discovered"], 0)
            self.assertEqual(blocked["failed"], 1)
            self.assertIn("permanently forget", blocked["results"][0]["error"])
            forget = removal_service.preview_forget(source["id"])
            removal_service.forget(
                source["id"], expected_digest=forget["preview_digest"]
            )
            accepted = SourceRefreshService(settings).reconcile()
            self.assertEqual(accepted["discovered"], 1)
            self.assertEqual(accepted["failed"], 0)
            self.assertEqual(accepted["results"][0]["source"], "shared")

    def test_reconcile_discovers_and_scans_manually_cloned_sources(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            library = Path(raw) / "library"
            manual = init_repo(library / "manual-source")
            write_skill(manual, "manual-skill", "A manually cloned skill source.")
            commit_all(manual, "manual clone")
            settings = Settings.load(library)

            result = SourceRefreshService(settings).reconcile()

            self.assertEqual(result["discovered"], 1)
            self.assertEqual(result["scanned"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["results"][0]["source"], "manual-source")
            self.assertEqual(result["results"][0]["scan"]["valid"], 1)
            self.assertEqual(
                [skill["name"] for skill in Catalog(settings).list_skills()],
                ["manual-skill"],
            )
            self.assertEqual(SourceRefreshService(settings).reconcile()["discovered"], 0)

            second = init_repo(library / "second-source")
            write_skill(second, "second-skill", "A second manual source.")
            commit_all(second, "second clone")
            cli_result = run_cli(library, "source", "reconcile")
            self.assertEqual(cli_result["discovered"], 1)
            self.assertEqual(cli_result["scanned"], 1)

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
