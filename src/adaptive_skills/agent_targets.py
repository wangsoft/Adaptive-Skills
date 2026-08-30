from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError


@dataclass(frozen=True)
class AgentTarget:
    id: str
    label: str
    global_parts: tuple[str, ...]
    project_path: str
    preferred_rel_prefixes: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    def global_path(self, home: Path | None = None) -> Path:
        root = (home or Path.home()).expanduser().resolve()
        return root.joinpath(*self.global_parts)

    def as_dict(self, home: Path | None = None) -> dict[str, Any]:
        path = self.global_path(home)
        return {
            "id": self.id,
            "label": self.label,
            "path": str(path),
            "global_path": str(path),
            "project_path": self.project_path,
            "exists": path.is_dir(),
            "aliases": list(self.aliases),
            "preferred_rel_prefixes": list(self.preferred_rel_prefixes),
        }


AGENT_TARGETS: tuple[AgentTarget, ...] = (
    AgentTarget(
        id="agents",
        label="通用 Agents",
        global_parts=(".agents", "skills"),
        project_path=".agents/skills",
        preferred_rel_prefixes=(".agents/skills", "skills", "plugin/skills"),
        aliases=("auto", "universal"),
    ),
    AgentTarget(
        id="claude",
        label="Claude Code",
        global_parts=(".claude", "skills"),
        project_path=".claude/skills",
        preferred_rel_prefixes=(".claude/skills", "skills", "plugin/skills"),
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
    ),
    AgentTarget(
        id="cursor",
        label="Cursor",
        global_parts=(".cursor", "skills"),
        project_path=".cursor/skills",
        preferred_rel_prefixes=(".cursor/skills", ".agents/skills", "skills"),
    ),
    AgentTarget(
        id="gemini",
        label="Gemini CLI",
        global_parts=(".gemini", "skills"),
        project_path=".gemini/skills",
        preferred_rel_prefixes=(".gemini/skills", ".agents/skills", "skills"),
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
    ),
)

_TARGETS_BY_KEY = {
    key: target
    for target in AGENT_TARGETS
    for key in (target.id, *target.aliases)
}


def get_agent_target(identifier: str) -> AgentTarget:
    target = _TARGETS_BY_KEY.get(identifier)
    if target is None:
        raise ValidationError(f"Unknown Agent target: {identifier}")
    return target


def list_agent_targets(home: Path | None = None) -> list[dict[str, Any]]:
    return [target.as_dict(home) for target in AGENT_TARGETS]


def project_target_choices() -> tuple[str, ...]:
    return tuple(_TARGETS_BY_KEY)
