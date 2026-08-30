from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import Settings


SCHEMA_VERSION = 7


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self) -> sqlite3.Connection:
        self.settings.ensure()
        connection = sqlite3.connect(self.settings.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate(connection)
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                url TEXT,
                local_path TEXT NOT NULL UNIQUE,
                tracked_ref TEXT,
                update_policy TEXT NOT NULL DEFAULT 'remote'
                    CHECK(update_policy IN ('remote', 'local')),
                head_sha TEXT,
                status TEXT NOT NULL DEFAULT 'registered',
                last_scanned_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                rel_path TEXT NOT NULL,
                directory_name TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                license TEXT,
                compatibility TEXT,
                allowed_tools TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                body TEXT NOT NULL DEFAULT '',
                skill_md_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                tree_hash TEXT NOT NULL,
                line_count INTEGER NOT NULL DEFAULT 0,
                file_count INTEGER NOT NULL DEFAULT 0,
                valid INTEGER NOT NULL DEFAULT 0,
                validation_json TEXT NOT NULL DEFAULT '[]',
                audit_severity TEXT NOT NULL DEFAULT 'none',
                audit_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id, rel_path)
            );

            CREATE TABLE IF NOT EXISTS annotations (
                skill_id TEXT PRIMARY KEY REFERENCES skills(id) ON DELETE CASCADE,
                category_l1 TEXT,
                category_l2 TEXT,
                problem TEXT,
                use_case TEXT,
                score REAL,
                score_source TEXT,
                notes TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                review_status TEXT,
                content_hash TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_reviews (
                skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                finding_id TEXT NOT NULL,
                finding_digest TEXT NOT NULL,
                skill_tree_hash TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN ('reviewed_false_positive', 'confirmed_risk')),
                content_summary TEXT NOT NULL,
                note TEXT,
                reviewed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(skill_id, finding_id)
            );

            CREATE TABLE IF NOT EXISTS llm_evaluations (
                id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                profile_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_version TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                category_l1 TEXT,
                category_l2 TEXT,
                category_candidate INTEGER NOT NULL DEFAULT 0,
                problem TEXT,
                use_case TEXT,
                score REAL,
                dimensions_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL,
                status TEXT NOT NULL
                    CHECK(status IN ('proposed', 'applied', 'rejected', 'error')),
                raw_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                UNIQUE(skill_id, content_hash, profile_id, prompt_version)
            );

            CREATE TABLE IF NOT EXISTS managed_projects (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT,
                last_activity_at TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'missing', 'invalid'))
            );

            CREATE TABLE IF NOT EXISTS skill_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_profile_entries (
                profile_id TEXT NOT NULL
                    REFERENCES skill_profiles(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                skill_id TEXT,
                skill_name TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                rel_path TEXT,
                PRIMARY KEY(profile_id, position)
            );

            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                discovered INTEGER NOT NULL DEFAULT 0,
                valid INTEGER NOT NULL DEFAULT 0,
                invalid INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5(
                skill_id UNINDEXED,
                name,
                description,
                annotations,
                body,
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE INDEX IF NOT EXISTS idx_skills_source_active
                ON skills(source_id, active);
            CREATE INDEX IF NOT EXISTS idx_skills_name
                ON skills(name);
            CREATE INDEX IF NOT EXISTS idx_skills_risk
                ON skills(audit_severity, valid, active);
            CREATE INDEX IF NOT EXISTS idx_llm_evaluations_status
                ON llm_evaluations(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_llm_evaluations_skill_hash
                ON llm_evaluations(skill_id, content_hash);
            CREATE INDEX IF NOT EXISTS idx_audit_reviews_status
                ON audit_reviews(status, reviewed_at);
            CREATE INDEX IF NOT EXISTS idx_managed_projects_activity
                ON managed_projects(last_activity_at DESC, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_skill_profiles_updated
                ON skill_profiles(updated_at DESC, name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_skill_profile_entries_name
                ON skill_profile_entries(skill_name COLLATE NOCASE);
            """
        )
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sources)")
        }
        if "update_policy" not in source_columns:
            connection.execute(
                "ALTER TABLE sources ADD COLUMN update_policy TEXT NOT NULL DEFAULT 'remote'"
            )
        annotation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(annotations)")
        }
        if "content_hash" not in annotation_columns:
            connection.execute("ALTER TABLE annotations ADD COLUMN content_hash TEXT")
        evaluation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(llm_evaluations)")
        }
        if "profile_id" not in evaluation_columns:
            connection.executescript(
                """
                CREATE TABLE llm_evaluations_v4 (
                    id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                    content_hash TEXT NOT NULL,
                    profile_id TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL,
                    taxonomy_version TEXT NOT NULL,
                    category_l1 TEXT,
                    category_l2 TEXT,
                    category_candidate INTEGER NOT NULL DEFAULT 0,
                    problem TEXT,
                    use_case TEXT,
                    score REAL,
                    dimensions_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL,
                    status TEXT NOT NULL
                        CHECK(status IN ('proposed', 'applied', 'rejected', 'error')),
                    raw_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    UNIQUE(skill_id, content_hash, profile_id, prompt_version)
                );
                INSERT INTO llm_evaluations_v4(
                    id, skill_id, content_hash, profile_id, provider, model,
                    prompt_version, taxonomy_version, category_l1, category_l2,
                    category_candidate, problem, use_case, score, dimensions_json,
                    notes, tags_json, confidence, status, raw_json, error,
                    created_at, reviewed_at
                )
                SELECT id, skill_id, content_hash, 'legacy-' || provider, provider,
                       model, prompt_version, taxonomy_version, category_l1,
                       category_l2, category_candidate, problem, use_case, score,
                       dimensions_json, notes, tags_json, confidence, status,
                       raw_json, error, created_at, reviewed_at
                FROM llm_evaluations;
                DROP TABLE llm_evaluations;
                ALTER TABLE llm_evaluations_v4 RENAME TO llm_evaluations;
                """
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS llm_evaluation_insights (
                evaluation_id TEXT PRIMARY KEY
                    REFERENCES llm_evaluations(id) ON DELETE CASCADE,
                previous_score REAL,
                score_delta REAL,
                requires_review INTEGER NOT NULL DEFAULT 1
                    CHECK(requires_review IN (0, 1)),
                name_conflicts_json TEXT NOT NULL DEFAULT '[]',
                comparison_json TEXT NOT NULL DEFAULT '{}',
                recommendation TEXT NOT NULL DEFAULT 'review'
                    CHECK(recommendation IN ('review', 'ignore')),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_llm_insights_review
                ON llm_evaluation_insights(requires_review, recommendation);
            """
        )
        connection.execute(
            """
            UPDATE annotations
            SET content_hash = (
                SELECT skills.content_hash FROM skills
                WHERE skills.id = annotations.skill_id
            )
            WHERE content_hash IS NULL
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_evaluations_status ON llm_evaluations(status, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_evaluations_skill_hash ON llm_evaluations(skill_id, content_hash)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def json_value(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
