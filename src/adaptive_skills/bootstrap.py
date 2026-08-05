from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .database import Database, path_is_within
from .errors import ValidationError
from .scanner import (
    PRUNED_DIRECTORIES,
    CatalogScanner,
    hash_skill_tree,
    parse_frontmatter,
)
from .sources import SourceManager, _run_git, validate_source_name


MAX_ROOTS = 20
MAX_CANDIDATES = 2_000
MAX_VISITED_DIRECTORIES = 20_000
MAX_IMPORT_FILES = 2_000
MAX_IMPORT_BYTES = 200 * 1024 * 1024
LOCAL_SOURCE_NAME = "local-imports"

CURATED_SOURCES: tuple[dict[str, str], ...] = (
    {
        "id": "openai-plugins",
        "name": "openai-plugins",
        "title": "OpenAI Plugins",
        "url": "https://github.com/openai/plugins.git",
        "homepage": "https://github.com/openai/plugins",
        "license": "按插件目录声明",
        "maintainer": "OpenAI",
        "description": "OpenAI 官方 Codex 插件示例集合，包含 Skills、Agents 与工具集成。",
    },
    {
        "id": "anthropic-skills",
        "name": "anthropic-skills",
        "title": "Anthropic Agent Skills",
        "url": "https://github.com/anthropics/skills.git",
        "homepage": "https://github.com/anthropics/skills",
        "license": "混合许可",
        "maintainer": "Anthropic",
        "description": "Anthropic 官方 Agent Skills 示例、规范与模板，部分文档能力为 source-available。",
    },
    {
        "id": "superpowers",
        "name": "superpowers",
        "title": "Superpowers",
        "url": "https://github.com/obra/superpowers.git",
        "homepage": "https://github.com/obra/superpowers",
        "license": "MIT",
        "maintainer": "obra",
        "description": "跨 Codex、Claude Code 等 Agent 的软件开发方法与可组合 Skills。",
    },
)


def _normalized_git_url(value: str | None) -> str:
    return (value or "").strip().removesuffix(".git").rstrip("/").casefold()


