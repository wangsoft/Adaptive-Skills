from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from adaptive_skills.catalog import Catalog
from adaptive_skills.app_service import AppService
from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError, NotFoundError
from adaptive_skills.projects import ProjectManager
from adaptive_skills.profiles import SkillProfileService
from adaptive_skills.scanner import CatalogScanner
from adaptive_skills.source_refresh import SourceRefreshService
from adaptive_skills.source_removal import SourceRemovalService
from adaptive_skills.sources import SourceManager

from tests.helpers import commit_all, init_repo, write_skill
from tests.test_cli import run_cli


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


class SourceRemovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.repository = init_repo(self.library / "source")
        write_skill(self.repository, "docs-skill", "Create technical documentation.")
        commit_all(self.repository, "source fixture")
        self.settings = Settings.load(self.library)
        self.sources = SourceManager(self.settings)
        self.source = self.sources.register(self.repository)
        CatalogScanner(self.settings).scan(self.source["id"])
        self.skill = Catalog(self.settings).list_skills()[0]
        self.projects = ProjectManager(self.settings)
        self.service = SourceRemovalService(self.settings)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_preview_and_remove_clean_managed_symlink_without_deleting_repo(self) -> None:
        project = self.root / "project"
        project.mkdir()
        self.projects.apply(project, [self.skill["id"]], mode="symlink")
        destination = project / ".agents" / "skills" / self.skill["name"]
        self.assertTrue(destination.is_symlink())

        preview = self.service.preview(self.source["id"])

        self.assertEqual(preview["source"]["name"], "source")
        self.assertEqual(preview["affected_project_count"], 1)
        self.assertEqual(preview["reference_count"], 1)
        self.assertEqual(preview["symlink_count"], 1)
        self.assertEqual(preview["copy_count"], 0)
        self.assertEqual(preview["blocker_count"], 0)
        self.assertTrue(preview["repository_retained"])
        self.assertEqual(preview["repository_path"], str(self.repository.resolve()))

        removed = self.service.remove(
            self.source["id"],
            cleanup_references=True,
            expected_digest=preview["preview_digest"],
        )

        self.assertEqual(removed["cleaned_reference_count"], 1)
        self.assertFalse(lexists(destination))
        self.assertTrue(self.repository.is_dir())
        self.assertEqual(self.sources.list(), [])
        self.assertEqual(Catalog(self.settings).list_skills(), [])
        snapshot = AppService(self.settings).snapshot()
        self.assertEqual(snapshot["summary"]["source_count"], 0)
        self.assertEqual(snapshot["sources"], [])
        self.assertEqual(snapshot["removed_sources"][0]["id"], self.source["id"])
        with self.assertRaises(NotFoundError):
            self.sources.get(self.source["id"])
        self.assertEqual(
            self.sources.get(self.source["id"], include_removed=True)["status"],
            "removed",
        )
        self.assertEqual(SourceRefreshService(self.settings).reconcile()["discovered"], 0)

    def test_remove_can_keep_references_and_restore_stable_ids(self) -> None:
        Catalog(self.settings).annotate(
            self.skill["id"], score=8.0, score_source="manual"
        )
        project = self.root / "project"
        project.mkdir()
        self.projects.apply(project, [self.skill["id"]], mode="symlink")
        destination = project / ".agents" / "skills" / self.skill["name"]
        preview = self.service.preview(self.source["id"])

        removed = self.service.remove(
            self.source["id"],
            cleanup_references=False,
            expected_digest=preview["preview_digest"],
        )

        self.assertEqual(removed["cleaned_reference_count"], 0)
        self.assertTrue(destination.is_symlink())
        status = self.projects.status(project)
        self.assertEqual(status["entries"][0]["state"], "catalog-missing")

        restored = self.service.restore(self.source["id"])

        self.assertEqual(restored["source"]["id"], self.source["id"])
        self.assertEqual(restored["scan"]["valid"], 1)
        self.assertEqual(Catalog(self.settings).list_skills()[0]["id"], self.skill["id"])
        self.assertEqual(Catalog(self.settings).get_skill(self.skill["id"])["score"], 8.0)
        self.assertEqual(self.projects.status(project)["entries"][0]["state"], "clean")

    def test_cleanup_refuses_drift_and_stale_preview(self) -> None:
        project = self.root / "project"
        project.mkdir()
        self.projects.apply(project, [self.skill["id"]], mode="copy")
        destination = project / ".agents" / "skills" / self.skill["name"]
        preview = self.service.preview(self.source["id"])
        (destination / "local-note.txt").write_text("keep me", encoding="utf-8")

        with self.assertRaisesRegex(ConflictError, "changed after the removal preview"):
            self.service.remove(
                self.source["id"],
                cleanup_references=True,
                expected_digest=preview["preview_digest"],
            )

        current = self.service.preview(self.source["id"])
        self.assertEqual(current["blocker_count"], 1)
        with self.assertRaisesRegex(ConflictError, "changed managed references"):
            self.service.remove(
                self.source["id"],
                cleanup_references=True,
                expected_digest=current["preview_digest"],
            )
        self.assertEqual(self.sources.get(self.source["id"])["status"], "scanned")
        self.assertTrue(destination.is_dir())

    def test_cli_preview_remove_and_restore(self) -> None:
        preview = run_cli(
            self.library, "source", "remove-preview", self.source["id"]
        )
        removed = run_cli(
            self.library,
            "source",
            "remove",
            self.source["id"],
            "--expected-digest",
            preview["preview_digest"],
        )
        self.assertTrue(removed["removed"])
        listed = run_cli(self.library, "source", "list")
        self.assertEqual(listed, [])

        restored = run_cli(
            self.library, "source", "restore", self.source["id"]
        )
        self.assertEqual(restored["source"]["id"], self.source["id"])
        self.assertEqual(restored["scan"]["valid"], 1)

    def test_forget_removed_source_deletes_only_catalog_history(self) -> None:
        profile = SkillProfileService(self.settings).save(
            name="Documentation", skill_ids=[self.skill["id"]]
        )
        removal = self.service.preview(self.source["id"])
        self.service.remove(
            self.source["id"],
            cleanup_references=True,
            expected_digest=removal["preview_digest"],
        )

        preview = self.service.preview_forget(self.source["id"])

        self.assertTrue(preview["repository_retained"])
        self.assertTrue(preview["repository_exists"])
        self.assertEqual(preview["reference_count"], 0)
        self.assertEqual(preview["profile_locator_count"], 1)
        forgotten = self.service.forget(
            self.source["id"], expected_digest=preview["preview_digest"]
        )

        self.assertTrue(forgotten["forgotten"])
        self.assertTrue(self.repository.is_dir())
        with self.assertRaises(NotFoundError):
            self.sources.get(self.source["id"], include_removed=True)
        with self.service.database.transaction() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM skill_fts WHERE skill_id = ?",
                    (self.skill["id"],),
                ).fetchone()[0],
                0,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT skill_id FROM skill_profile_entries WHERE profile_id = ?",
                    (profile["id"],),
                ).fetchone()[0]
            )
        discovered = self.sources.discover()
        self.assertEqual(len(discovered), 1)
        self.assertEqual(Path(discovered[0]["local_path"]), self.repository.resolve())
        self.assertNotEqual(discovered[0]["id"], self.source["id"])

    def test_forget_refuses_references_inaccessible_projects_and_stale_preview(self) -> None:
        project = self.root / "project"
        project.mkdir()
        self.projects.apply(project, [self.skill["id"]], mode="symlink")
        removal = self.service.preview(self.source["id"])
        self.service.remove(
            self.source["id"],
            cleanup_references=False,
            expected_digest=removal["preview_digest"],
        )

        referenced = self.service.preview_forget(self.source["id"])
        self.assertEqual(referenced["reference_count"], 1)
        with self.assertRaisesRegex(ConflictError, "managed project references"):
            self.service.forget(
                self.source["id"], expected_digest=referenced["preview_digest"]
            )

        self.projects.unlink(project, skill_ids=[self.skill["id"]])
        clear = self.service.preview_forget(self.source["id"])
        shutil.rmtree(project)
        inaccessible = self.service.preview_forget(self.source["id"])
        self.assertEqual(len(inaccessible["inaccessible_projects"]), 1)
        with self.assertRaisesRegex(ConflictError, "cannot be inspected"):
            self.service.forget(
                self.source["id"], expected_digest=inaccessible["preview_digest"]
            )
        with self.assertRaisesRegex(ConflictError, "changed after the forget preview"):
            self.service.forget(
                self.source["id"], expected_digest=clear["preview_digest"]
            )

    def test_removed_source_reports_whether_restore_is_possible(self) -> None:
        removal = self.service.preview(self.source["id"])
        self.service.remove(
            self.source["id"],
            cleanup_references=True,
            expected_digest=removal["preview_digest"],
        )
        self.assertTrue(self.service.list_removed()[0]["restorable"])
        shutil.rmtree(self.repository)
        removed = self.service.list_removed()[0]
        self.assertFalse(removed["repository_exists"])
        self.assertFalse(removed["restorable"])

    def test_cli_forget_requires_preview_digest(self) -> None:
        removal = self.service.preview(self.source["id"])
        self.service.remove(
            self.source["id"],
            cleanup_references=True,
            expected_digest=removal["preview_digest"],
        )
        preview = run_cli(
            self.library, "source", "forget-preview", self.source["id"]
        )
        forgotten = run_cli(
            self.library,
            "source",
            "forget",
            self.source["id"],
            "--expected-digest",
            preview["preview_digest"],
        )
        self.assertTrue(forgotten["forgotten"])

    def test_forget_serializes_against_project_manifest_mutations(self) -> None:
        removal = self.service.preview(self.source["id"])
        self.service.remove(
            self.source["id"],
            cleanup_references=True,
            expected_digest=removal["preview_digest"],
        )
        preview = self.service.preview_forget(self.source["id"])
        project = self.root / "concurrent-project"
        project.mkdir()
        entered = threading.Event()
        release = threading.Event()
        original_impact = self.service._project_impact

        def paused_impact(item: dict, skill_ids: set[str]):
            entered.set()
            self.assertTrue(release.wait(3))
            return original_impact(item, skill_ids)

        self.service._project_impact = paused_impact  # type: ignore[method-assign]
        forget_errors: list[Exception] = []
        apply_errors: list[Exception] = []

        def run_forget() -> None:
            try:
                self.service.forget(
                    self.source["id"], expected_digest=preview["preview_digest"]
                )
            except Exception as exc:  # pragma: no cover - asserted below
                forget_errors.append(exc)

        def run_apply() -> None:
            try:
                ProjectManager(self.settings).apply(project, [self.skill["id"]])
            except Exception as exc:
                apply_errors.append(exc)

        forget_thread = threading.Thread(target=run_forget)
        forget_thread.start()
        self.assertTrue(entered.wait(3))
        apply_thread = threading.Thread(target=run_apply)
        apply_thread.start()
        time.sleep(0.1)
        self.assertTrue(apply_thread.is_alive())
        self.assertFalse(ProjectManager.manifest_path(project).exists())

        release.set()
        forget_thread.join(3)
        apply_thread.join(3)

        self.assertFalse(forget_thread.is_alive())
        self.assertFalse(apply_thread.is_alive())
        self.assertEqual(forget_errors, [])
        self.assertEqual(len(apply_errors), 1)
        self.assertIsInstance(apply_errors[0], NotFoundError)
        self.assertFalse(ProjectManager.manifest_path(project).exists())

    def test_project_listing_serializes_registry_refresh_against_relink(self) -> None:
        original = self.root / "original-project"
        replacement = self.root / "replacement-project"
        original.mkdir()
        self.projects.apply(original, [self.skill["id"]])
        project_id = next(
            item["id"]
            for item in self.projects.list_projects()
            if item["path"] == str(original.resolve())
        )
        original.rename(replacement)
        entered = threading.Event()
        release = threading.Event()
        original_system_projects = self.projects._system_projects

        def paused_system_projects() -> list[dict]:
            entered.set()
            self.assertTrue(release.wait(3))
            return original_system_projects()

        self.projects._system_projects = (  # type: ignore[method-assign]
            paused_system_projects
        )
        listing_errors: list[Exception] = []
        relink_errors: list[Exception] = []

        def run_listing() -> None:
            try:
                self.projects.list_projects()
            except Exception as exc:  # pragma: no cover - asserted below
                listing_errors.append(exc)

        def run_relink() -> None:
            try:
                ProjectManager(self.settings).relink(project_id, replacement)
            except Exception as exc:  # pragma: no cover - asserted below
                relink_errors.append(exc)

        listing_thread = threading.Thread(target=run_listing)
        listing_thread.start()
        self.assertTrue(entered.wait(3))
        relink_thread = threading.Thread(target=run_relink)
        relink_thread.start()
        time.sleep(0.1)
        self.assertTrue(relink_thread.is_alive())

        release.set()
        listing_thread.join(3)
        relink_thread.join(3)

        self.assertEqual(listing_errors, [])
        self.assertEqual(relink_errors, [])
        current = next(
            item
            for item in ProjectManager(self.settings).list_projects()
            if item["id"] == project_id
        )
        self.assertEqual(current["path"], str(replacement.resolve()))
        self.assertEqual(current["status"], "active")


if __name__ == "__main__":
    unittest.main()
