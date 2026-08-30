from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_skills.config import Settings
from adaptive_skills.errors import ConflictError
from adaptive_skills.github_metadata import (
    GitHubRepositoryMetadata,
    github_repository_slug,
)
from adaptive_skills.sources import SourceManager
from tests.helpers import init_repo


class GitHubMetadataTests(unittest.TestCase):
    def test_repository_slug_supports_https_ssh_and_git_urls(self) -> None:
        expected = ("openai", "plugins")
        self.assertEqual(
            github_repository_slug("https://github.com/openai/plugins.git"),
            expected,
        )
        self.assertEqual(
            github_repository_slug("git@github.com:openai/plugins.git"),
            expected,
        )
        self.assertEqual(
            github_repository_slug("ssh://git@github.com/openai/plugins.git"),
            expected,
        )

    def test_repository_slug_rejects_non_github_and_nested_paths(self) -> None:
        self.assertIsNone(github_repository_slug("https://gitlab.com/openai/plugins.git"))
        self.assertIsNone(
            github_repository_slug("https://github.com/openai/plugins/tree/main")
        )
        self.assertIsNone(github_repository_slug(None))

    def test_refresh_persists_stars_and_reuses_fresh_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = init_repo(root / "repository")
            manager = SourceManager(Settings.load(root / "library"))
            source = manager.register(
                repository,
                name="plugins",
                url="https://github.com/openai/plugins.git",
            )

            with patch(
                "adaptive_skills.sources.fetch_github_repository_metadata",
                return_value=GitHubRepositoryMetadata(stars=12_345, etag='"etag"'),
            ) as fetch:
                refreshed = manager.refresh_github_metadata(source["id"])
                cached = manager.refresh_github_metadata(source["id"])

            self.assertEqual(refreshed["github_stars"], 12_345)
            self.assertEqual(refreshed["github_metadata_etag"], '"etag"')
            self.assertIsNotNone(refreshed["github_metadata_checked_at"])
            self.assertEqual(cached["github_stars"], 12_345)
            fetch.assert_called_once_with(
                "https://github.com/openai/plugins.git",
                etag=None,
            )

    def test_not_modified_preserves_the_previous_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = init_repo(root / "repository")
            manager = SourceManager(Settings.load(root / "library"))
            source = manager.register(
                repository,
                url="https://github.com/openai/plugins.git",
            )
            with manager.database.transaction() as connection:
                connection.execute(
                    "UPDATE sources SET github_stars = ?, github_metadata_etag = ? WHERE id = ?",
                    (321, '"old"', source["id"]),
                )

            with patch(
                "adaptive_skills.sources.fetch_github_repository_metadata",
                return_value=GitHubRepositoryMetadata(
                    stars=None,
                    etag='"new"',
                    not_modified=True,
                ),
            ):
                refreshed = manager.refresh_github_metadata(source["id"], force=True)

            self.assertEqual(refreshed["github_stars"], 321)
            self.assertEqual(refreshed["github_metadata_etag"], '"new"')

    def test_failed_refresh_preserves_previous_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = init_repo(root / "repository")
            manager = SourceManager(Settings.load(root / "library"))
            source = manager.register(
                repository,
                url="https://github.com/openai/plugins.git",
            )
            with manager.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE sources
                    SET github_stars = ?, github_metadata_etag = ?,
                        github_metadata_checked_at = ?
                    WHERE id = ?
                    """,
                    (321, '"old"', "2026-08-01T00:00:00+00:00", source["id"]),
                )

            with patch(
                "adaptive_skills.sources.fetch_github_repository_metadata",
                return_value=None,
            ):
                refreshed = manager.refresh_github_metadata(source["id"], force=True)

            self.assertEqual(refreshed["github_stars"], 321)
            self.assertEqual(refreshed["github_metadata_etag"], '"old"')
            self.assertEqual(
                refreshed["github_metadata_checked_at"],
                "2026-08-01T00:00:00+00:00",
            )

    def test_update_refreshes_metadata_even_when_the_checkout_is_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = init_repo(root / "repository")
            manager = SourceManager(Settings.load(root / "library"))
            source = manager.register(
                repository,
                url="https://github.com/openai/plugins.git",
            )
            (repository / "local-notes.txt").write_text("keep me", encoding="utf-8")

            with patch(
                "adaptive_skills.sources.fetch_github_repository_metadata",
                return_value=GitHubRepositoryMetadata(stars=456, etag='"etag"'),
            ):
                with self.assertRaises(ConflictError):
                    manager.update(source["id"])

            self.assertEqual(manager.get(source["id"])["github_stars"], 456)


if __name__ == "__main__":
    unittest.main()