def _candidate_id(path: Path, tree_hash: str) -> str:
    digest = hashlib.sha256()
    digest.update(str(path).encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    digest.update(tree_hash.encode("ascii"))
    return digest.hexdigest()[:24]


def _skill_directories(root: Path) -> Iterable[tuple[Path, bool]]:
    visited = 0
    for current, directories, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > MAX_VISITED_DIRECTORIES:
            raise ValidationError(
                f"Discovery exceeded {MAX_VISITED_DIRECTORIES} directories: {root}"
            )
        current_path = Path(current)
        symlinked = sorted(
            name for name in directories if (current_path / name).is_symlink()
        )
        for name in symlinked:
            linked = current_path / name
            if (linked / "SKILL.md").is_file():
                yield linked.absolute(), True
        directories[:] = sorted(
            name
            for name in directories
            if name not in PRUNED_DIRECTORIES and name not in symlinked
        )
        if "SKILL.md" in files:
            yield current_path.absolute(), current_path.is_symlink()


def _git_metadata(path: Path) -> tuple[str | None, str | None]:
    probe = _run_git(path, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode:
        return None, None
    root = Path(probe.stdout.strip()).resolve()
    remote = _run_git(root, "remote", "get-url", "origin", check=False)
    return str(root), remote.stdout.strip() if remote.returncode == 0 else None


def _preview_metadata(skill_root: Path) -> tuple[str, str]:
    skill_file = skill_root / "SKILL.md"
    try:
        if skill_file.stat().st_size > 2 * 1024 * 1024:
            return skill_root.name, "SKILL.md 超过 2 MB，导入前需要处理。"
        frontmatter, _, _ = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return skill_root.name, "无法读取 SKILL.md。"
    return (
        str(frontmatter.get("name") or skill_root.name).strip(),
        str(frontmatter.get("description") or "").strip(),
    )


def _assert_copyable_tree(root: Path) -> None:
    count = 0
    total = 0
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in directories:
            path = current_path / name
            if name in PRUNED_DIRECTORIES:
                continue
            if path.is_symlink():
                raise ValidationError(f"Skill tree contains a symlink: {path}")
            kept.append(name)
        directories[:] = kept
        for name in files:
            path = current_path / name
            if path.is_symlink():
                raise ValidationError(f"Skill tree contains a symlink: {path}")
            count += 1
            total += path.stat().st_size
            if count > MAX_IMPORT_FILES:
                raise ValidationError(
                    f"Skill tree exceeds the {MAX_IMPORT_FILES} file import limit"
                )
            if total > MAX_IMPORT_BYTES:
                raise ValidationError("Skill tree exceeds the 200 MB import limit")


class BootstrapService:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)
        self.sources = SourceManager(settings, self.database)
        self.scanner = CatalogScanner(settings, self.database)

    @staticmethod
    def default_roots() -> list[dict[str, Any]]:
        home = Path.home()
        values = (
            ("agents", "通用 Agents", home / ".agents" / "skills"),
            ("claude", "Claude Code", home / ".claude" / "skills"),
            ("codex", "Codex", home / ".codex" / "skills"),
        )
        return [
            {
                "id": identifier,
                "label": label,
                "path": str(path),
                "exists": path.is_dir(),
            }
            for identifier, label, path in values
        ]

    def status(self) -> dict[str, Any]:
        known = self.sources.list()
        known_urls = {_normalized_git_url(item.get("url")) for item in known}
        known_names = {item["name"] for item in known}
        starters = [
            {
                **item,
                "installed": item["name"] in known_names
                or _normalized_git_url(item["url"]) in known_urls,
            }
            for item in CURATED_SOURCES
        ]
        return {
            "default_roots": self.default_roots(),
            "starters": starters,
            "local_source": next(
                (item for item in known if item["name"] == LOCAL_SOURCE_NAME), None
            ),
        }

    def discover(self, roots: list[str | Path] | None = None) -> dict[str, Any]:
        requested = list(roots or [
            item["path"] for item in self.default_roots() if item["exists"]
        ])
        if len(requested) > MAX_ROOTS:
            raise ValidationError(f"Discovery accepts at most {MAX_ROOTS} roots")

        with self.database.transaction() as connection:
            existing_rows = connection.execute(
                """
                SELECT s.id, s.name, s.tree_hash, src.name AS source_name
                FROM skills s JOIN sources src ON src.id = s.source_id
                WHERE s.active = 1
                """
            ).fetchall()
        existing_hashes = {
            row["tree_hash"]: f"catalog:{row['source_name']}/{row['name']}"
            for row in existing_rows
        }
        seen_hashes: dict[str, str] = {}
        candidates: list[dict[str, Any]] = []
        root_results: list[dict[str, Any]] = []

        for raw_root in requested:
            lexical_root = Path(raw_root).expanduser().absolute()
            root_result = {
                "path": str(lexical_root),
                "exists": lexical_root.is_dir(),
                "candidate_count": 0,
                "error": None,
            }
            if not lexical_root.is_dir():
                root_result["error"] = "Directory does not exist"
                root_results.append(root_result)
                continue
            try:
                skill_paths = _skill_directories(lexical_root)
                for lexical_path, symlinked in skill_paths:
                    if len(candidates) >= MAX_CANDIDATES:
                        raise ValidationError(
                            f"Discovery exceeded the {MAX_CANDIDATES} Skill limit"
                        )
                    real_path = lexical_path.resolve()
                    display_path = lexical_path if symlinked else real_path
                    tree_hash, file_count = hash_skill_tree(real_path)
                    name, description = _preview_metadata(real_path)
                    try:
                        relative_parts = lexical_path.relative_to(lexical_root).parts
                    except ValueError:
                        relative_parts = lexical_path.parts
                    system = ".system" in relative_parts
                    managed = path_is_within(real_path, self.settings.library)
                    git_root, git_url = _git_metadata(real_path)
                    duplicate_of = existing_hashes.get(tree_hash) or seen_hashes.get(
                        tree_hash
                    )
                    if managed:
                        kind = "managed"
                        reason = "Skill 已位于当前仓库中"
                    elif system:
                        kind = "system"
                        reason = "系统内置 Skill 不参与迁移"
                    elif symlinked or lexical_path.is_symlink():
                        kind = "symlink"
                        reason = "软链接仅展示，不自动复制其目标"
                    elif git_root:
                        kind = "git"
                        reason = "将复制当前 Skill；Git 来源可另行登记或克隆"
                    else:
                        kind = "local"
                        reason = "可安全复制到本地归集来源"
                    importable = not (managed or system or symlinked or duplicate_of)
                    if duplicate_of:
                        reason = f"内容与 {duplicate_of} 重复"
                    candidate_id = _candidate_id(display_path, tree_hash)
                    candidate = {
                        "id": candidate_id,
                        "name": name,
                        "description": description,
                        "path": str(display_path),
                        "real_path": str(real_path),
                        "root": str(lexical_root),
                        "kind": kind,
                        "tree_hash": tree_hash,
                        "file_count": file_count,
                        "git_root": git_root,
                        "git_url": git_url,
                        "duplicate_of": duplicate_of,
                        "importable": bool(importable),
                        "reason": reason,
                    }
                    candidates.append(candidate)
                    root_result["candidate_count"] += 1
                    seen_hashes.setdefault(tree_hash, f"candidate:{candidate_id}")
            except (OSError, ValidationError) as exc:
                root_result["error"] = str(exc)
            root_results.append(root_result)

        return {
            "roots": root_results,
            "root_count": len(root_results),
            "candidate_count": len(candidates),
            "importable_count": sum(item["importable"] for item in candidates),
            "candidates": candidates,
        }

    def import_candidates(self, candidates: list[dict[str, str]]) -> dict[str, Any]:
        if not candidates:
            raise ValidationError("Select at least one local Skill to import")
        if len(candidates) > MAX_CANDIDATES:
            raise ValidationError(f"Import accepts at most {MAX_CANDIDATES} Skills")

        lexical_local_root = self.settings.library / LOCAL_SOURCE_NAME
        if lexical_local_root.is_symlink():
            raise ValidationError(
                f"Local import source cannot be a symlink: {lexical_local_root}"
            )
        local_root = lexical_local_root.resolve()
        source = self.sources.register_local(local_root, LOCAL_SOURCE_NAME)
        results: list[dict[str, Any]] = []
        imported = 0
        skipped = 0

        for candidate in candidates:
            raw_path = str(candidate.get("path") or "").strip()
            expected_hash = str(candidate.get("tree_hash") or "").strip()
            item: dict[str, Any] = {"path": raw_path}
            try:
                if not raw_path or not expected_hash:
                    raise ValidationError("Import candidate needs path and tree_hash")
                lexical_path = Path(raw_path).expanduser().absolute()
                if lexical_path.is_symlink():
                    raise ValidationError(
                        f"Refusing to import a symlinked Skill directory: {lexical_path}"
                    )
                skill_root = lexical_path.resolve()
                if not skill_root.is_dir() or not (skill_root / "SKILL.md").is_file():
                    raise ValidationError(f"Skill directory is missing SKILL.md: {skill_root}")
                if path_is_within(skill_root, self.settings.library):
                    raise ValidationError("Skill is already inside the configured library")
                _assert_copyable_tree(skill_root)
                current_hash, _ = hash_skill_tree(skill_root)
                if current_hash != expected_hash:
                    raise ValidationError(
                        f"Skill changed after discovery; scan it again: {skill_root}"
                    )
                destination_name = validate_source_name(skill_root.name)
                destination = local_root / destination_name
                if not path_is_within(destination, local_root):
                    raise ValidationError(f"Unsafe import destination: {destination}")
                if os.path.lexists(destination):
                    if destination.is_dir() and hash_skill_tree(destination)[0] == current_hash:
                        skipped += 1
                        item.update(
                            status="duplicate",
                            destination=str(destination),
                            error=None,
                        )
                        results.append(item)
                        continue
                    raise ValidationError(
                        f"Import destination already exists: {destination}"
                    )
                temporary = local_root / f".adaptive-skills-import-{uuid.uuid4().hex}"
                try:
                    shutil.copytree(
                        skill_root,
                        temporary,
                        ignore=shutil.ignore_patterns(*PRUNED_DIRECTORIES),
                    )
                    temporary.rename(destination)
                except Exception:
                    if temporary.exists():
                        shutil.rmtree(temporary)
                    raise
                imported += 1
                item.update(status="imported", destination=str(destination), error=None)
            except Exception as exc:
                item.update(status="failed", destination=None, error=str(exc))
            results.append(item)

        scan = None
        if imported or skipped:
            scan = self.scanner.scan(source["id"])[0]
        failed = sum(item["status"] == "failed" for item in results)
        return {
            "source": source,
            "total": len(candidates),
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "results": results,
            "scan": scan,
        }

    def install_starters(self, starter_ids: list[str]) -> dict[str, Any]:
        if not starter_ids:
            raise ValidationError("Select at least one curated Git source")
        available = {item["id"]: item for item in CURATED_SOURCES}
        unknown = sorted(set(starter_ids) - set(available))
        if unknown:
            raise ValidationError(f"Unknown curated source: {', '.join(unknown)}")

        installed_sources = self.sources.list()
        installed_urls = {
            _normalized_git_url(item.get("url")): item for item in installed_sources
        }
        results: list[dict[str, Any]] = []
        for starter_id in dict.fromkeys(starter_ids):
            starter = available[starter_id]
            existing = installed_urls.get(_normalized_git_url(starter["url"]))
            if existing:
                results.append(
                    {
                        "id": starter_id,
                        "status": "already-installed",
                        "source": existing,
                        "scan": None,
                        "error": None,
                    }
                )
                continue
            try:
                source = self.sources.add(starter["url"], name=starter["name"])
                scan = self.scanner.scan(source["id"])[0]
                installed_urls[_normalized_git_url(starter["url"])] = source
                results.append(
                    {
                        "id": starter_id,
                        "status": "installed",
                        "source": source,
                        "scan": scan,
                        "error": None,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "id": starter_id,
                        "status": "failed",
                        "source": None,
                        "scan": None,
                        "error": str(exc),
                    }
                )
        return {
            "total": len(results),
            "installed": sum(item["status"] == "installed" for item in results),
            "already_installed": sum(
                item["status"] == "already-installed" for item in results
            ),
            "failed": sum(item["status"] == "failed" for item in results),
            "results": results,
        }
