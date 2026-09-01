from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings
from .database import Database, path_is_within, utc_now
from .errors import ConflictError, ValidationError
from .operation_lock import serialized_catalog_operation


@dataclass(frozen=True)
class AgentTarget:
    id: str
    label: str
    global_parts: tuple[str, ...]
    project_path: str
    preferred_rel_prefixes: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    detect_parts: tuple[str, ...] = ()
    supported_scopes: tuple[str, ...] = ("global", "project")
    sync_modes: tuple[str, ...] = ("symlink", "copy")
    default_sync_mode: str = "symlink"
    built_in: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.label:
            raise ValidationError("Agent target id and label are required")
        self._validate_parts(self.global_parts, "global")
        if self.detect_parts:
            self._validate_parts(self.detect_parts, "detection")
        self._validate_relative_path(self.project_path, "project")
        if not self.preferred_rel_prefixes:
            raise ValidationError("Agent target preferred paths are required")
        for prefix in self.preferred_rel_prefixes:
            self._validate_relative_path(prefix, "preferred")
        if not self.supported_scopes or any(
            scope not in {"global", "project"} for scope in self.supported_scopes
        ):
            raise ValidationError("Agent target scopes must be global or project")
        if len(set(self.supported_scopes)) != len(self.supported_scopes):
            raise ValidationError("Agent target scopes must be unique")
        if not self.sync_modes or any(
            mode not in {"symlink", "copy"} for mode in self.sync_modes
        ):
            raise ValidationError("Agent target sync modes must be symlink or copy")
        if len(set(self.sync_modes)) != len(self.sync_modes):
            raise ValidationError("Agent target sync modes must be unique")
        if self.default_sync_mode not in self.sync_modes:
            raise ValidationError(
                "Agent target default sync mode must be one of its sync modes"
            )

    @staticmethod
    def _validate_parts(parts: tuple[str, ...], kind: str) -> None:
        if not parts or any(
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            for part in parts
        ):
            raise ValidationError(
                f"Agent target {kind} path parts must form a safe relative path"
            )

    @staticmethod
    def _validate_relative_path(value: str, kind: str) -> None:
        path = PurePosixPath(value)
        if (
            not value
            or not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or value.startswith("~")
            or "\\" in value
        ):
            raise ValidationError(
                f"Agent target {kind} path must be a safe relative path"
            )

    @staticmethod
    def _home(home: Path | None = None) -> Path:
        return (home or Path.home()).expanduser().resolve()

    def global_path(self, home: Path | None = None) -> Path:
        return self._home(home).joinpath(*self.global_parts)

    def detect_path(self, home: Path | None = None) -> Path:
        parts = self.detect_parts or self.global_parts[:-1] or self.global_parts
        return self._home(home).joinpath(*parts)

    @property
    def global_group(self) -> str:
        return PurePosixPath(*self.global_parts).as_posix()

    @property
    def project_group(self) -> str:
        return PurePosixPath(self.project_path).as_posix()

    def supports_scope(self, scope: str) -> bool:
        return scope in self.supported_scopes

    def as_dict(self, home: Path | None = None) -> dict[str, Any]:
        path = self.global_path(home)
        detect_path = self.detect_path(home)
        return {
            "id": self.id,
            "label": self.label,
            "path": str(path),
            "global_path": str(path),
            "project_path": self.project_path,
            "exists": path.is_dir(),
            "detect_path": str(detect_path),
            "detected": detect_path.is_dir(),
            "aliases": list(self.aliases),
            "preferred_rel_prefixes": list(self.preferred_rel_prefixes),
            "supported_scopes": list(self.supported_scopes),
            "supports_global": self.supports_scope("global"),
            "supports_project": self.supports_scope("project"),
            "sync_modes": list(self.sync_modes),
            "default_sync_mode": self.default_sync_mode,
            "global_group": self.global_group,
            "project_group": self.project_group,
            "built_in": self.built_in,
        }


