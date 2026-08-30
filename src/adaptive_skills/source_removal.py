from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Database, utc_now
from .errors import ConflictError, NotFoundError, ValidationError
from .operation_lock import serialized_catalog_operation
from .projects import ProjectManager
from .scanner import CatalogScanner
from .sources import SourceManager


PREVIEW_DIGEST = re.compile(r"^[0-9a-f]{64}$")
BLOCKING_STATES = {"project-drift", "replaced"}


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SourceRemovalService:
    """Preview-bound soft removal for catalog sources.

    Removing a source never deletes or moves its repository. The source row and
    Skill records remain recoverable, but removed Skills are inactive and absent
    from FTS, scans, refreshes, and automatic discovery.
    """

    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)
        self.sources = SourceManager(settings, self.database)
        self.projects = ProjectManager(settings)

    def _project_impact(
        self, item: dict[str, Any], skill_ids: set[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        references: list[dict[str, Any]] = []
        inaccessible: list[dict[str, Any]] = []
        for project in self.projects.list_projects():
            if project["status"] != "active":
                inaccessible.append(
                    {
                        "id": project["id"],
                        "display_name": project["display_name"],
                        "path": project["path"],
                        "status": project["status"],
                        "problem": project.get("problem"),
                    }
                )
                continue
            root = Path(project["path"])
            try:
                manifest = self.projects.load_manifest(root)
                status = self.projects.status(root)
            except (OSError, ValidationError) as exc:
                inaccessible.append(
                    {
                        "id": project["id"],
                        "display_name": project["display_name"],
                        "path": project["path"],
                        "status": "invalid",
                        "problem": str(exc),
                    }
                )
                continue
            states = {entry["path"]: entry for entry in status["entries"]}
            entries: list[dict[str, Any]] = []
            for entry in manifest["entries"]:
                if entry.get("source_id") != item["id"] and entry.get(
                    "skill_id"
                ) not in skill_ids:
                    continue
                current = states.get(entry["path"], {})
                entries.append(
                    {
                        "skill_id": entry["skill_id"],
                        "name": entry.get("name") or current.get("name"),
                        "path": entry["path"],
                        "mode": entry.get("mode"),
                        "state": current.get("state", "catalog-missing"),
                        "restores_external": bool(entry.get("adopted_backup")),
                    }
                )
            if entries:
                references.append(
                    {
                        "project_id": project["id"],
                        "project_path": project["path"],
                        "display_name": project["display_name"],
                        "project_kind": project["project_kind"],
                        "entries": entries,
                    }
                )
        return references, inaccessible

    def preview(self, source: str) -> dict[str, Any]:
        item = self.sources.get(source)
        with self.database.transaction() as connection:
            skills = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, name, rel_path
                    FROM skills
                    WHERE source_id = ? AND active = 1
                    ORDER BY name COLLATE NOCASE, rel_path, id
                    """,
                    (item["id"],),
                )
            ]
        skill_ids = {skill["id"] for skill in skills}
        references, inaccessible = self._project_impact(item, skill_ids)

        reference_entries = [
            entry for project in references for entry in project["entries"]
        ]
        payload = {
            "source": {
                "id": item["id"],
                "name": item["name"],
                "url": item.get("url"),
                "local_path": item["local_path"],
                "status": item["status"],
                "head_sha": item.get("head_sha"),
                "updated_at": item["updated_at"],
            },
            "skills": skills,
            "references": references,
            "inaccessible_projects": inaccessible,
        }
        return {
            **payload,
            "skill_count": len(skills),
            "affected_project_count": len(references),
            "reference_count": len(reference_entries),
            "symlink_count": sum(
                entry["mode"] == "symlink" for entry in reference_entries
            ),
            "copy_count": sum(entry["mode"] == "copy" for entry in reference_entries),
            "restore_count": sum(
                entry["restores_external"] for entry in reference_entries
            ),
            "blocker_count": sum(
                entry["state"] in BLOCKING_STATES for entry in reference_entries
            ),
            "repository_retained": True,
            "repository_path": item["local_path"],
            "preview_digest": _digest(payload),
        }

    @serialized_catalog_operation
    def remove(
        self,
        source: str,
        *,
        cleanup_references: bool = True,
        expected_digest: str,
    ) -> dict[str, Any]:
        if not PREVIEW_DIGEST.fullmatch(expected_digest):
            raise ValidationError("A valid removal preview digest is required")
        preview = self.preview(source)
        if preview["preview_digest"] != expected_digest:
            raise ConflictError(
                "The source or a managed project changed after the removal preview; review the impact again"
            )
        if cleanup_references and preview["blocker_count"]:
            raise ConflictError(
                "Refusing to clean changed managed references; resolve their drift from the Projects page or keep references"
            )

        cleaned_projects: list[dict[str, Any]] = []
        restored_count = 0
        if cleanup_references:
            for reference in preview["references"]:
                result = self.projects.unlink(
                    reference["project_path"],
                    skill_ids=[entry["skill_id"] for entry in reference["entries"]],
                )
                restored_count += len(result["restored"])
                cleaned_projects.append(
                    {
                        "project_id": reference["project_id"],
                        "project_path": reference["project_path"],
                        "display_name": reference["display_name"],
                        "removed_count": len(result["removed"]),
                        "restored_count": len(result["restored"]),
                    }
                )

        source_state = preview["source"]
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT status, updated_at, head_sha FROM sources WHERE id = ?",
                (source_state["id"],),
            ).fetchone()
            if (
                current is None
                or current["status"] == "removed"
                or current["updated_at"] != source_state["updated_at"]
                or current["head_sha"] != source_state["head_sha"]
            ):
                raise ConflictError(
                    "The source changed during reference cleanup; it remains active and can be reviewed again"
                )
            connection.execute(
                "DELETE FROM skill_fts WHERE skill_id IN (SELECT id FROM skills WHERE source_id = ?)",
                (source_state["id"],),
            )
            connection.execute(
                "UPDATE skills SET active = 0, updated_at = ? WHERE source_id = ?",
                (now, source_state["id"]),
            )
            connection.execute(
                "UPDATE sources SET status = 'removed', updated_at = ? WHERE id = ?",
                (now, source_state["id"]),
            )
        return {
            "removed": True,
            "source_id": source_state["id"],
            "source_name": source_state["name"],
            "repository_retained": True,
            "repository_path": source_state["local_path"],
            "cleanup_references": cleanup_references,
            "cleaned_project_count": len(cleaned_projects),
            "cleaned_reference_count": sum(
                project["removed_count"] for project in cleaned_projects
            ),
            "restored_external_count": restored_count,
            "kept_reference_count": 0
            if cleanup_references
            else preview["reference_count"],
            "cleaned_projects": cleaned_projects,
            "inaccessible_projects": preview["inaccessible_projects"],
        }

    @serialized_catalog_operation
    def restore(self, source: str) -> dict[str, Any]:
        item = self.sources.get(source, include_removed=True)
        if item["status"] != "removed":
            raise ConflictError(f"Source is not removed: {item['name']}")
        path = Path(item["local_path"])
        if not path.is_dir():
            raise NotFoundError(
                f"The retained source directory is missing and cannot be restored: {path}"
            )
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE sources SET status = 'registered', updated_at = ? WHERE id = ?",
                (now, item["id"]),
            )
        try:
            scan = CatalogScanner(self.settings, self.database).scan(item["id"])[0]
        except Exception:
            with self.database.transaction() as connection:
                connection.execute(
                    "DELETE FROM skill_fts WHERE skill_id IN (SELECT id FROM skills WHERE source_id = ?)",
                    (item["id"],),
                )
                connection.execute(
                    "UPDATE skills SET active = 0 WHERE source_id = ?", (item["id"],)
                )
                connection.execute(
                    "UPDATE sources SET status = 'removed', updated_at = ? WHERE id = ?",
                    (utc_now(), item["id"]),
                )
            raise
        return {"restored": True, "source": self.sources.get(item["id"]), "scan": scan}

    def preview_forget(self, source: str) -> dict[str, Any]:
        item = self.sources.get(source, include_removed=True)
        if item["status"] != "removed":
            raise ConflictError(
                f"Source must be soft-removed before its catalog history can be forgotten: {item['name']}"
            )
        with self.database.transaction() as connection:
            skills = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, name, rel_path, content_hash, tree_hash, updated_at
                    FROM skills
                    WHERE source_id = ?
                    ORDER BY name COLLATE NOCASE, rel_path, id
                    """,
                    (item["id"],),
                )
            ]
            skill_ids = [skill["id"] for skill in skills]
            profile_locators = (
                [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT profile_id, position, skill_id
                        FROM skill_profile_entries
                        WHERE skill_id IN ({','.join('?' for _ in skill_ids)})
                        ORDER BY profile_id, position
                        """,
                        skill_ids,
                    )
                ]
                if skill_ids
                else []
            )
            history = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM annotations WHERE skill_id IN
                        (SELECT id FROM skills WHERE source_id = ?)) AS annotation_count,
                    (SELECT count(*) FROM audit_reviews WHERE skill_id IN
                        (SELECT id FROM skills WHERE source_id = ?)) AS audit_review_count,
                    (SELECT count(*) FROM llm_evaluations WHERE skill_id IN
                        (SELECT id FROM skills WHERE source_id = ?)) AS evaluation_count,
                    (SELECT count(*) FROM scan_runs WHERE source_id = ?) AS scan_run_count
                """,
                (item["id"], item["id"], item["id"], item["id"]),
            ).fetchone()
        references, inaccessible = self._project_impact(item, set(skill_ids))
        reference_entries = [
            entry for project in references for entry in project["entries"]
        ]
        repository_exists = Path(item["local_path"]).is_dir()
        payload = {
            "source": {
                "id": item["id"],
                "name": item["name"],
                "url": item.get("url"),
                "local_path": item["local_path"],
                "status": item["status"],
                "head_sha": item.get("head_sha"),
                "updated_at": item["updated_at"],
            },
            "skills": skills,
            "references": references,
            "inaccessible_projects": inaccessible,
            "profile_locators": profile_locators,
            "history": dict(history),
            "repository_exists": repository_exists,
        }
        return {
            **payload,
            "skill_count": len(skills),
            "affected_project_count": len(references),
            "reference_count": len(reference_entries),
            "profile_locator_count": len(profile_locators),
            "blocker_count": len(reference_entries) + len(inaccessible),
            "repository_retained": True,
            "repository_path": item["local_path"],
            "preview_digest": _digest(payload),
        }

    @serialized_catalog_operation
    def forget(self, source: str, *, expected_digest: str) -> dict[str, Any]:
        if not PREVIEW_DIGEST.fullmatch(expected_digest):
            raise ValidationError("A valid forget preview digest is required")
        preview = self.preview_forget(source)
        if preview["preview_digest"] != expected_digest:
            raise ConflictError(
                "The removed source or a managed project changed after the forget preview; review the impact again"
            )
        if preview["reference_count"]:
            raise ConflictError(
                "Refusing to forget a source with managed project references; unlink those Skills first"
            )
        if preview["inaccessible_projects"]:
            raise ConflictError(
                "Refusing to forget while registered projects cannot be inspected; reconnect or remove those project records first"
            )

        source_state = preview["source"]
        skill_ids = [skill["id"] for skill in preview["skills"]]
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT status, updated_at, head_sha FROM sources WHERE id = ?",
                (source_state["id"],),
            ).fetchone()
            if (
                current is None
                or current["status"] != "removed"
                or current["updated_at"] != source_state["updated_at"]
                or current["head_sha"] != source_state["head_sha"]
            ):
                raise ConflictError(
                    "The removed source changed during confirmation; review the forget impact again"
                )
            connection.execute(
                "DELETE FROM skill_fts WHERE skill_id IN (SELECT id FROM skills WHERE source_id = ?)",
                (source_state["id"],),
            )
            if skill_ids:
                connection.execute(
                    f"""
                    UPDATE skill_profile_entries
                    SET skill_id = NULL
                    WHERE skill_id IN ({','.join('?' for _ in skill_ids)})
                    """,
                    skill_ids,
                )
            connection.execute(
                "DELETE FROM sources WHERE id = ?", (source_state["id"],)
            )
        return {
            "forgotten": True,
            "source_id": source_state["id"],
            "source_name": source_state["name"],
            "deleted_skill_count": preview["skill_count"],
            "cleared_profile_locator_count": preview["profile_locator_count"],
            "deleted_history": preview["history"],
            "repository_retained": True,
            "repository_exists": preview["repository_exists"],
            "repository_path": source_state["local_path"],
        }

    def list_removed(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT src.*, count(s.id) AS skill_count
                FROM sources src
                LEFT JOIN skills s ON s.source_id = src.id
                WHERE src.status = 'removed'
                GROUP BY src.id
                ORDER BY src.updated_at DESC, src.name COLLATE NOCASE
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            repository_exists = Path(row["local_path"]).is_dir()
            result.append(
                {
                    **dict(row),
                    "skill_count": row["skill_count"] or 0,
                    "valid_count": 0,
                    "invalid_count": 0,
                    "elevated_risk_count": 0,
                    "pending_evaluation_count": 0,
                    "repository_exists": repository_exists,
                    "restorable": repository_exists,
                }
            )
        return result
