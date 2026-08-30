from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from adaptive_skills.catalog import Catalog
from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError
from adaptive_skills.scanner import CatalogScanner
from adaptive_skills.source_removal import SourceRemovalService
from adaptive_skills.sources import SourceManager

from tests.helpers import commit_all, init_repo, write_skill


class SourceManagerTests(unittest.TestCase):
    def test_add_clones_file_url_and_registers_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            origin = init_repo(root / "origin")
            write_skill(origin, "cloned-skill", "A skill cloned from a Git URL.")
            head = commit_all(origin)
            library = root / "library"
            settings = Settings.load(library)
            manager = SourceManager(settings)
            source = manager.add(origin.as_uri(), name="cloned-source")
            self.assertEqual(
                Path(source["local_path"]), (library / "cloned-source").resolve()
            )
            self.assertEqual(source["url"], origin.as_uri())
            self.assertEqual(source["head_sha"], head)
            result = CatalogScanner(settings).scan(source["id"])[0]
            self.assertEqual(result["valid"], 1)

    def test_add_recovers_a_registered_remote_when_its_directory_was_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            origin = init_repo(root / "owner" / "skills")
            write_skill(origin, "recoverable-skill", "Recover this Skill source.")
            commit_all(origin)
            settings = Settings.load(root / "library")
            manager = SourceManager(settings)
            original = manager.add(origin.as_uri(), name="managed-skills")
            CatalogScanner(settings).scan(original["id"])
            original_skill = Catalog(settings).list_skills()[0]
            Catalog(settings).annotate(
                original_skill["id"], score=8.0, score_source="smart"
            )
            shutil.rmtree(original["local_path"])

            recovered = manager.add(origin.as_uri(), name="managed-skills")
            scan = CatalogScanner(settings).scan(recovered["id"])[0]

            self.assertTrue(recovered["recovered"])
            self.assertEqual(recovered["id"], original["id"])
            self.assertTrue(Path(recovered["local_path"]).is_dir())
            self.assertEqual(scan["valid"], 1)
            restored_skill = Catalog(settings).list_skills()[0]
            self.assertEqual(restored_skill["id"], original_skill["id"])
            self.assertEqual(restored_skill["score"], 8.0)

    def test_missing_external_registration_is_not_recloned_outside_library(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = init_repo(root / "external" / "skills")
            write_skill(repository, "external-skill", "External registration.")
            commit_all(repository)
            settings = Settings.load(root / "library")
            manager = SourceManager(settings)
            source = manager.register(repository, url=repository.as_uri())
            shutil.rmtree(repository)

            with self.assertRaisesRegex(ConflictError, "outside the managed Skill library"):
                manager.add(source["url"])

    def test_discover_ignores_plain_directories_inside_a_parent_repository(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            library = init_repo(Path(raw) / "library")
            (library / "plain-directory").mkdir()
            nested_repo = init_repo(library / "real-source")
            write_skill(nested_repo, "real-skill", "A real nested Git source.")
            commit_all(nested_repo)
            manager = SourceManager(Settings.load(library))
            discovered = manager.discover()
            self.assertEqual([source["name"] for source in discovered], ["real-source"])

    def test_implicit_names_disambiguate_same_repo_basename_by_owner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = init_repo(root / "owner-one" / "skills")
            second = init_repo(root / "owner-two" / "skills")
            write_skill(first, "first-skill", "First implementation.")
            write_skill(second, "second-skill", "Second implementation.")
            commit_all(first)
            commit_all(second)
            manager = SourceManager(Settings.load(root / "library"))

            first_source = manager.add(first.as_uri())
            second_source = manager.add(second.as_uri())

            self.assertEqual(first_source["name"], "skills")
            self.assertEqual(second_source["name"], "owner-two-skills")
            self.assertEqual(
                Path(second_source["local_path"]).name, "owner-two-skills"
            )
            with self.assertRaises(ConflictError):
                manager.add(second.as_uri(), name="skills")

    def test_different_repo_can_reuse_removed_basename_with_owner_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = init_repo(root / "owner-one" / "skills")
            second = init_repo(root / "owner-two" / "skills")
            write_skill(first, "first-skill", "First implementation.")
            write_skill(second, "second-skill", "Second implementation.")
            commit_all(first)
            commit_all(second)
            settings = Settings.load(root / "library")
            manager = SourceManager(settings)
            original = manager.add(first.as_uri())
            CatalogScanner(settings).scan(original["id"])
            removal_service = SourceRemovalService(settings)
            removal = removal_service.preview(original["id"])
            removal_service.remove(
                original["id"],
                cleanup_references=True,
                expected_digest=removal["preview_digest"],
            )
            shutil.rmtree(original["local_path"])

            replacement = manager.add(second.as_uri())

            self.assertEqual(replacement["name"], "owner-two-skills")
            self.assertEqual(Path(replacement["local_path"]).name, "owner-two-skills")

    def test_transport_variants_are_the_same_remote_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = init_repo(root / "repository")
            write_skill(repository, "one", "One Skill.")
            commit_all(repository)
            manager = SourceManager(Settings.load(root / "library"))
            manager.register(
                repository,
                name="example",
                url="https://github.com/example/skills.git",
            )

            with self.assertRaisesRegex(ConflictError, "already registered"):
                manager.add("git@github.com:example/skills.git")

    def test_unborn_repository_cannot_impersonate_removed_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            original = init_repo(library / "source")
            manager = SourceManager(Settings.load(library))
            source = manager.register(original)
            service = SourceRemovalService(Settings.load(library))
            preview = service.preview(source["id"])
            service.remove(
                source["id"],
                cleanup_references=True,
                expected_digest=preview["preview_digest"],
            )
            shutil.rmtree(original)
            init_repo(original)

            result = manager.discover_detailed()

            self.assertEqual(result["sources"], [])
            self.assertEqual(len(result["failures"]), 1)
            self.assertIn("different repository", result["failures"][0]["error"])

    def test_manual_registration_disambiguates_an_implicit_name_collision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = init_repo(root / "owner-one" / "shared")
            second = init_repo(root / "owner-two" / "shared")
            write_skill(first, "first", "First shared repository.")
            write_skill(second, "second", "Second shared repository.")
            commit_all(first)
            commit_all(second)
            manager = SourceManager(Settings.load(root / "library"))
            manager.register(first)

            registered = manager.register(second)

            self.assertEqual(registered["name"], "shared-2")