AGENT_TARGETS: tuple[AgentTarget, ...] = (
    AgentTarget(
        id="agents",
        label="通用 Agents",
        global_parts=(".agents", "skills"),
        project_path=".agents/skills",
        preferred_rel_prefixes=(".agents/skills", "skills", "plugin/skills"),
        aliases=("auto", "universal"),
        detect_parts=(".agents",),
    ),
    AgentTarget(
        id="claude",
        label="Claude Code",
        global_parts=(".claude", "skills"),
        project_path=".claude/skills",
        preferred_rel_prefixes=(".claude/skills", "skills", "plugin/skills"),
        detect_parts=(".claude",),
    ),
    AgentTarget(
        id="codex",
        label="Codex",
        global_parts=(".codex", "skills"),
        project_path=".agents/skills",
        preferred_rel_prefixes=(
            ".codex/skills",
            ".agents/skills",
            "skills",
            "plugin/skills",
        ),
        detect_parts=(".codex",),
    ),
    AgentTarget(
        id="cursor",
        label="Cursor",
        global_parts=(".cursor", "skills"),
        project_path=".cursor/skills",
        preferred_rel_prefixes=(".cursor/skills", ".agents/skills", "skills"),
        detect_parts=(".cursor",),
    ),
    AgentTarget(
        id="gemini",
        label="Gemini CLI",
        global_parts=(".gemini", "skills"),
        project_path=".gemini/skills",
        preferred_rel_prefixes=(".gemini/skills", ".agents/skills", "skills"),
        detect_parts=(".gemini",),
    ),
    AgentTarget(
        id="opencode",
        label="OpenCode",
        global_parts=(".config", "opencode", "skills"),
        project_path=".opencode/skills",
        preferred_rel_prefixes=(
            ".opencode/skills",
            ".config/opencode/skills",
            ".agents/skills",
            "skills",
        ),
        detect_parts=(".config", "opencode"),
    ),
)


def _target_index() -> dict[str, AgentTarget]:
    targets: dict[str, AgentTarget] = {}
    for target in AGENT_TARGETS:
        for key in (target.id, *target.aliases):
            if key in targets:
                raise ValidationError(f"Duplicate Agent target id or alias: {key}")
            targets[key] = target
    return targets


_TARGETS_BY_KEY = _target_index()


def get_agent_target(identifier: str) -> AgentTarget:
    target = _TARGETS_BY_KEY.get(identifier)
    if target is None:
        raise ValidationError(f"Unknown Agent target: {identifier}")
    return target


def list_agent_targets(home: Path | None = None) -> list[dict[str, Any]]:
    return [target.as_dict(home) for target in AGENT_TARGETS]


def targets_sharing_global_path(identifier: str) -> tuple[AgentTarget, ...]:
    group = get_agent_target(identifier).global_group
    return tuple(target for target in AGENT_TARGETS if target.global_group == group)


def targets_sharing_project_path(identifier: str) -> tuple[AgentTarget, ...]:
    group = get_agent_target(identifier).project_group
    return tuple(target for target in AGENT_TARGETS if target.project_group == group)


def project_target_choices() -> tuple[str, ...]:
    return tuple(_TARGETS_BY_KEY)


CUSTOM_TARGET_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
CUSTOM_TARGET_LABEL_LIMIT = 80
CUSTOM_TARGET_MANIFEST_LIMIT = 2_000_000


