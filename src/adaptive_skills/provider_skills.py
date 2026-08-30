from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any

from .scanner import hash_skill_tree


ANTHROPIC_LICENSE_MARKERS = (
    "Anthropic, PBC",
    "outside the Services",
)
ANTHROPIC_SKILL_CREATOR_MARKER = (
    "extends Claude's capabilities with specialized knowledge, workflows, or tool integrations"
)


def _read_limited(path: Path, limit: int = 128_000) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _infer_scope(root: Path) -> str | None:
    parts = root.parts
    if len(parts) >= 2 and parts[-2:] == (".claude", "skills"):
        return "claude"
    if len(parts) >= 2 and parts[-2:] == (".codex", "skills"):
        return "codex"
    if len(parts) >= 2 and parts[-2:] == (".agents", "skills"):
        return "agents"
    return None


def _codex_vendor_roots(root: Path) -> tuple[Path, ...]:
    if len(root.parents) < 2:
        return ()
    home = root.parents[1]
    return (
        home / ".codex" / "vendor_imports" / "skills" / "skills",
    )


def _same_tree(first: Path, second: Path) -> bool:
    try:
        first_hash, _ = hash_skill_tree(first)
        second_hash, _ = hash_skill_tree(second)
    except OSError:
        return False
    return hmac.compare_digest(first_hash, second_hash)


def _codex_vendor_match(root: Path, skill: Path) -> Path | None:
    for vendor_root in _codex_vendor_roots(root):
        if not vendor_root.is_dir():
            continue
        try:
            candidates = vendor_root.rglob("SKILL.md")
            for candidate_file in candidates:
                candidate = candidate_file.parent
                if candidate.name == skill.name and _same_tree(skill, candidate):
                    return candidate
        except OSError:
            continue
    return None


def _is_anthropic_skill_creator(skill: Path) -> bool:
    if skill.name != "skill-creator":
        return False
    text = _read_limited(skill / "SKILL.md")
    required = (
        skill / "scripts" / "init_skill.py",
        skill / "scripts" / "package_skill.py",
        skill / "scripts" / "quick_validate.py",
    )
    return ANTHROPIC_SKILL_CREATOR_MARKER in text and all(
        path.is_file() for path in required
    )


def provider_skill_info(
    root: Path,
    skill: Path,
    scope_id: str | None = None,
) -> dict[str, Any] | None:
    """Identify provider-managed Skills that Adaptive Skills must not adopt.

    Detection is deliberately evidence-based. Names alone never make a Skill
    provider-owned: Claude requires provider license/package markers and Codex
    requires exact tree identity with its local vendor import.
    """

    lexical_root = root.expanduser().absolute()
    lexical_skill = skill.expanduser().absolute()
    scope = scope_id or _infer_scope(lexical_root)

    try:
        relative_parts = lexical_skill.relative_to(lexical_root).parts
    except ValueError:
        relative_parts = ()
    if ".system" in relative_parts:
        provider = "Codex" if scope == "codex" else "Agent"
        return {
            "provider": provider,
            "reason": f"{provider} 系统内置 Skill 由宿主自行更新，不参与迁移",
            "evidence": "system-directory",
        }

    if scope == "claude":
        license_text = _read_limited(lexical_skill / "LICENSE.txt")
        if all(marker in license_text for marker in ANTHROPIC_LICENSE_MARKERS):
            return {
                "provider": "Claude",
                "reason": "Claude 内置 Skill 受 Anthropic 服务条款管理，不迁移到本地仓库",
                "evidence": "anthropic-provider-license",
            }
        if _is_anthropic_skill_creator(lexical_skill):
            return {
                "provider": "Claude",
                "reason": "Claude 内置 skill-creator 由 Claude 自行更新，不迁移到本地仓库",
                "evidence": "anthropic-skill-creator-package",
            }

    if scope == "codex":
        vendor_match = _codex_vendor_match(lexical_root, lexical_skill)
        if vendor_match is not None:
            return {
                "provider": "Codex",
                "reason": "Codex 自带 Skill 与本机 vendor import 完全一致，由 Codex 自行更新",
                "evidence": f"exact-vendor-tree:{vendor_match}",
            }
    return None
