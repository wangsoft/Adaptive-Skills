from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_targets import AGENT_TARGETS, list_agent_targets


AGENT_SCOPE_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = tuple(
    (target.id, target.label, target.global_parts) for target in AGENT_TARGETS
)


def default_agent_roots(home: Path | None = None) -> list[dict[str, Any]]:
    return list_agent_targets(home)
