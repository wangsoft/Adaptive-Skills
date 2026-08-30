from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .agent_scopes import default_agent_roots
from .agent_targets import get_agent_target
from .catalog import Catalog
from .config import Settings
from .database import Database, path_is_within, utc_now
from .errors import ConflictError, NotFoundError, ValidationError
from .operation_lock import serialized_catalog_operation
from .provider_skills import provider_skill_info
from .scanner import hash_skill_tree


MANIFEST_SCHEMA = "adaptive-skills-project/1"
MANIFEST_DIRECTORY = ".adaptive-skills"
MANIFEST_FILE = "manifest.json"
HISTORY_LIMIT = 100
HISTORY_ACTIONS = {"apply", "adopt", "sync", "unlink"}
SYSTEM_PROJECT_PREFIX = "system:"
EXTERNAL_BACKUP_DIRECTORY = Path(MANIFEST_DIRECTORY) / "external-backups"
BACKUP_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_project(project: str | Path) -> Path:
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise NotFoundError(f"Project directory does not exist: {root}")
    return root


def _safe_entry_path(project: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValidationError(f"Unsafe manifest entry path: {relative}")
    lexical = Path(os.path.abspath(project / value))
    try:
        lexical.relative_to(project)
    except ValueError as exc:
        raise ValidationError(f"Manifest entry escapes project: {relative}") from exc
    return lexical


class ProjectManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings)
        self.catalog = Catalog(settings, self.database)

    @staticmethod
    def manifest_path(project: Path) -> Path:
        return project / MANIFEST_DIRECTORY / MANIFEST_FILE

    def load_manifest(self, project: Path) -> dict[str, Any]:
        path = self.manifest_path(project)
        if not path.exists():
            return {
                "schema": MANIFEST_SCHEMA,
                "project": str(project),
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "entries": [],
                "history": [],
            }
        if path.stat().st_size > 2_000_000:
            raise ValidationError(f"Manifest is unexpectedly large: {path}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Invalid project manifest: {path}") from exc
        if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(
            manifest.get("entries"), list
        ):
            raise ValidationError(f"Unsupported project manifest: {path}")
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValidationError(f"Malformed project manifest entry: {path}")
            if not isinstance(entry.get("skill_id"), str) or entry.get("mode") not in {
                "symlink",
                "copy",
            }:
                raise ValidationError(f"Malformed project manifest entry: {path}")
            _safe_entry_path(project, entry["path"])
            adopted_backup = entry.get("adopted_backup")
            if adopted_backup is not None:
                if not isinstance(adopted_backup, str):
                    raise ValidationError(f"Malformed project manifest entry: {path}")
                backup_path = _safe_entry_path(project, adopted_backup)
                expected_root = Path(os.path.abspath(project / EXTERNAL_BACKUP_DIRECTORY))
                try:
                    backup_path.relative_to(expected_root)
                except ValueError:
                    raise ValidationError(f"Unsafe adopted backup path: {path}")
        history = manifest.get("history", [])
        if not isinstance(history, list):
            raise ValidationError(f"Malformed project manifest history: {path}")
        for event in history:
            if (
                not isinstance(event, dict)
                or event.get("action") not in HISTORY_ACTIONS
                or not isinstance(event.get("id"), str)
                or not isinstance(event.get("created_at"), str)
                or not isinstance(event.get("count"), int)
                or not isinstance(event.get("skill_ids"), list)
                or not all(isinstance(value, str) for value in event["skill_ids"])
                or not isinstance(event.get("skill_names"), list)
                or not all(isinstance(value, str) for value in event["skill_names"])
                or not isinstance(event.get("requirement"), (str, type(None)))
            ):
                raise ValidationError(f"Malformed project manifest history: {path}")
        manifest["history"] = history
        return manifest

    @staticmethod
    def _last_activity(manifest: dict[str, Any]) -> str | None:
        history = manifest.get("history", [])
        if history:
            value = history[-1].get("created_at")
            if isinstance(value, str):
                return value
        value = manifest.get("updated_at")
        return value if isinstance(value, str) else None

    def _register_manifest(
        self, project: Path, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        scope = self._system_scope(project)
        if scope is not None:
            return self._system_summary(scope, manifest)
        now = utc_now()
        project_id = str(uuid.uuid4())
        activity = self._last_activity(manifest)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO managed_projects(
                    id, path, display_name, created_at, updated_at,
                    last_seen_at, last_activity_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                ON CONFLICT(path) DO UPDATE SET
                    display_name=excluded.display_name,
                    updated_at=excluded.updated_at,
                    last_seen_at=excluded.last_seen_at,
                    last_activity_at=excluded.last_activity_at,
                    status='active'
                """,
                (
                    project_id,
                    str(project),
                    project.name or str(project),
                    now,
                    now,
                    now,
                    activity,
                ),
            )
            row = connection.execute(
                "SELECT * FROM managed_projects WHERE path = ?", (str(project),)
            ).fetchone()
        return self._summary(dict(row), project=project, manifest=manifest)

    @serialized_catalog_operation
    def register(self, project: str | Path) -> dict[str, Any]:
        root = _safe_project(project)
        scope = self._system_scope(root)
        if scope is not None:
            return self._system_summary(scope, self.load_manifest(root))
        if not self.manifest_path(root).is_file():
            raise ValidationError(
                "Only projects with an adaptive-skills manifest can be registered"
            )
        return self._register_manifest(root, self.load_manifest(root))

    @serialized_catalog_operation
    def list_projects(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_projects
                ORDER BY COALESCE(last_activity_at, updated_at) DESC,
                         display_name COLLATE NOCASE, id
                """
            ).fetchall()
        system_projects = self._system_projects()
        system_paths = {item["path"] for item in system_projects}
        results: list[dict[str, Any]] = system_projects
        observed: list[tuple[str, str | None, str | None, str]] = []
        for raw in rows:
            row = dict(raw)
            if row["path"] in system_paths:
                continue
            path = Path(row["path"])
            if not path.is_dir():
                summary = self._summary(row, status="missing")
            elif not self.manifest_path(path).is_file():
                summary = self._summary(
                    row,
                    status="invalid",
                    problem="Project manifest is missing",
                )
            else:
                try:
                    manifest = self.load_manifest(path)
                    summary = self._summary(row, project=path, manifest=manifest)
                except (OSError, ValidationError):
                    summary = self._summary(
                        row,
                        status="invalid",
                        problem="Project manifest is invalid",
                    )
            observed.append(
                (
                    summary["status"],
                    utc_now() if summary["status"] == "active" else None,
                    summary.get("last_activity_at"),
                    row["id"],
                )
            )
            results.append(summary)
        if observed:
            with self.database.transaction() as connection:
                connection.executemany(
                    """
                    UPDATE managed_projects
                    SET status = ?, last_seen_at = COALESCE(?, last_seen_at),
                        last_activity_at = COALESCE(?, last_activity_at)
                    WHERE id = ?
                    """,
                    observed,
                )
        return results

    @serialized_catalog_operation
    def forget(self, project_id_or_path: str | Path) -> dict[str, Any]:
        value = str(project_id_or_path)
        if value.startswith(SYSTEM_PROJECT_PREFIX) or self._system_scope_value(value):
            raise ValidationError("System Agent projects cannot be forgotten")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM managed_projects WHERE id = ?", (value,)
            ).fetchone()
            if row is None:
                candidate = str(Path(value).expanduser().resolve())
                row = connection.execute(
                    "SELECT * FROM managed_projects WHERE path = ?", (candidate,)
                ).fetchone()
            if row is None:
                raise NotFoundError(f"Unknown managed project: {value}")
            connection.execute("DELETE FROM managed_projects WHERE id = ?", (row["id"],))
        return {"forgotten": True, "id": row["id"], "path": row["path"]}

    @serialized_catalog_operation
    def relink(self, project_id: str, new_path: str | Path) -> dict[str, Any]:
        if project_id.startswith(SYSTEM_PROJECT_PREFIX):
            raise ValidationError("System Agent projects cannot be relinked")
        root = _safe_project(new_path)
        if not self.manifest_path(root).is_file():
            raise ValidationError(
                "The replacement project directory has no adaptive-skills manifest"
            )
        manifest = self.load_manifest(root)
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM managed_projects WHERE id = ?", (project_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"Unknown managed project: {project_id}")
            try:
                connection.execute(
                    """
                    UPDATE managed_projects
                    SET path = ?, display_name = ?, updated_at = ?, last_seen_at = ?,
                        last_activity_at = ?, status = 'active'
                    WHERE id = ?
                    """,
                    (
                        str(root),
                        root.name or str(root),
                        now,
                        now,
                        self._last_activity(manifest),
                        project_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    "That project directory is already registered"
                ) from exc
            row = connection.execute(
                "SELECT * FROM managed_projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._summary(dict(row), project=root, manifest=manifest)

    def _summary(
        self,
        row: dict[str, Any],
        *,
        project: Path | None = None,
        manifest: dict[str, Any] | None = None,
        status: str = "active",
        problem: str | None = None,
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        if project is not None and manifest is not None:
            entries = [
                self._entry_status(project, entry) for entry in manifest["entries"]
            ]
            history = manifest.get("history", [])
        return {
            "id": row["id"],
            "path": row["path"],
            "display_name": row["display_name"],
            "status": status,
            "entry_count": len(entries),
            "history_count": len(history),
            "clean": (
                all(entry["state"] == "clean" for entry in entries)
                if status == "active"
                else False
            ),
            "last_activity_at": (
                self._last_activity(manifest) if manifest is not None else row["last_activity_at"]
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "problem": problem,
            "project_kind": "project",
            "system_scope": None,
            "protected": False,
            "external_count": 0,
        }

    def _system_projects(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for scope in default_agent_roots():
            if not scope["exists"]:
                continue
            root = Path(scope["path"])
            try:
                results.append(self._system_summary(scope, self.load_manifest(root)))
            except (OSError, ValidationError):
                results.append(
                    self._system_summary(
                        scope,
                        {"entries": [], "history": []},
                        status="invalid",
                        problem="System project manifest is invalid",
                    )
                )
        return results

    def _system_summary(
        self,
        scope: dict[str, Any],
        manifest: dict[str, Any],
        *,
        status: str = "active",
        problem: str | None = None,
    ) -> dict[str, Any]:
        root = Path(scope["path"])
        manifest_present = self.manifest_path(root).is_file()
        entries = (
            [self._entry_status(root, entry) for entry in manifest["entries"]]
            if status == "active"
            else []
        )
        return {
            "id": f"{SYSTEM_PROJECT_PREFIX}{scope['id']}",
            "path": str(root),
            "display_name": f"{scope['label']} · 全局 Skills",
            "status": status,
            "entry_count": len(entries),
            "history_count": len(manifest.get("history", [])),
            "clean": status == "active"
            and all(entry["state"] == "clean" for entry in entries),
            "last_activity_at": self._last_activity(manifest)
            if manifest_present
            else None,
            "created_at": None,
            "updated_at": manifest.get("updated_at") if manifest_present else None,
            "problem": problem,
            "project_kind": "system",
            "system_scope": scope["id"],
            "protected": True,
            "external_count": self._external_count(root, manifest),
        }

    @staticmethod
    def _system_scope_value(value: str) -> dict[str, Any] | None:
        try:
            candidate = Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        return next(
            (
                scope
                for scope in default_agent_roots()
                if Path(scope["path"]).expanduser().resolve() == candidate
            ),
            None,
        )

    def _system_scope(self, project: Path) -> dict[str, Any] | None:
        return self._system_scope_value(str(project))

    @staticmethod
    def _external_count(root: Path, manifest: dict[str, Any]) -> int:
        managed = {entry["path"] for entry in manifest["entries"]}
        try:
            children = list(root.iterdir())
        except OSError:
            return 0
        return sum(
            1
            for child in children
            if not child.name.startswith(".")
            and child.name not in managed
            and (child / "SKILL.md").is_file()
        )

    @staticmethod
    def _append_history(
        manifest: dict[str, Any], action: str, **details: Any
    ) -> None:
        event = {
            "id": str(uuid.uuid4()),
            "action": action,
            "created_at": utc_now(),
            **details,
        }
        manifest["history"] = [*manifest.get("history", []), event][
            -HISTORY_LIMIT:
        ]

    def plan(
        self,
        project: str | Path,
        requirement: str | None = None,
        *,
        limit: int = 5,
        target: str = "auto",
        allow_risk: bool = False,
        category_l1: str | None = None,
        category_l2: str | None = None,
    ) -> dict[str, Any]:
        root = _safe_project(project)
        target_root = self._target_path(target, root)
        normalized_requirement = (requirement or "").strip()
        normalized_l1 = (category_l1 or "").strip()
        normalized_l2 = (category_l2 or "").strip()
        if normalized_requirement and normalized_l1:
            raise ValidationError(
                "Choose either a requirement search or category browsing"
            )
        if normalized_l2 and not normalized_l1:
            raise ValidationError("A level-two category requires a level-one category")
        if not normalized_requirement and not normalized_l1:
            raise ValidationError("A requirement or level-one category is required")
        manifest = self.load_manifest(root)
        by_path = {entry["path"]: entry for entry in manifest["entries"]}
        prefixes = self._recommendation_prefixes(target, root)
        if normalized_l1:
            recommendations = self._category_recommendations(
                normalized_l1,
                normalized_l2 or None,
                limit=limit,
                allow_risk=allow_risk,
                preferred_rel_prefixes=prefixes,
            )
            discovery_mode = "category"
            plan_requirement = "分类浏览：" + normalized_l1
            if normalized_l2:
                plan_requirement += " / " + normalized_l2
        else:
            recommendations = self.catalog.search(
                normalized_requirement,
                limit=limit,
                allow_risk=allow_risk,
                scope_root=self.settings.library,
                unique_names=True,
                preferred_rel_prefixes=prefixes,
            )
            discovery_mode = "requirement"
            plan_requirement = normalized_requirement
        for skill in recommendations:
            relative = (target_root / skill["name"]).as_posix()
            destination = _safe_entry_path(root, relative)
            managed = by_path.get(relative)
            if managed is not None:
                status = self._entry_status(root, managed)
                skill["project_selection_state"] = (
                    "installed"
                    if managed["skill_id"] == skill["id"]
                    else "managed-conflict"
                )
                skill["project_entry_state"] = status["state"]
                skill["project_entry_skill_id"] = managed["skill_id"]
                skill["project_entry_path"] = relative
            elif _lexists(destination):
                skill["project_selection_state"] = "path-conflict"
                skill["project_entry_state"] = None
                skill["project_entry_skill_id"] = None
                skill["project_entry_path"] = relative
            else:
                skill["project_selection_state"] = "available"
                skill["project_entry_state"] = None
                skill["project_entry_skill_id"] = None
                skill["project_entry_path"] = relative
        return {
            "project": str(root),
            "requirement": plan_requirement,
            "discovery_mode": discovery_mode,
            "category_l1": normalized_l1 or None,
            "category_l2": normalized_l2 or None,
            "target": str(root) if target_root == Path(".") else target_root.as_posix(),
            "library_root": str(self.settings.library),
            "recommendations": recommendations,
        }

    def _category_recommendations(
        self,
        category_l1: str,
        category_l2: str | None,
        *,
        limit: int,
        allow_risk: bool,
        preferred_rel_prefixes: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 100:
            raise ValidationError("Category browse limit must be between 1 and 100")
        library = self.settings.library.resolve()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for skill in self.catalog.list_skills():
            if not skill["valid"]:
                continue
            if not allow_risk and skill["audit_severity"] in {"high", "critical"}:
                continue
            if skill.get("category_l1") != category_l1:
                continue
            if category_l2 and skill.get("category_l2") != category_l2:
                continue
            source_root = Path(skill["source_path"])
            skill_root = source_root / skill["rel_path"]
            if not path_is_within(source_root, library) or not path_is_within(
                skill_root, library
            ):
                continue
            grouped.setdefault(skill["name"].casefold(), []).append(skill)

        recommendations: list[dict[str, Any]] = []
        reason_field = "category_l2" if category_l2 else "category_l1"
        reason_value = category_l2 or category_l1
        for variants in grouped.values():
            winner = min(
                variants,
                key=lambda item: (
                    self.catalog._rel_path_preference(
                        item["rel_path"], preferred_rel_prefixes
                    ),
                    -(item.get("score") or 0.0),
                    item["audit_severity"] in {"high", "critical"},
                    item["rel_path"].casefold(),
                    item["id"],
                ),
            )
            annotation_score = winner.get("score")
            recommendations.append(
                {
                    "id": winner["id"],
                    "name": winner["name"],
                    "description": winner["description"],
                    "source": winner["source_name"],
                    "source_name": winner["source_name"],
                    "source_url": winner.get("source_url"),
                    "source_stars": winner.get("source_stars"),
                    "rel_path": winner["rel_path"],
                    "valid": winner["valid"],
                    "audit_severity": winner["audit_severity"],
                    "format_issue_count": winner["format_issue_count"],
                    "capability_hint_count": winner["capability_hint_count"],
                    "unreviewed_risk_count": winner["unreviewed_risk_count"],
                    "confirmed_risk_count": winner["confirmed_risk_count"],
                    "false_positive_count": winner["false_positive_count"],
                    "category_l1": winner.get("category_l1"),
                    "category_l2": winner.get("category_l2"),
                    "score": annotation_score,
                    "annotation_score": annotation_score,
                    "reason": [
                        {
                            "field": reason_field,
                            "terms": [reason_value],
                            "contribution": 1.0,
                        }
                    ],
                    "variant_count": len(variants),
                }
            )
        recommendations.sort(
            key=lambda item: (
                item["annotation_score"] is None,
                -(item["annotation_score"] or 0.0),
                item["name"].casefold(),
                item["id"],
            )
        )
        return recommendations[:limit]

    def _recommendation_prefixes(
        self, target: str, project: Path
    ) -> tuple[str, ...]:
        scope = self._system_scope(project) if target == "root" else None
        scope_id = scope["id"] if scope is not None else target
        return get_agent_target(scope_id).preferred_rel_prefixes

    def activation_matrix(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValidationError("Activation matrix limit must be between 1 and 100")
        library = self.settings.library.resolve()
        skills = [
            skill
            for skill in self.catalog.list_skills()
            if path_is_within(Path(skill["source_path"]), library)
            and path_is_within(
                Path(skill["source_path"]) / skill["rel_path"], library
            )
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        normalized_query = (query or "").strip().casefold()
        for skill in skills:
            searchable = " ".join(
                str(skill.get(field) or "")
                for field in (
                    "name",
                    "description",
                    "problem",
                    "use_case",
                    "category_l1",
                    "category_l2",
                    "source_name",
                )
            ).casefold()
            if normalized_query and normalized_query not in searchable:
                continue
            grouped.setdefault(skill["name"].casefold(), []).append(skill)

        targets = default_agent_roots()
        target_context: dict[str, dict[str, Any]] = {}
        matrix_targets: list[dict[str, Any]] = []
        for target in targets:
            target_view = dict(target)
            root = Path(target["path"])
            if not target["exists"]:
                target_view["status"] = "unavailable"
                target_view["problem"] = None
                target_context[target["id"]] = {
                    "manifest": {"entries": []},
                    "external": {},
                    "problem": None,
                }
            else:
                try:
                    manifest = self.load_manifest(root)
                    external = {
                        item["name"].casefold(): item
                        for item in self._external_entries(root, manifest)
                    }
                    target_view["status"] = "available"
                    target_view["problem"] = None
                    target_context[target["id"]] = {
                        "manifest": manifest,
                        "external": external,
                        "problem": None,
                    }
                except (OSError, ValidationError) as exc:
                    target_view["status"] = "invalid"
                    target_view["problem"] = str(exc)
                    target_context[target["id"]] = {
                        "manifest": {"entries": []},
                        "external": {},
                        "problem": str(exc),
                    }
            matrix_targets.append(target_view)

        all_groups = sorted(
            grouped.values(),
            key=lambda variants: (variants[0]["name"].casefold(), variants[0]["id"]),
        )
        rows: list[dict[str, Any]] = []
        for variants in all_groups[:limit]:
            cells: list[dict[str, Any]] = []
            display_skill = min(
                variants,
                key=lambda item: (
                    not item["valid"],
                    item["audit_severity"] in {"high", "critical"},
                    -(item.get("score") or 0.0),
                    item["rel_path"].casefold(),
                    item["id"],
                ),
            )
            for target in matrix_targets:
                candidate = self._matrix_variant(variants, target["id"])
                cells.append(
                    self._activation_cell(
                        target,
                        target_context[target["id"]],
                        variants,
                        candidate,
                    )
                )
            rows.append(
                {
                    "name": display_skill["name"],
                    "description": display_skill["description"],
                    "variant_count": len(variants),
                    "cells": cells,
                }
            )
        return {
            "library_root": str(library),
            "query": query or "",
            "limit": limit,
            "total": len(all_groups),
            "targets": matrix_targets,
            "rows": rows,
        }

    def _matrix_variant(
        self, variants: list[dict[str, Any]], target_id: str
    ) -> dict[str, Any]:
        prefixes = get_agent_target(target_id).preferred_rel_prefixes
        return min(
            variants,
            key=lambda item: (
                not item["valid"],
                self.catalog._rel_path_preference(item["rel_path"], prefixes),
                item["audit_severity"] in {"high", "critical"},
                -(item.get("score") or 0.0),
                item["rel_path"].casefold(),
                item["id"],
            ),
        )

    def _activation_cell(
        self,
        target: dict[str, Any],
        context: dict[str, Any],
        variants: list[dict[str, Any]],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        base = {
            "target_id": target["id"],
            "skill_id": candidate["id"],
            "installed_skill_id": None,
            "adopt_skill_id": None,
            "content_match": None,
            "source_name": candidate["source_name"],
            "audit_severity": candidate["audit_severity"],
            "valid": candidate["valid"],
            "path": str(Path(target["path"]) / candidate["name"]),
            "detail_state": None,
            "read_only": False,
        }
        if target["status"] != "available":
            return {
                **base,
                "state": "unavailable",
                "detail_state": target.get("problem") or "directory-missing",
                "read_only": True,
            }
        manifest = context["manifest"]
        managed = next(
            (
                entry
                for entry in manifest["entries"]
                if (entry.get("name") or Path(entry["path"]).name).casefold()
                == candidate["name"].casefold()
            ),
            None,
        )
        if managed is not None:
            status = self._entry_status(Path(target["path"]), managed)
            return {
                **base,
                "state": "managed" if status["state"] == "clean" else "drift",
                "detail_state": status["state"],
                "installed_skill_id": managed["skill_id"],
                "path": str(Path(target["path"]) / managed["path"]),
            }
        external = context["external"].get(candidate["name"].casefold())
        if external is not None:
            variant_ids = {item["id"] for item in variants}
            match = next(
                (
                    item
                    for item in external["matches"]
                    if item["id"] == candidate["id"]
                ),
                next(
                    (
                        item
                        for item in external["matches"]
                        if item["id"] in variant_ids
                    ),
                    None,
                ),
            )
            matched_base = (
                {
                    **base,
                    "skill_id": match["id"],
                    "source_name": match["source_name"],
                    "audit_severity": match["audit_severity"],
                    "valid": match["valid"],
                }
                if match
                else base
            )
            return {
                **matched_base,
                "state": "external-match" if match else "external",
                "adopt_skill_id": match["id"] if match else None,
                "content_match": match["content_match"] if match else None,
                "path": str(Path(target["path"]) / external["path"]),
                "read_only": True,
            }
        destination = Path(target["path"]) / candidate["name"]
        if _lexists(destination):
            return {
                **base,
                "state": "external",
                "detail_state": "path-collision",
                "path": str(destination),
                "read_only": True,
            }
        return {**base, "state": "absent"}

    @serialized_catalog_operation
    def apply(
        self,
        project: str | Path,
        skill_ids: list[str],
        *,
        target: str = "auto",
        mode: str = "auto",
        requirement: str | None = None,
        allow_risk: bool = False,
    ) -> dict[str, Any]:
        root = _safe_project(project)
        if not skill_ids:
            raise ValidationError("At least one --skill is required")
        if mode not in {"auto", "symlink", "copy"}:
            raise ValidationError("Mode must be auto, symlink, or copy")
        target_root = self._target_path(target, root)
        manifest = self.load_manifest(root)
        by_path = {entry["path"]: entry for entry in manifest["entries"]}
        selected = [self.catalog.get_skill(skill_id) for skill_id in skill_ids]
        names = [skill["name"] for skill in selected]
        if len(set(names)) != len(names):
            raise ConflictError("Selected skills contain duplicate names")

        prepared: list[tuple[dict[str, Any], Path, Path, str]] = []
        for skill in selected:
            if not skill["valid"]:
                raise ValidationError(f"Refusing invalid skill: {skill['name']}")
            if not allow_risk and skill["audit_severity"] in {"high", "critical"}:
                raise ValidationError(
                    f"Refusing {skill['audit_severity']}-risk skill without --allow-risk: {skill['name']}"
                )
            source = (Path(skill["source_path"]) / skill["rel_path"]).resolve()
            if not source.is_dir() or not path_is_within(
                source, Path(skill["source_path"])
            ):
                raise ValidationError(
                    f"Catalog source path is missing or unsafe: {skill['name']}"
                )
            relative = (target_root / skill["name"]).as_posix()
            destination = _safe_entry_path(root, relative)
            managed = by_path.get(relative)
            if _lexists(destination) and managed is None:
                raise ConflictError(
                    f"Refusing to overwrite unmanaged project entry: {destination}"
                )
            if managed is not None and managed.get("skill_id") != skill["id"]:
                raise ConflictError(
                    f"Project path is owned by another catalog skill: {destination}"
                )
            prepared.append((skill, source, destination, relative))

        installed: list[dict[str, Any]] = []
        newly_created: list[tuple[Path, dict[str, Any]]] = []
        try:
            for skill, source, destination, relative in prepared:
                previous = by_path.get(relative)
                if previous and _lexists(destination):
                    state = self._entry_status(root, previous)
                    if state["state"] != "clean":
                        raise ConflictError(
                            f"Managed entry is not clean; use project sync first: {destination} ({state['state']})"
                        )
                    installed.append(previous)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                actual_mode = self._install(source, destination, mode)
                installed_hash, _ = hash_skill_tree(
                    destination if actual_mode == "copy" else source
                )
                entry = {
                    "skill_id": skill["id"],
                    "name": skill["name"],
                    "path": relative,
                    "mode": actual_mode,
                    "source_id": skill["source_id"],
                    "source_name": skill["source_name"],
                    "source_url": skill["source_url"],
                    "source_ref": skill["tracked_ref"],
                    "source_sha": skill["head_sha"],
                    "source_rel_path": skill["rel_path"],
                    "source_path": str(source),
                    "content_hash": skill["content_hash"],
                    "installed_tree_hash": installed_hash,
                    "requirement": requirement,
                    "risk_accepted": bool(
                        allow_risk and skill["audit_severity"] in {"high", "critical"}
                    ),
                    "applied_at": utc_now(),
                }
                by_path[relative] = entry
                installed.append(entry)
                newly_created.append((destination, entry))
        except Exception:
            for destination, entry in reversed(newly_created):
                if _lexists(destination):
                    self._remove_verified(destination, entry, force=True)
            raise

        previous_manifest = json.loads(json.dumps(manifest))
        manifest["entries"] = sorted(by_path.values(), key=lambda entry: entry["path"])
        manifest["updated_at"] = utc_now()
        self._append_history(
            manifest,
            "apply",
            count=len(selected),
            skill_ids=[skill["id"] for skill in selected],
            skill_names=names,
            requirement=requirement[:500] if requirement else None,
            target=target_root.as_posix(),
            modes=sorted({entry["mode"] for entry in installed}),
        )
        try:
            _atomic_json(self.manifest_path(root), manifest)
        except Exception:
            for destination, entry in reversed(newly_created):
                if _lexists(destination):
                    self._remove_verified(destination, entry, force=True)
            if self.manifest_path(root).is_file():
                _atomic_json(self.manifest_path(root), previous_manifest)
            raise
        self._register_manifest(root, manifest)
        return {
            "project": str(root),
            "installed": installed,
            "manifest": str(self.manifest_path(root)),
        }

    @serialized_catalog_operation
    def status(self, project: str | Path) -> dict[str, Any]:
        root = _safe_project(project)
        manifest = self.load_manifest(root)
        entries = [self._entry_status(root, entry) for entry in manifest["entries"]]
        scope = self._system_scope(root)
        managed = self.manifest_path(root).is_file()
        if managed:
            self._register_manifest(root, manifest)
        return {
            "project": str(root),
            "manifest": str(self.manifest_path(root)),
            "managed": managed,
            "entries": entries,
            "clean": all(entry["state"] == "clean" for entry in entries),
            "project_kind": "system" if scope is not None else "project",
            "system_scope": scope["id"] if scope is not None else None,
            "protected": scope is not None,
            "external_entries": self._external_entries(root, manifest)
            if scope is not None
            else [],
        }

    def _external_entries(
        self, root: Path, manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        managed = {entry["path"] for entry in manifest["entries"]}
        catalog_skills = self.catalog.list_skills()
        by_name: dict[str, list[dict[str, Any]]] = {}
        for skill in catalog_skills:
            by_name.setdefault(skill["name"].casefold(), []).append(skill)
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return []
        external: list[dict[str, Any]] = []
        scope = self._system_scope(root)
        for child in children:
            if (
                child.name.startswith(".")
                or child.name in managed
                or not (child / "SKILL.md").is_file()
            ):
                continue
            try:
                tree_hash, _ = hash_skill_tree(child)
            except OSError:
                tree_hash = None
            provider = provider_skill_info(
                root,
                child,
                scope["id"] if scope is not None else None,
            )
            candidates = [] if provider is not None else by_name.get(child.name.casefold(), [])
            matches = sorted(
                candidates,
                key=lambda skill: (
                    skill["tree_hash"] != tree_hash,
                    not skill["valid"],
                    skill["audit_severity"] in {"high", "critical"},
                    -(skill.get("score") or 0.0),
                    skill["source_name"].casefold(),
                    skill["id"],
                ),
            )
            external.append(
                {
                    "name": child.name,
                    "path": child.name,
                    "entry_type": "symlink" if child.is_symlink() else "directory",
                    "tree_hash": tree_hash,
                    "read_only": True,
                    "management_state": (
                        "provider-owned" if provider is not None else "external"
                    ),
                    "provider": provider["provider"] if provider else None,
                    "protected_reason": provider["reason"] if provider else None,
                    "migratable": provider is None and not child.is_symlink(),
                    "migration_mode": (
                        None
                        if provider is not None
                        else "associate-link"
                        if child.is_symlink()
                        else "backup-and-link"
                    ),
                    "matches": [
                        {
                            "id": skill["id"],
                            "name": skill["name"],
                            "source_name": skill["source_name"],
                            "audit_severity": skill["audit_severity"],
                            "valid": skill["valid"],
                            "content_match": skill["tree_hash"] == tree_hash,
                            "target_path": str(
                                Path(skill["source_path"]) / skill["rel_path"]
                            ),
                        }
                        for skill in matches
                    ],
                }
            )
        return external

    @serialized_catalog_operation
    def adopt(
        self,
        project: str | Path,
        entry_name: str,
        skill_id: str,
        *,
        allow_risk: bool = False,
        replace_content: bool = False,
        backup_token: str | None = None,
    ) -> dict[str, Any]:
        root = _safe_project(project)
        if self._system_scope(root) is None:
            raise ValidationError("External Skills can only be adopted in a system project")
        if (
            not entry_name
            or entry_name.startswith(".")
            or Path(entry_name).name != entry_name
            or len(Path(entry_name).parts) != 1
        ):
            raise ValidationError("External Skill name must be one safe directory name")
        destination = _safe_entry_path(root, entry_name)
        if not _lexists(destination) or not (destination / "SKILL.md").is_file():
            raise NotFoundError(f"External Skill does not exist: {entry_name}")

        scope = self._system_scope(root)
        provider = provider_skill_info(
            root,
            destination,
            scope["id"] if scope is not None else None,
        )
        if provider is not None:
            raise ValidationError(
                f"Refusing to adopt provider-owned {provider['provider']} Skill: {entry_name}"
            )

        manifest = self.load_manifest(root)
        if any(entry["path"] == entry_name for entry in manifest["entries"]):
            raise ConflictError(f"Skill is already managed: {entry_name}")
        skill = self.catalog.get_skill(skill_id)
        if skill["name"] != entry_name:
            raise ConflictError("Catalog Skill name does not match the external directory")
        if not skill["valid"]:
            raise ValidationError(f"Refusing invalid skill: {skill['name']}")
        if not allow_risk and skill["audit_severity"] in {"high", "critical"}:
            raise ValidationError(
                f"Refusing {skill['audit_severity']}-risk skill without --allow-risk: {skill['name']}"
            )
        source = (Path(skill["source_path"]) / skill["rel_path"]).resolve()
        if not source.is_dir() or not path_is_within(source, Path(skill["source_path"])):
            raise ValidationError(f"Catalog source path is missing or unsafe: {skill['name']}")
        source_hash, _ = hash_skill_tree(source)
        external_hash, _ = hash_skill_tree(destination)
        if source_hash != skill["tree_hash"]:
            raise ConflictError("Catalog source changed; scan the source before associating")
        if external_hash != source_hash and not replace_content:
            raise ConflictError("External Skill content does not exactly match the catalog Skill")

        original_entry_type = "symlink" if destination.is_symlink() else "directory"
        same_link = destination.is_symlink() and destination.resolve() == source
        backup_relative: str | None = None
        backup: Path | None = None
        if not same_link:
            if backup_token is not None and not BACKUP_TOKEN.fullmatch(backup_token):
                raise ValidationError("Backup token must contain only safe filename characters")
            backup_relative = (
                EXTERNAL_BACKUP_DIRECTORY
                / f"{backup_token or uuid.uuid4().hex}-{entry_name}"
            ).as_posix()
            backup = _safe_entry_path(root, backup_relative)
            if _lexists(backup):
                raise ConflictError(f"External Skill backup already exists: {backup}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, backup)
            try:
                self._install(source, destination, "symlink")
            except Exception:
                os.replace(backup, destination)
                raise

        entry = {
            "skill_id": skill["id"],
            "name": skill["name"],
            "path": entry_name,
            "mode": "symlink",
            "source_id": skill["source_id"],
            "source_name": skill["source_name"],
            "source_url": skill["source_url"],
            "source_ref": skill["tracked_ref"],
            "source_sha": skill["head_sha"],
            "source_rel_path": skill["rel_path"],
            "source_path": str(source),
            "content_hash": skill["content_hash"],
            "installed_tree_hash": source_hash,
            "requirement": None,
            "risk_accepted": bool(
                allow_risk and skill["audit_severity"] in {"high", "critical"}
            ),
            "replaced_external_content": external_hash != source_hash,
            "applied_at": utc_now(),
            "adopted_backup": backup_relative,
        }
        manifest["entries"] = sorted(
            [*manifest["entries"], entry], key=lambda item: item["path"]
        )
        manifest["updated_at"] = utc_now()
        self._append_history(
            manifest,
            "adopt",
            count=1,
            skill_ids=[skill["id"]],
            skill_names=[skill["name"]],
            requirement=None,
            target=".",
            modes=["symlink"],
            backup_path=backup_relative,
            source_path=str(source),
            original_entry_type=original_entry_type,
        )
        try:
            _atomic_json(self.manifest_path(root), manifest)
        except Exception:
            if not same_link and destination.is_symlink() and backup is not None:
                destination.unlink()
                os.replace(backup, destination)
            raise
        return {
            "project": str(root),
            "adopted": entry,
            "preserved_original": backup_relative is not None,
            "backup_path": (
                str(_safe_entry_path(root, backup_relative))
                if backup_relative is not None
                else None
            ),
        }

    @serialized_catalog_operation
    def history(self, project: str | Path, *, limit: int = 50) -> dict[str, Any]:
        if limit < 1 or limit > HISTORY_LIMIT:
            raise ValidationError(
                f"Project history limit must be between 1 and {HISTORY_LIMIT}"
            )
        root = _safe_project(project)
        manifest = self.load_manifest(root)
        if self.manifest_path(root).is_file():
            self._register_manifest(root, manifest)
        return {
            "project": str(root),
            "events": list(reversed(manifest.get("history", [])))[:limit],
        }

    @serialized_catalog_operation
    def sync(
        self,
        project: str | Path,
        *,
        force: bool = False,
        allow_risk: bool = False,
    ) -> dict[str, Any]:
        root = _safe_project(project)
        manifest = self.load_manifest(root)
        updated: list[dict[str, Any]] = []
        for entry in manifest["entries"]:
            destination = _safe_entry_path(root, entry["path"])
            status = self._entry_status(root, entry)
            if status["state"] == "clean":
                continue
            if status["state"] in {"project-drift", "replaced"} and not force:
                raise ConflictError(
                    f"Refusing to overwrite changed project entry without --force: {destination}"
                )
            skill = self.catalog.get_skill(entry["skill_id"])
            risk_allowed = allow_risk or bool(entry.get("risk_accepted"))
            if not skill["valid"] or (
                skill["audit_severity"] in {"high", "critical"} and not risk_allowed
            ):
                raise ValidationError(
                    f"Catalog skill is no longer safe to sync: {skill['name']}"
                )
            source = (Path(skill["source_path"]) / skill["rel_path"]).resolve()
            if _lexists(destination):
                self._remove_verified(destination, entry, force=force)
            destination.parent.mkdir(parents=True, exist_ok=True)
            actual_mode = self._install(source, destination, entry["mode"])
            tree_hash, _ = hash_skill_tree(
                destination if actual_mode == "copy" else source
            )
            entry.update(
                {
                    "mode": actual_mode,
                    "source_path": str(source),
                    "source_sha": skill["head_sha"],
                    "content_hash": skill["content_hash"],
                    "installed_tree_hash": tree_hash,
                    "synced_at": utc_now(),
                }
            )
            updated.append(entry.copy())
            manifest["updated_at"] = utc_now()
            _atomic_json(self.manifest_path(root), manifest)
        if not updated:
            manifest["updated_at"] = utc_now()
            _atomic_json(self.manifest_path(root), manifest)
        self._append_history(
            manifest,
            "sync",
            count=len(updated),
            skill_ids=[entry["skill_id"] for entry in updated],
            skill_names=[entry.get("name") or entry["skill_id"] for entry in updated],
            force=force,
        )
        manifest["updated_at"] = utc_now()
        _atomic_json(self.manifest_path(root), manifest)
        self._register_manifest(root, manifest)
        return {"project": str(root), "updated": updated}

    @serialized_catalog_operation
    def unlink(
        self,
        project: str | Path,
        *,
        skill_ids: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        root = _safe_project(project)
        manifest = self.load_manifest(root)
        selected = set(
            skill_ids or [entry["skill_id"] for entry in manifest["entries"]]
        )
        known = {entry["skill_id"] for entry in manifest["entries"]}
        names_by_id = {
            entry["skill_id"]: entry.get("name") or entry["skill_id"]
            for entry in manifest["entries"]
        }
        missing = selected - known
        if missing:
            raise NotFoundError(
                f"Skills are not managed by this project: {', '.join(sorted(missing))}"
            )
        for entry in manifest["entries"]:
            if entry["skill_id"] not in selected:
                continue
            state = self._entry_status(root, entry)["state"]
            if state in {"project-drift", "replaced"} and not force:
                raise ConflictError(
                    f"Refusing to remove changed managed entry without --force: {entry['path']}"
                )
            backup_relative = entry.get("adopted_backup")
            if backup_relative and not _lexists(
                _safe_entry_path(root, backup_relative)
            ):
                raise ConflictError(
                    "Preserved external Skill backup is missing; refusing to uninstall: "
                    f"{backup_relative}"
                )
        retained: list[dict[str, Any]] = []
        removed: list[str] = []
        restored: list[str] = []
        for entry in manifest["entries"]:
            if entry["skill_id"] not in selected:
                retained.append(entry)
                continue
            destination = _safe_entry_path(root, entry["path"])
            if _lexists(destination):
                self._remove_verified(destination, entry, force=force)
            backup_relative = entry.get("adopted_backup")
            if backup_relative:
                backup = _safe_entry_path(root, backup_relative)
                if not _lexists(backup):
                    raise ConflictError(
                        f"Preserved external Skill backup is missing; refusing to uninstall: {backup}"
                    )
                if _lexists(destination):
                    raise ConflictError(
                        f"Cannot restore preserved external Skill over existing content: {destination}"
                    )
                os.replace(backup, destination)
                restored.append(entry["skill_id"])
            removed.append(entry["skill_id"])
        manifest["entries"] = retained
        manifest["updated_at"] = utc_now()
        removed_names = [names_by_id[skill_id] for skill_id in removed]
        self._append_history(
            manifest,
            "unlink",
            count=len(removed),
            skill_ids=removed,
            skill_names=removed_names,
            force=force,
        )
        _atomic_json(self.manifest_path(root), manifest)
        self._register_manifest(root, manifest)
        return {"project": str(root), "removed": removed, "restored": restored}

    @serialized_catalog_operation
    def _finalize_restored_unlink(
        self,
        project: str | Path,
        skill_id: str,
        *,
        expected_tree_hash: str,
    ) -> dict[str, Any]:
        """Finish only the manifest half of an interrupted backup restoration."""
        root = _safe_project(project)
        manifest = self.load_manifest(root)
        entry = next(
            (item for item in manifest["entries"] if item["skill_id"] == skill_id),
            None,
        )
        if entry is None:
            raise NotFoundError(f"Skill is not managed by this project: {skill_id}")
        backup_relative = entry.get("adopted_backup")
        if not backup_relative:
            raise ConflictError("Managed Skill has no preserved external backup")
        backup = _safe_entry_path(root, backup_relative)
        destination = _safe_entry_path(root, entry["path"])
        if _lexists(backup):
            raise ConflictError("Preserved backup has not been restored yet")
        if not _lexists(destination) or destination.is_symlink():
            raise ConflictError("Restored external Skill must be a physical directory")
        actual_hash, _ = hash_skill_tree(destination)
        if actual_hash != expected_tree_hash:
            raise ConflictError("Restored external Skill does not match the approved backup")

        manifest["entries"] = [
            item for item in manifest["entries"] if item["skill_id"] != skill_id
        ]
        manifest["updated_at"] = utc_now()
        self._append_history(
            manifest,
            "unlink",
            count=1,
            skill_ids=[skill_id],
            skill_names=[entry.get("name") or skill_id],
            restored_manifest_only=True,
        )
        _atomic_json(self.manifest_path(root), manifest)
        self._register_manifest(root, manifest)
        return {"project": str(root), "removed": [skill_id], "restored": [skill_id]}

    def _target_path(self, target: str, project: Path) -> Path:
        if target == "root":
            if self._system_scope(project) is None:
                raise ValidationError("The root target is reserved for system Agent projects")
            return Path(".")
        return Path(get_agent_target(target).project_path)

    @staticmethod
    def _install(source: Path, destination: Path, mode: str) -> str:
        temporary = (
            destination.parent / f".{destination.name}.adaptive-{uuid.uuid4().hex}"
        )
        try:
            if mode in {"auto", "symlink"}:
                try:
                    os.symlink(source, temporary, target_is_directory=True)
                    os.replace(temporary, destination)
                    return "symlink"
                except OSError:
                    if _lexists(temporary):
                        temporary.unlink()
                    if mode == "symlink":
                        raise
            shutil.copytree(source, temporary, symlinks=True)
            os.replace(temporary, destination)
            return "copy"
        finally:
            if _lexists(temporary):
                if temporary.is_symlink():
                    temporary.unlink()
                elif temporary.is_dir():
                    shutil.rmtree(temporary)

    def _entry_status(self, project: Path, entry: dict[str, Any]) -> dict[str, Any]:
        destination = _safe_entry_path(project, entry["path"])
        result = {
            "skill_id": entry["skill_id"],
            "name": entry.get("name"),
            "path": entry["path"],
            "mode": entry.get("mode"),
            "state": "clean",
            "restores_external": bool(entry.get("adopted_backup")),
        }
        if not _lexists(destination):
            result["state"] = "missing"
            return result
        try:
            catalog_skill = self.catalog.get_skill(entry["skill_id"], active_only=False)
        except NotFoundError:
            result["state"] = "catalog-missing"
            return result
        if catalog_skill.get("source_status") == "removed":
            result["state"] = "catalog-missing"
            return result
        source = (
            Path(catalog_skill["source_path"]) / catalog_skill["rel_path"]
        ).resolve()
        if entry.get("mode") == "symlink":
            if not destination.is_symlink():
                result["state"] = "replaced"
                return result
            actual = (destination.parent / os.readlink(destination)).resolve()
            if actual != source.resolve():
                result["state"] = "replaced"
                return result
            if not source.is_dir():
                result["state"] = "broken"
                return result
            current_hash, _ = hash_skill_tree(source)
            if current_hash != entry.get("installed_tree_hash"):
                result["state"] = "source-drift"
            return result
        if not destination.is_dir() or destination.is_symlink():
            result["state"] = "replaced"
            return result
        current_hash, _ = hash_skill_tree(destination)
        if current_hash != entry.get("installed_tree_hash"):
            result["state"] = "project-drift"
            return result
        if source.is_dir():
            source_hash, _ = hash_skill_tree(source)
            if source_hash != entry.get("installed_tree_hash"):
                result["state"] = "source-drift"
        else:
            result["state"] = "broken"
        return result

    def _remove_verified(
        self, destination: Path, entry: dict[str, Any], *, force: bool
    ) -> None:
        if destination.is_symlink():
            actual = (destination.parent / os.readlink(destination)).resolve()
            try:
                skill = self.catalog.get_skill(entry["skill_id"], active_only=False)
                expected = (Path(skill["source_path"]) / skill["rel_path"]).resolve()
            except NotFoundError:
                if not force:
                    raise ConflictError(
                        f"Cannot verify managed symlink because its catalog skill is missing: {destination}"
                    )
                expected = actual
            if actual != expected and not force:
                raise ConflictError(f"Managed symlink target changed: {destination}")
            destination.unlink()
            return
        if destination.is_dir():
            current_hash, _ = hash_skill_tree(destination)
            if current_hash != entry.get("installed_tree_hash") and not force:
                raise ConflictError(f"Managed copy changed: {destination}")
            shutil.rmtree(destination)
            return
        if force:
            destination.unlink()
            return
        raise ConflictError(
            f"Managed entry was replaced by an unmanaged file: {destination}"
        )