@dataclass(frozen=True)
class ConfiguredAgentTarget:
    id: str
    label: str
    configured_global_path: Path
    configured_detect_path: Path
    project_path: str
    preferred_rel_prefixes: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    supported_scopes: tuple[str, ...] = ("global", "project")
    sync_modes: tuple[str, ...] = ("symlink", "copy")
    default_sync_mode: str = "symlink"
    built_in: bool = False

    def __post_init__(self) -> None:
        AgentTarget._validate_relative_path(self.project_path, "project")
        if not self.preferred_rel_prefixes:
            raise ValidationError("Agent target preferred paths are required")
        for prefix in self.preferred_rel_prefixes:
            AgentTarget._validate_relative_path(prefix, "preferred")

    def global_path(self, home: Path | None = None) -> Path:
        return self.configured_global_path

    def detect_path(self, home: Path | None = None) -> Path:
        return self.configured_detect_path

    @property
    def global_group(self) -> str:
        return str(self.configured_global_path)

    @property
    def project_group(self) -> str:
        return PurePosixPath(self.project_path).as_posix()

    def supports_scope(self, scope: str) -> bool:
        return scope in self.supported_scopes

    def as_dict(self, home: Path | None = None) -> dict[str, Any]:
        path = self.global_path()
        detect_path = self.detect_path()
        return {
            "id": self.id,
            "label": self.label,
            "path": str(path),
            "global_path": str(path),
            "project_path": self.project_path,
            "exists": path.is_dir(),
            "detect_path": str(detect_path),
            "detected": detect_path.is_dir(),
            "aliases": list(self.aliases),
            "preferred_rel_prefixes": list(self.preferred_rel_prefixes),
            "supported_scopes": list(self.supported_scopes),
            "supports_global": self.supports_scope("global"),
            "supports_project": self.supports_scope("project"),
            "sync_modes": list(self.sync_modes),
            "default_sync_mode": self.default_sync_mode,
            "global_group": self.global_group,
            "project_group": self.project_group,
            "built_in": self.built_in,
        }


AgentTargetDefinition = AgentTarget | ConfiguredAgentTarget


