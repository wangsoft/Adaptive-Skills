from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_skills.config import Settings
from adaptive_skills.scanner import CatalogScanner
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
