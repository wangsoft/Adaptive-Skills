from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .config import Settings
from .database import Database, path_is_within, utc_now
from .errors import ConflictError, NotFoundError, ValidationError
from .scanner import hash_skill_tree


MANIFEST_SCHEMA = "adaptive-skills-project/1"
MANIFEST_DIRECTORY = ".adaptive-skills"
MANIFEST_FILE = "manifest.json"
HISTORY_LIMIT = 100
HISTORY_ACTIONS = {"apply", "sync", "unlink"}
TARGETS = {
    "auto": Path(".agents/skills"),
    "universal": Path(".agents/skills"),
    "codex": Path(".agents/skills"),
    "claude": Path(".claude/skills"),
}


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

    def register(self, project: str | Path) -> dict[str, Any]:
        root = _safe_project(project)
        if not self.manifest_path(root).is_file():
            raise ValidationError(
                "Only projects with an adaptive-skills manifest can be registered"
            )
        return self._register_manifest(root, self.load_manifest(root))

    def list_projects(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_projects
                ORDER BY COALESCE(last_activity_at, updated_at) DESC,
                         display_name COLLATE NOCASE, id
                """
            ).fetchall()
        results: list[dict[str, Any]] = []
        observed: list[tuple[str, str | None, str | None, str]] = []
        for raw in rows:
            row = dict(raw)
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

    def forget(self, project_id_or_path: str | Path) -> dict[str, Any]:
        value = str(project_id_or_path)
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

    def relink(self, project_id: str, new_path: str | Path) -> dict[str, Any]:
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
        }

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
        requirement: str,
        *,
        limit: int = 5,
        target: str = "auto",
        allow_risk: bool = False,
    ) -> dict[str, Any]:
        root = _safe_project(project)
        target_root = self._target_path(target)
        return {
            "project": str(root),
            "requirement": requirement,
            "target": target_root.as_posix(),
            "recommendations": self.catalog.search(
                requirement, limit=limit, allow_risk=allow_risk
            ),
        }

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
        target_root = self._target_path(target)
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
        _atomic_json(self.manifest_path(root), manifest)
        self._register_manifest(root, manifest)
        return {
            "project": str(root),
            "installed": installed,
            "manifest": str(self.manifest_path(root)),
        }

    def status(self, project: str | Path) -> dict[str, Any]:
        root = _safe_project(project)
        manifest = self.load_manifest(root)
        entries = [self._entry_status(root, entry) for entry in manifest["entries"]]
        managed = self.manifest_path(root).is_file()
        if managed:
            self._register_manifest(root, manifest)
        return {
            "project": str(root),
            "manifest": str(self.manifest_path(root)),
            "managed": managed,
            "entries": entries,
            "clean": all(entry["state"] == "clean" for entry in entries),
        }

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
        retained: list[dict[str, Any]] = []
        removed: list[str] = []
        for entry in manifest["entries"]:
            if entry["skill_id"] not in selected:
                retained.append(entry)
                continue
            destination = _safe_entry_path(root, entry["path"])
            if _lexists(destination):
                self._remove_verified(destination, entry, force=force)
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
        return {"project": str(root), "removed": removed}

    @staticmethod
    def _target_path(target: str) -> Path:
        if target not in TARGETS:
            raise ValidationError(f"Unknown project target: {target}")
        return TARGETS[target]

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
        }
        if not _lexists(destination):
            result["state"] = "missing"
            return result
        try:
            catalog_skill = self.catalog.get_skill(entry["skill_id"], active_only=False)
        except NotFoundError:
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
