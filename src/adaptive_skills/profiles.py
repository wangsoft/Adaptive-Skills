from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .agent_targets import get_agent_target
from .catalog import Catalog
from .config import Settings
from .database import Database, path_is_within, utc_now
from .errors import ConflictError, NotFoundError, ValidationError
from .projects import ProjectManager, _lexists


PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
PORTABLE_PROFILE_SCHEMA = "adaptive-skills-profile/1"
MAX_PROFILE_FILE_BYTES = 1_000_000
MAX_PROFILE_ENTRIES = 500


class SkillProfileService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings)
        self.catalog = Catalog(settings, self.database)
        self.projects = ProjectManager(settings)

    def list(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT p.*, count(e.position) AS entry_count
                FROM skill_profiles p
                LEFT JOIN skill_profile_entries e ON e.profile_id = p.id
                GROUP BY p.id
                ORDER BY p.updated_at DESC, p.name COLLATE NOCASE, p.id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, profile_id: str) -> dict[str, Any]:
        profile_id = self._profile_id(profile_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM skill_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            entries = connection.execute(
                """
                SELECT skill_id, skill_name, source_name, source_url, rel_path
                FROM skill_profile_entries
                WHERE profile_id = ?
                ORDER BY position
                """,
                (profile_id,),
            ).fetchall()
        if row is None:
            raise NotFoundError(f"Unknown Skill profile: {profile_id}")
        return {**dict(row), "entries": [dict(entry) for entry in entries]}

    def save(
        self,
        *,
        name: str,
        skill_ids: list[str],
        description: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        if not skill_ids:
            raise ValidationError("A Skill profile requires at least one Skill")
        entries: list[dict[str, Any]] = []
        observed: set[str] = set()
        for skill_id in skill_ids:
            skill = self.catalog.get_skill(skill_id)
            if skill["id"] in observed:
                continue
            observed.add(skill["id"])
            self._require_library_skill(skill)
            entries.append(self._skill_locator(skill))
        return self._save_entries(
            name=name,
            description=description,
            entries=entries,
            profile_id=profile_id,
        )

    def capture(
        self,
        project: str | Path,
        *,
        name: str,
        description: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        status = self.projects.status(project)
        manifest = self.projects.load_manifest(Path(status["project"]))
        if not manifest["entries"]:
            raise ValidationError("The project has no managed Skills to capture")
        entries = [
            {
                "skill_id": entry.get("skill_id"),
                "skill_name": entry.get("name") or Path(entry["path"]).name,
                "source_name": entry.get("source_name"),
                "source_url": entry.get("source_url"),
                "rel_path": entry.get("source_rel_path"),
            }
            for entry in manifest["entries"]
        ]
        return self._save_entries(
            name=name,
            description=description,
            entries=entries,
            profile_id=profile_id,
        )

    def delete(self, profile_id: str) -> dict[str, Any]:
        profile = self.get(profile_id)
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM skill_profiles WHERE id = ?", (profile["id"],))
        return {"deleted": True, "id": profile["id"], "name": profile["name"]}

    def export_file(
        self,
        profile_id: str,
        output: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        profile = self.get(profile_id)
        destination = Path(output).expanduser()
        if not destination.name:
            raise ValidationError("Profile export path must include a file name")
        if not destination.parent.is_dir():
            raise ValidationError(
                f"Profile export directory does not exist: {destination.parent}"
            )
        existed = os.path.lexists(destination)
        if existed and not overwrite:
            raise ConflictError(f"Profile export file already exists: {destination}")
        if destination.is_dir():
            raise ConflictError(f"Profile export path is a directory: {destination}")
        document = {
            "schema": PORTABLE_PROFILE_SCHEMA,
            "exported_at": utc_now(),
            "profile": self._portable_profile(profile),
        }
        document["profile"] = self._validate_portable_document(document)["profile"]
        payload = (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_PROFILE_FILE_BYTES:
            raise ValidationError(
                f"Portable Skill profile exceeds {MAX_PROFILE_FILE_BYTES} bytes"
            )
        temporary_path: Path | None = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary_path, destination)
            else:
                try:
                    os.link(temporary_path, destination)
                except FileExistsError as error:
                    raise ConflictError(
                        f"Profile export file already exists: {destination}"
                    ) from error
                temporary_path.unlink()
            temporary_path = None
        except OSError as error:
            raise ValidationError(f"Could not write profile export file: {error}") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return {
            "schema": PORTABLE_PROFILE_SCHEMA,
            "path": str(destination.resolve()),
            "written": True,
            "overwritten": existed,
            "bytes": len(payload),
            "profile": {
                "id": profile["id"],
                "name": profile["name"],
                "entry_count": len(profile["entries"]),
            },
        }

    def preview_import(self, input_path: str | Path) -> dict[str, Any]:
        document, digest = self._read_portable_document(input_path)
        portable = document["profile"]
        existing = self._find_portable_profile(portable)
        library_skills = self._library_skills()
        items = [
            self._portable_entry_status(entry, library_skills)
            for entry in portable["entries"]
        ]
        counts = {
            status: sum(1 for item in items if item["status"] == status)
            for status in ("exact", "compatible", "ambiguous", "missing")
        }
        action = "already-exists" if existing is not None else "create"
        return {
            "schema": PORTABLE_PROFILE_SCHEMA,
            "path": str(Path(input_path).expanduser().resolve()),
            "sha256": digest,
            "profile": {
                "name": portable["name"],
                "description": portable["description"],
                "entry_count": len(portable["entries"]),
            },
            "items": items,
            "counts": counts,
            "action": action,
            "can_import": action == "create",
            "existing_profile_id": existing["id"] if existing else None,
        }

    def import_file(
        self,
        input_path: str | Path,
        *,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        document, digest = self._read_portable_document(input_path)
        if expected_sha256 is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                raise ValidationError("Invalid profile import SHA-256 digest")
            if digest != expected_sha256:
                raise ConflictError(
                    "Profile import file changed after preview; preview it again before importing"
                )
        portable = document["profile"]
        existing = self._find_portable_profile(portable)
        if existing is not None:
            return {
                "changed": False,
                "action": "already-exists",
                "sha256": digest,
                "profile": existing,
            }
        profile = self._save_entries(
            name=portable["name"],
            description=portable["description"],
            entries=[{"skill_id": None, **entry} for entry in portable["entries"]],
            profile_id=None,
        )
        return {
            "changed": True,
            "action": "created",
            "sha256": digest,
            "profile": profile,
        }

    def preview(
        self,
        profile_id: str,
        project: str | Path,
        *,
        target: str = "auto",
        allow_risk: bool = False,
    ) -> dict[str, Any]:
        profile = self.get(profile_id)
        status = self.projects.status(project)
        root = Path(status["project"])
        target_root = self.projects._target_path(target, root)
        manifest = self.projects.load_manifest(root)
        by_path = {entry["path"]: entry for entry in manifest["entries"]}
        items: list[dict[str, Any]] = []
        for entry in profile["entries"]:
            skill, resolution = self._resolve(entry, target=target, project=root)
            if skill is None:
                items.append(
                    {
                        **entry,
                        "skill_id": None,
                        "resolved_name": entry["skill_name"],
                        "action": "unresolved",
                        "reason": resolution,
                        "path": None,
                    }
                )
                continue
            relative = (target_root / skill["name"]).as_posix()
            destination = root / relative
            managed = by_path.get(relative)
            action = "install"
            reason = resolution
            if not skill["valid"]:
                action = "conflict"
                reason = "Skill 格式无效，不能应用"
            elif skill["audit_severity"] in {"high", "critical"} and not allow_risk:
                action = "conflict"
                reason = "Skill 需要显式风险确认"
            elif managed is not None:
                managed_status = self.projects._entry_status(root, managed)
                if (
                    managed.get("skill_id") == skill["id"]
                    and managed_status["state"] == "clean"
                ):
                    action = "already-installed"
                    reason = "已由 Adaptive Skills 管理"
                else:
                    action = "conflict"
                    reason = (
                        "目标路径由其他 Skill 管理"
                        if managed.get("skill_id") != skill["id"]
                        else f"受管条目状态异常：{managed_status['state']}"
                    )
            elif _lexists(destination):
                action = "conflict"
                reason = "目标路径已有外部内容"
            items.append(
                {
                    **entry,
                    "skill_id": skill["id"],
                    "resolved_name": skill["name"],
                    "source_name": skill["source_name"],
                    "action": action,
                    "reason": reason,
                    "path": relative,
                    "audit_severity": skill["audit_severity"],
                    "valid": skill["valid"],
                }
            )
        counts = {
            action: sum(1 for item in items if item["action"] == action)
            for action in ("install", "already-installed", "conflict", "unresolved")
        }
        return {
            "profile": profile,
            "project": str(root),
            "target": str(root) if target_root == Path(".") else target_root.as_posix(),
            "items": items,
            "counts": counts,
            "can_apply": counts["conflict"] == 0 and counts["unresolved"] == 0,
        }

    def apply(
        self,
        profile_id: str,
        project: str | Path,
        *,
        target: str = "auto",
        allow_risk: bool = False,
    ) -> dict[str, Any]:
        preview = self.preview(
            profile_id, project, target=target, allow_risk=allow_risk
        )
        if not preview["can_apply"]:
            raise ConflictError(
                "Skill profile has unresolved or conflicting entries; review the preview before applying"
            )
        skill_ids = [
            item["skill_id"]
            for item in preview["items"]
            if item["action"] == "install"
        ]
        if not skill_ids:
            return {**preview, "installed": [], "changed": False}
        result = self.projects.apply(
            project,
            skill_ids,
            target=target,
            mode="symlink",
            requirement=f"Skill profile: {preview['profile']['name']}",
            allow_risk=allow_risk,
        )
        return {**preview, "installed": result["installed"], "changed": True}

    def _save_entries(
        self,
        *,
        name: str,
        description: str | None,
        entries: list[dict[str, Any]],
        profile_id: str | None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        clean_description = (description or "").strip()
        if not clean_name or len(clean_name) > 100:
            raise ValidationError("Profile name must be between 1 and 100 characters")
        if len(clean_description) > 500:
            raise ValidationError("Profile description must not exceed 500 characters")
        if not entries:
            raise ValidationError("A Skill profile requires at least one Skill")
        resolved_id = (
            self._profile_id(profile_id)
            if profile_id
            else f"profile-{uuid.uuid4().hex}"
        )
        now = utc_now()
        with self.database.transaction() as connection:
            created = connection.execute(
                "SELECT created_at FROM skill_profiles WHERE id = ?", (resolved_id,)
            ).fetchone()
            connection.execute(
                """
                INSERT INTO skill_profiles(id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    updated_at=excluded.updated_at
                """,
                (
                    resolved_id,
                    clean_name,
                    clean_description,
                    created["created_at"] if created else now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM skill_profile_entries WHERE profile_id = ?",
                (resolved_id,),
            )
            for position, entry in enumerate(entries):
                skill_name = str(entry.get("skill_name") or "").strip()
                if not skill_name:
                    raise ValidationError("Profile entry is missing its Skill name")
                connection.execute(
                    """
                    INSERT INTO skill_profile_entries(
                        profile_id, position, skill_id, skill_name,
                        source_name, source_url, rel_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_id,
                        position,
                        entry.get("skill_id"),
                        skill_name,
                        entry.get("source_name"),
                        entry.get("source_url"),
                        entry.get("rel_path"),
                    ),
                )
        return self.get(resolved_id)

    def _resolve(
        self,
        entry: dict[str, Any],
        *,
        target: str,
        project: Path,
    ) -> tuple[dict[str, Any] | None, str]:
        skills = self._library_skills()
        named = [
            skill
            for skill in skills
            if skill["name"].casefold() == entry["skill_name"].casefold()
        ]
        if not named:
            return None, "目录中没有同名 Skill"
        scope = self.projects._system_scope(project) if target == "root" else None
        target_id = scope["id"] if scope is not None else get_agent_target(target).id
        prefixes = get_agent_target(target_id).preferred_rel_prefixes

        same_source = [
            skill
            for skill in named
            if (
                entry.get("source_url")
                and skill.get("source_url") == entry["source_url"]
            )
            or (
                not entry.get("source_url")
                and entry.get("source_name")
                and skill.get("source_name") == entry["source_name"]
            )
        ]
        candidates = same_source
        if not candidates and entry.get("skill_id"):
            candidates = [
                skill for skill in named if skill["id"] == entry["skill_id"]
            ]
        if not candidates:
            source_ids = {skill["source_id"] for skill in named}
            if len(source_ids) != 1:
                return None, "同名 Skill 来自多个来源，无法安全判断"
            candidates = named
        selected = min(
            candidates,
            key=lambda skill: (
                not skill["valid"],
                self.catalog._rel_path_preference(skill["rel_path"], prefixes),
                skill["audit_severity"] in {"high", "critical"},
                skill["id"] != entry.get("skill_id"),
                skill["rel_path"].casefold(),
                skill["id"],
            ),
        )
        exact = (
            selected["id"] == entry.get("skill_id")
            or (
                selected.get("source_url") == entry.get("source_url")
                and selected.get("rel_path") == entry.get("rel_path")
            )
        )
        return selected, "精确匹配" if exact else f"已选择 {target_id} 适配版本"

    def _library_skills(self) -> list[dict[str, Any]]:
        library = self.settings.library.resolve()
        return [
            skill
            for skill in self.catalog.list_skills()
            if path_is_within(Path(skill["source_path"]), library)
            and path_is_within(
                Path(skill["source_path"]) / skill["rel_path"], library
            )
        ]

    def _require_library_skill(self, skill: dict[str, Any]) -> None:
        library = self.settings.library.resolve()
        if not path_is_within(Path(skill["source_path"]), library) or not path_is_within(
            Path(skill["source_path"]) / skill["rel_path"], library
        ):
            raise ValidationError(
                f"Skill is outside the configured library: {skill['name']}"
            )

    def _read_portable_document(
        self, input_path: str | Path
    ) -> tuple[dict[str, Any], str]:
        source = Path(input_path).expanduser()
        if not source.is_file():
            raise ValidationError(f"Profile import file does not exist: {source}")
        try:
            size = source.stat().st_size
        except OSError as error:
            raise ValidationError(f"Could not inspect profile import file: {error}") from error
        if size > MAX_PROFILE_FILE_BYTES:
            raise ValidationError(
                f"Profile import file exceeds {MAX_PROFILE_FILE_BYTES} bytes"
            )
        try:
            raw = source.read_bytes()
        except OSError as error:
            raise ValidationError(f"Could not read profile import file: {error}") from error
        if len(raw) > MAX_PROFILE_FILE_BYTES:
            raise ValidationError(
                f"Profile import file exceeds {MAX_PROFILE_FILE_BYTES} bytes"
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError(f"Invalid portable Skill profile JSON: {error}") from error
        document = self._validate_portable_document(parsed)
        return document, hashlib.sha256(raw).hexdigest()

    def _validate_portable_document(self, value: Any) -> dict[str, Any]:
        document = self._object(value, "Portable Skill profile document")
        self._only_keys(document, {"schema", "exported_at", "profile"}, "document")
        if document.get("schema") != PORTABLE_PROFILE_SCHEMA:
            raise ValidationError(
                f"Unsupported portable Skill profile schema: {document.get('schema')!r}"
            )
        profile = self._object(document.get("profile"), "Portable Skill profile")
        self._only_keys(profile, {"name", "description", "entries"}, "profile")
        name = self._bounded_text(profile.get("name"), "Profile name", 100)
        description = self._bounded_text(
            profile.get("description", ""),
            "Profile description",
            500,
            allow_empty=True,
        )
        entries_value = profile.get("entries")
        if not isinstance(entries_value, list) or not (
            1 <= len(entries_value) <= MAX_PROFILE_ENTRIES
        ):
            raise ValidationError(
                f"Portable Skill profile must contain 1 to {MAX_PROFILE_ENTRIES} entries"
            )
        entries: list[dict[str, str | None]] = []
        observed: set[tuple[str, str, str, str]] = set()
        for index, raw_entry in enumerate(entries_value):
            entry = self._object(raw_entry, f"Profile entry {index + 1}")
            self._only_keys(
                entry,
                {"skill_name", "source_name", "source_url", "rel_path"},
                f"profile entry {index + 1}",
            )
            clean = {
                "skill_name": self._bounded_text(
                    entry.get("skill_name"), "Skill name", 200
                ),
                "source_name": self._optional_text(
                    entry.get("source_name"), "Source name", 200
                ),
                "source_url": self._optional_text(
                    entry.get("source_url"), "Source URL", 2048
                ),
                "rel_path": self._portable_rel_path(entry.get("rel_path")),
            }
            key = tuple((clean[field] or "").casefold() for field in clean)
            if key not in observed:
                observed.add(key)
                entries.append(clean)
        return {
            "schema": PORTABLE_PROFILE_SCHEMA,
            "profile": {
                "name": name,
                "description": description,
                "entries": entries,
            },
        }

    def _find_portable_profile(
        self, portable: dict[str, Any]
    ) -> dict[str, Any] | None:
        expected = {
            "name": portable["name"],
            "description": portable["description"],
            "entries": portable["entries"],
        }
        for summary in self.list():
            profile = self.get(summary["id"])
            if self._portable_profile(profile) == expected:
                return profile
        return None

    def _portable_entry_status(
        self,
        entry: dict[str, Any],
        library_skills: list[dict[str, Any]],
    ) -> dict[str, Any]:
        named = [
            skill
            for skill in library_skills
            if skill["name"].casefold() == entry["skill_name"].casefold()
        ]
        exact = [
            skill
            for skill in named
            if skill.get("rel_path") == entry.get("rel_path")
            and (
                (
                    entry.get("source_url")
                    and skill.get("source_url") == entry["source_url"]
                )
                or (
                    not entry.get("source_url")
                    and entry.get("source_name")
                    and skill.get("source_name") == entry["source_name"]
                )
            )
        ]
        if exact:
            status = "exact"
            reason = "当前目录中存在相同来源和路径"
        elif not named:
            status = "missing"
            reason = "当前目录中还没有同名 Skill，可先导入配置集"
        elif len({skill["source_id"] for skill in named}) == 1:
            status = "compatible"
            reason = "存在同名 Skill，应用时会按目标 Agent 选择适配版本"
        else:
            status = "ambiguous"
            reason = "同名 Skill 来自多个来源，应用前需要选择或补齐来源"
        return {**entry, "status": status, "reason": reason}

    @staticmethod
    def _portable_profile(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": profile["name"],
            "description": profile.get("description") or "",
            "entries": [
                {
                    "skill_name": entry["skill_name"],
                    "source_name": entry.get("source_name"),
                    "source_url": entry.get("source_url"),
                    "rel_path": entry.get("rel_path"),
                }
                for entry in profile["entries"]
            ],
        }

    @staticmethod
    def _object(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValidationError(f"{label} must be a JSON object")
        return value

    @staticmethod
    def _only_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
        unexpected = sorted(set(value) - allowed)
        if unexpected:
            raise ValidationError(
                f"Unexpected field in {label}: {', '.join(unexpected)}"
            )

    @staticmethod
    def _bounded_text(
        value: Any,
        label: str,
        maximum: int,
        *,
        allow_empty: bool = False,
    ) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{label} must be text")
        clean = value.strip()
        if "\x00" in clean or (not clean and not allow_empty) or len(clean) > maximum:
            minimum = 0 if allow_empty else 1
            raise ValidationError(
                f"{label} must contain {minimum} to {maximum} characters"
            )
        return clean

    @classmethod
    def _optional_text(cls, value: Any, label: str, maximum: int) -> str | None:
        if value is None:
            return None
        clean = cls._bounded_text(value, label, maximum, allow_empty=True)
        return clean or None

    @classmethod
    def _portable_rel_path(cls, value: Any) -> str | None:
        clean = cls._optional_text(value, "Repository-relative Skill path", 1000)
        if clean is None:
            return None
        path = PurePosixPath(clean)
        if "\\" in clean or path.is_absolute() or ".." in path.parts:
            raise ValidationError(
                "Repository-relative Skill path must not be absolute or contain parent traversal"
            )
        return path.as_posix()

    @staticmethod
    def _skill_locator(skill: dict[str, Any]) -> dict[str, Any]:
        return {
            "skill_id": skill["id"],
            "skill_name": skill["name"],
            "source_name": skill["source_name"],
            "source_url": skill["source_url"],
            "rel_path": skill["rel_path"],
        }

    @staticmethod
    def _profile_id(profile_id: str) -> str:
        value = profile_id.strip()
        if not PROFILE_ID.fullmatch(value):
            raise ValidationError("Invalid Skill profile ID")
        return value
