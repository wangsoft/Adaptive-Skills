from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from adaptive_skills.config import Settings
from adaptive_skills.database import SCHEMA_VERSION, Database
from adaptive_skills.errors import ValidationError
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
            backups = list(
                settings.state_dir.glob(
                    f"backups/catalog-v0-before-v{SCHEMA_VERSION}-*.db"
                )
            )
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as backup:
                preserved = backup.execute(
                    "SELECT name FROM sources WHERE id = 'old-source'"
                ).fetchone()
                old_columns = {
                    row[1] for row in backup.execute("PRAGMA table_info(sources)")
                }
            self.assertEqual(preserved, ("old-source",))
            self.assertNotIn("update_policy", old_columns)
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
                custom_target_table = migrated_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='custom_agent_targets'"
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
            self.assertIsNotNone(custom_target_table)
            self.assertIn("profile_id", evaluation_columns)

    def test_newer_catalog_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            settings = Settings.load(Path(raw) / "library")
            settings.ensure()
            with sqlite3.connect(settings.database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE sentinel(value TEXT NOT NULL);
                    INSERT INTO schema_meta(key, value)
                    VALUES('schema_version', '999');
                    INSERT INTO sentinel(value) VALUES('preserve-me');
                    """
                )

            with self.assertRaisesRegex(ValidationError, "newer"):
                Database(settings).connect()

            with sqlite3.connect(settings.database) as connection:
                version = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                sentinel = connection.execute("SELECT value FROM sentinel").fetchone()[0]
            self.assertEqual(version, "999")
            self.assertEqual(sentinel, "preserve-me")
            self.assertFalse((settings.state_dir / "backups").exists())

    def test_previous_schema_version_is_backed_up_before_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            settings = Settings.load(Path(raw) / "library")
            settings.ensure()
            with sqlite3.connect(settings.database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE sentinel(value TEXT NOT NULL);
                    INSERT INTO schema_meta(key, value)
                    VALUES('schema_version', '8');
                    INSERT INTO sentinel(value) VALUES('before-v9');
                    """
                )

            with Database(settings).transaction() as migrated:
                version = migrated.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                custom_targets = migrated.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'custom_agent_targets'
                    """
                ).fetchone()

            backups = list(
                settings.state_dir.glob(
                    f"backups/catalog-v8-before-v{SCHEMA_VERSION}-*.db"
                )
            )
            self.assertEqual(version, str(SCHEMA_VERSION))
            self.assertIsNotNone(custom_targets)
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as backup:
                backup_version = backup.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                sentinel = backup.execute("SELECT value FROM sentinel").fetchone()[0]
                custom_targets = backup.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'custom_agent_targets'
                    """
                ).fetchone()
            self.assertEqual(backup_version, "8")
            self.assertEqual(sentinel, "before-v9")
            self.assertIsNone(custom_targets)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    def test_malformed_catalog_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            settings = Settings.load(Path(raw) / "library")
            settings.ensure()
            with sqlite3.connect(settings.database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    INSERT INTO schema_meta(key, value)
                    VALUES('schema_version', 'not-a-version');
                    """
                )

            with self.assertRaisesRegex(ValidationError, "version is invalid"):
                Database(settings).connect()


if __name__ == "__main__":
    unittest.main()
