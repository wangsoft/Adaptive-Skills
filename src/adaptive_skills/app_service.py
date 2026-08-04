from __future__ import annotations

from typing import Any

from .catalog import Catalog
from .config import Settings
from .database import Database, utc_now
from .errors import ValidationError
from .evaluation import EvaluationService


APP_CONTRACT_VERSION = 4
RISK_LEVELS = ("none", "low", "medium", "high", "critical")


class AppService:
    """Stable, presentation-ready reads for local desktop clients.

    Mutating desktop actions continue to call the existing SourceManager,
    CatalogScanner, and ProjectManager CLI commands. This service only shapes
    read models so the desktop layer never needs to query SQLite directly.
    """

    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)
        self.catalog = Catalog(settings, self.database)
        self.evaluations = EvaluationService(settings, self.database)

    def snapshot(
        self, *, query: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        normalized_query = (query or "").strip()
        maximum = 100 if normalized_query else 5000
        if limit < 1 or limit > maximum:
            raise ValidationError(f"Snapshot limit must be between 1 and {maximum}")

        llm = self.evaluations.status()
        pending_counts = self.evaluations.pending_counts()
        with self.database.transaction() as connection:
            summary = self._summary(connection)
            sources = self._sources(connection, pending_counts)
            filters = self._filters(connection)
        summary["pending_evaluation_count"] = llm["pending_count"]
        summary["proposal_count"] = llm["proposal_count"]

        if normalized_query:
            results = self.catalog.search(normalized_query, limit=limit)
            skills = [self._compact_search_result(item) for item in results]
        else:
            skills = [
                self._compact_skill(skill)
                for skill in self.catalog.list_skills()[:limit]
            ]

        return {
            "contract_version": APP_CONTRACT_VERSION,
            "generated_at": utc_now(),
            "library": {
                "path": str(self.settings.library),
                "database": str(self.settings.database),
                "initialized": self.settings.database.is_file(),
            },
            "summary": summary,
            "sources": sources,
            "skills": skills,
            "filters": filters,
            "llm": {
                **llm,
                "proposals": self.evaluations.list(status="proposed", limit=100),
            },
            "query": normalized_query or None,
            "capabilities": {
                "source_add": True,
                "source_update": True,
                "source_scan": True,
                "source_policy": True,
                "project_plan": True,
                "project_apply": True,
                "project_sync": True,
                "project_unlink": True,
                "inventory_import": True,
                "inventory_export": True,
                "llm_config": True,
                "llm_profiles": True,
                "llm_evaluate": True,
                "llm_review": True,
                "project_registry": True,
            },
        }

    @staticmethod
    def _summary(connection: Any) -> dict[str, Any]:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM sources) AS source_count,
                count(*) AS skill_count,
                sum(CASE WHEN valid = 1 THEN 1 ELSE 0 END) AS valid_count,
                sum(CASE WHEN valid = 0 THEN 1 ELSE 0 END) AS invalid_count,
                (SELECT count(*) FROM annotations) AS annotated_count,
                (SELECT max(last_scanned_at) FROM sources) AS last_scanned_at
            FROM skills
            WHERE active = 1
            """
        ).fetchone()
        risk_counts = {level: 0 for level in RISK_LEVELS}
        for row in connection.execute(
            """
            SELECT audit_severity, count(*) AS total
            FROM skills WHERE active = 1 GROUP BY audit_severity
            """
        ):
            risk_counts[row["audit_severity"]] = row["total"]
        return {
            "source_count": counts["source_count"] or 0,
            "skill_count": counts["skill_count"] or 0,
            "valid_count": counts["valid_count"] or 0,
            "invalid_count": counts["invalid_count"] or 0,
            "annotated_count": counts["annotated_count"] or 0,
            "last_scanned_at": counts["last_scanned_at"],
            "risk_counts": risk_counts,
        }

    @staticmethod
    def _sources(
        connection: Any, pending_counts: dict[str, int]
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT src.*,
                   count(s.id) AS skill_count,
                   sum(CASE WHEN s.valid = 1 THEN 1 ELSE 0 END) AS valid_count,
                   sum(CASE WHEN s.valid = 0 THEN 1 ELSE 0 END) AS invalid_count,
                   sum(CASE WHEN s.audit_severity IN ('high', 'critical') THEN 1 ELSE 0 END) AS elevated_risk_count
            FROM sources src
            LEFT JOIN skills s ON s.source_id = src.id AND s.active = 1
            GROUP BY src.id
            ORDER BY src.name
            """
        ).fetchall()
        return [
            {
                **dict(row),
                "skill_count": row["skill_count"] or 0,
                "valid_count": row["valid_count"] or 0,
                "invalid_count": row["invalid_count"] or 0,
                "elevated_risk_count": row["elevated_risk_count"] or 0,
                "pending_evaluation_count": pending_counts.get(row["id"], 0),
            }
            for row in rows
        ]

    @staticmethod
    def _filters(connection: Any) -> dict[str, Any]:
        categories = [
            {
                "category_l1": row["category_l1"],
                "category_l2": row["category_l2"],
                "count": row["total"],
            }
            for row in connection.execute(
                """
                SELECT category_l1, category_l2, count(*) AS total
                FROM annotations
                WHERE category_l1 IS NOT NULL OR category_l2 IS NOT NULL
                GROUP BY category_l1, category_l2
                ORDER BY category_l1, category_l2
                """
            )
        ]
        return {
            "categories": categories,
            "risks": list(RISK_LEVELS),
        }

    @staticmethod
    def _compact_skill(skill: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id",
            "source_id",
            "source_name",
            "name",
            "description",
            "rel_path",
            "valid",
            "audit_severity",
            "category_l1",
            "category_l2",
            "score",
            "review_status",
            "tags",
            "license",
            "compatibility",
            "updated_at",
        )
        return {key: skill.get(key) for key in keys}

    @staticmethod
    def _compact_search_result(skill: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": skill["id"],
            "source_name": skill["source"],
            "name": skill["name"],
            "description": skill["description"],
            "rel_path": skill["rel_path"],
            "valid": skill["valid"],
            "audit_severity": skill["audit_severity"],
            "score": skill["score"],
            "annotation_score": skill["annotation_score"],
            "reason": skill["reason"],
        }