class CustomAgentTargetService:
    def __init__(
        self,
        settings: Settings,
        database: Database | None = None,
        *,
        home: Path | None = None,
    ):
        self.settings = settings
        self.database = database or Database(settings)
        self.home = (home or Path.home()).expanduser().resolve()

    def list_custom(self) -> list[dict[str, Any]]:
        return [target.as_dict() for target in self.custom_targets()]

    def list(self) -> list[dict[str, Any]]:
        return [
            *(target.as_dict(self.home) for target in AGENT_TARGETS),
            *self.list_custom(),
        ]

    def custom_targets(self) -> tuple[ConfiguredAgentTarget, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, label, global_path, detect_path, project_path
                FROM custom_agent_targets
                ORDER BY label COLLATE NOCASE, id
                """
            ).fetchall()
        return tuple(self._from_row(dict(row)) for row in rows)

    def all_targets(self) -> tuple[AgentTargetDefinition, ...]:
        return (*AGENT_TARGETS, *self.custom_targets())

    def get_optional(self, identifier: str) -> AgentTargetDefinition | None:
        built_in = _TARGETS_BY_KEY.get(identifier)
        if built_in is not None:
            return built_in
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, label, global_path, detect_path, project_path
                FROM custom_agent_targets WHERE id = ?
                """,
                (identifier,),
            ).fetchone()
        return self._from_row(dict(row)) if row is not None else None

    def get(self, identifier: str) -> AgentTargetDefinition:
        target = self.get_optional(identifier)
        if target is None:
            raise ValidationError(f"Unknown Agent target: {identifier}")
        return target

    @serialized_catalog_operation
    def create(
        self,
        *,
        target_id: str,
        label: str,
        global_path: str | Path,
        detect_path: str | Path,
        project_path: str,
    ) -> dict[str, Any]:
        normalized_id = target_id.strip()
        normalized_label = label.strip()
        normalized_project_path = project_path.strip()
        if not CUSTOM_TARGET_ID.fullmatch(normalized_id):
            raise ValidationError(
                "Custom Agent target id must be 1-32 lowercase letters, numbers, or hyphens and start with a letter"
            )
        if normalized_id in {*_TARGETS_BY_KEY, "root"}:
            raise ValidationError(f"Custom Agent target id is reserved: {normalized_id}")
        if not normalized_label or len(normalized_label) > CUSTOM_TARGET_LABEL_LIMIT:
            raise ValidationError("Custom Agent target name must be 1-80 characters")
        AgentTarget._validate_relative_path(normalized_project_path, "project")
        resolved_global = self._home_path(global_path, "global")
        resolved_detect = self._home_path(detect_path, "detection")
        if resolved_global.exists() and not resolved_global.is_dir():
            raise ValidationError("Agent target global path must be a directory")
        if resolved_detect.exists() and not resolved_detect.is_dir():
            raise ValidationError("Agent target detection path must be a directory")
        if resolved_global == resolved_detect:
            raise ValidationError(
                "Agent target detection path must be a parent of its global Skills path"
            )
        try:
            resolved_global.relative_to(resolved_detect)
        except ValueError as exc:
            raise ValidationError(
                "Agent target global Skills path must be inside its detection path"
            ) from exc
        library_path = self.settings.library.resolve()
        if path_is_within(resolved_global, library_path) or path_is_within(
            library_path, resolved_global
        ):
            raise ValidationError(
                "Custom Agent target global path cannot overlap the managed Skill library"
            )
        for built_in in AGENT_TARGETS:
            built_in_path = built_in.global_path(self.home)
            if path_is_within(resolved_global, built_in_path) or path_is_within(
                built_in_path, resolved_global
            ):
                raise ValidationError(
                    "Custom Agent target global path overlaps a built-in target"
                )
        for configured in self.custom_targets():
            configured_path = configured.global_path()
            if path_is_within(resolved_global, configured_path) or path_is_within(
                configured_path, resolved_global
            ):
                raise ConflictError(
                    "Custom Agent target global paths cannot overlap"
                )

        preferred = self._preferred_prefixes(
            resolved_global, normalized_project_path
        )
        target = ConfiguredAgentTarget(
            id=normalized_id,
            label=normalized_label,
            configured_global_path=resolved_global,
            configured_detect_path=resolved_detect,
            project_path=normalized_project_path,
            preferred_rel_prefixes=preferred,
        )
        now = utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO custom_agent_targets(
                        id, label, global_path, detect_path, project_path,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target.id,
                        target.label,
                        str(target.global_path()),
                        str(target.detect_path()),
                        target.project_path,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "A custom Agent target with this id or global path already exists"
            ) from exc
        return target.as_dict()

    @serialized_catalog_operation
    def delete(self, target_id: str) -> dict[str, Any]:
        normalized_id = target_id.strip()
        if normalized_id in _TARGETS_BY_KEY:
            raise ValidationError("A built-in Agent target cannot be removed")
        target = self.get_optional(normalized_id)
        if target is None or isinstance(target, AgentTarget):
            raise ValidationError(f"Unknown custom Agent target: {normalized_id}")
        self._assert_removable(target)
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM custom_agent_targets WHERE id = ?", (normalized_id,)
            )
        return {
            "deleted": True,
            "id": target.id,
            "label": target.label,
            "global_path": str(target.global_path()),
            "filesystem_changed": False,
        }

    def _home_path(self, value: str | Path, kind: str) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValidationError(f"Agent target {kind} path must be absolute")
        resolved = candidate.resolve()
        if resolved == self.home or not path_is_within(resolved, self.home):
            raise ValidationError(
                f"Agent target {kind} path must remain inside the user's home directory"
            )
        return resolved

    def _preferred_prefixes(
        self, global_path: Path, project_path: str
    ) -> tuple[str, ...]:
        home_relative = global_path.relative_to(self.home).as_posix()
        values = (project_path, home_relative, "skills")
        return tuple(dict.fromkeys(values))

    def _from_row(self, row: dict[str, Any]) -> ConfiguredAgentTarget:
        global_path = Path(row["global_path"]).expanduser().resolve()
        return ConfiguredAgentTarget(
            id=row["id"],
            label=row["label"],
            configured_global_path=global_path,
            configured_detect_path=Path(row["detect_path"]).expanduser().resolve(),
            project_path=row["project_path"],
            preferred_rel_prefixes=self._preferred_prefixes(
                global_path, row["project_path"]
            ),
        )

    @staticmethod
    def _assert_removable(target: ConfiguredAgentTarget) -> None:
        manifest_path = (
            target.global_path() / ".adaptive-skills" / "manifest.json"
        )
        if not manifest_path.exists():
            return
        try:
            if manifest_path.stat().st_size > CUSTOM_TARGET_MANIFEST_LIMIT:
                raise ValueError("manifest is too large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = manifest["entries"]
            if not isinstance(entries, list):
                raise ValueError("entries must be a list")
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise ValidationError(
                "Custom Agent target has an unreadable managed manifest; repair it before removal"
            ) from exc
        if entries:
            raise ValidationError(
                "Custom Agent target still has managed Skills; uninstall them before removing the target"
            )
