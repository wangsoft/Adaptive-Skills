from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from adaptive_skills.config import Settings
from adaptive_skills.database import SCHEMA_VERSION, Database
from adaptive_skills.sources import SourceManager


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_catalog_gains_remote_policy_without_losing_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            settings = Settings.load(Path(raw) / "library")
            settings.ensure()
            connection = sqlite3.connect(settings.database)
            connection.executescript(
                """
                CREATE TABLE sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    url TEXT,
                    local_path TEXT NOT NULL UNIQUE,
                    tracked_ref TEXT,
                    head_sha TEXT,
                    status TEXT NOT NULL DEFAULT 'registered',
                    last_scanned_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO sources(
                    id, name, local_path, status, created_at, updated_at
                ) VALUES (
                    'old-source', 'old-source', '/tmp/old-source',
                    'registered', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
                );
                """
            )
            connection.commit()
            connection.close()

            manager = SourceManager(settings)
            migrated = manager.get("old-source")
            self.assertEqual(migrated["update_policy"], "remote")
            self.assertEqual(
                manager.set_update_policy("old-source", "local")["update_policy"],
                "local",
            )

            with Database(settings).transaction() as migrated_database:
                version = migrated_database.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
            self.assertEqual(int(version), SCHEMA_VERSION)
            with Database(settings).transaction() as migrated_database:
                annotation_columns = {
                    row[1]
                    for row in migrated_database.execute(
                        "PRAGMA table_info(annotations)"
                    )
                }
                evaluation_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_evaluations'"
                ).fetchone()
                project_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='managed_projects'"
                ).fetchone()
                audit_review_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_reviews'"
                ).fetchone()
                evaluation_insight_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_evaluation_insights'"
                ).fetchone()
                profile_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_profiles'"
                ).fetchone()
                profile_entry_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_profile_entries'"
                ).fetchone()
                evaluation_columns = {
                    row[1]
                    for row in migrated_database.execute(
                        "PRAGMA table_info(llm_evaluations)"
                    )
                }
            self.assertIn("content_hash", annotation_columns)
            self.assertIsNotNone(evaluation_table)
            self.assertIsNotNone(project_table)
            self.assertIsNotNone(audit_review_table)
            self.assertIsNotNone(evaluation_insight_table)
            self.assertIsNotNone(profile_table)
            self.assertIsNotNone(profile_entry_table)
            self.assertIn("profile_id", evaluation_columns)


if __name__ == "__main__":
    unittest.main()
