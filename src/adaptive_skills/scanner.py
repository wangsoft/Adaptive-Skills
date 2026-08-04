from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .database import Database, json_value, path_is_within, utc_now
from .errors import NotFoundError, ValidationError
from .sources import SourceManager, git_head


PRUNED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}
TEXT_EXTENSIONS = {
    "",
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".css",
}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    rule: str
    message: str
    file: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ScannedSkill:
    id: str
    source_id: str
    rel_path: str
    directory_name: str
    name: str
    description: str
    license: str | None
    compatibility: str | None
    allowed_tools: str | None
    metadata: dict[str, Any]
    body: str
    skill_md_path: str
    content_hash: str
    tree_hash: str
    line_count: int
    file_count: int
    valid: bool
    validation: list[Finding]
    audit_severity: str
    audit: list[Finding]


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        if not inner.strip():
            return []
        return [
            part.strip().strip("'\"")
            for part in next(csv.reader([inner], skipinitialspace=True))
        ]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value in {"true", "false"}:
        return value == "true"
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[Finding]]:
    lines = text.splitlines()
    findings: list[Finding] = []
    if not lines or lines[0].strip() != "---":
        return (
            {},
            text,
            [
                Finding(
                    "high",
                    "frontmatter.missing",
                    "SKILL.md must start with YAML frontmatter",
                    "SKILL.md",
                    1,
                )
            ],
        )
    closing = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"), None
    )
    if closing is None:
        return (
            {},
            text,
            [
                Finding(
                    "high",
                    "frontmatter.unclosed",
                    "YAML frontmatter has no closing delimiter",
                    "SKILL.md",
                    1,
                )
            ],
        )

    metadata: dict[str, Any] = {}
    header = lines[1:closing]
    index = 0
    while index < len(header):
        raw_line = header[index]
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            index += 1
            continue
        if raw_line.startswith((" ", "\t")) or ":" not in raw_line:
            findings.append(
                Finding(
                    "medium",
                    "frontmatter.syntax",
                    "Unsupported frontmatter line",
                    "SKILL.md",
                    index + 2,
                )
            )
            index += 1
            continue
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            findings.append(
                Finding(
                    "medium",
                    "frontmatter.empty-key",
                    "Frontmatter key is empty",
                    "SKILL.md",
                    index + 2,
                )
            )
            index += 1
            continue
        if raw_value in {">", ">-", "|", "|-"}:
            block: list[str] = []
            index += 1
            while index < len(header):
                block_line = header[index]
                if block_line and not block_line.startswith((" ", "\t")):
                    break
                block.append(block_line.strip())
                index += 1
            separator = "\n" if raw_value.startswith("|") else " "
            metadata[key] = separator.join(block).strip()
            continue
        metadata[key] = _parse_scalar(raw_value)
        index += 1
    return metadata, "\n".join(lines[closing + 1 :]).strip(), findings


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_tree_files(root: Path) -> Iterable[Path]:
    for current, directories, files in os.walk(root, followlinks=False):
        symlinked_directories = sorted(
            name for name in directories if (Path(current) / name).is_symlink()
        )
        directories[:] = sorted(
            name
            for name in directories
            if name not in PRUNED_DIRECTORIES and name not in symlinked_directories
        )
        for directory in symlinked_directories:
            yield Path(current) / directory
        for filename in sorted(files):
            yield Path(current) / filename


def hash_skill_tree(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in _iter_tree_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"SYMLINK")
            digest.update(os.readlink(path).encode("utf-8", errors="replace"))
        else:
            try:
                digest.update(path.read_bytes())
            except OSError as exc:
                digest.update(f"UNREADABLE:{exc.__class__.__name__}".encode())
        count += 1
    return digest.hexdigest(), count


AUDIT_RULES: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    (
        "critical",
        "shell.remote-pipe",
        re.compile(
            r"(?:curl|wget)\b[^\n|]{0,500}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", re.I
        ),
        "Downloads are piped directly to a shell",
    ),
    (
        "critical",
        "filesystem.broad-delete",
        re.compile(
            r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(?:/|~|\$HOME)(?:\s|$)", re.I
        ),
        "Command may recursively delete a broad filesystem root",
    ),
    (
        "high",
        "credentials.sensitive-path",
        re.compile(
            r"(?:\.ssh/(?:id_|config)|\.aws/credentials|\.config/gcloud|keychain|login\.keychain)",
            re.I,
        ),
        "References a sensitive credential location",
    ),
    (
        "high",
        "execution.obfuscated",
        re.compile(r"(?:eval|exec)\s*\([^\n]{0,200}(?:base64|b64decode)", re.I),
        "Executes obfuscated or decoded content",
    ),
    (
        "medium",
        "prompt.override",
        re.compile(
            r"(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions",
            re.I,
        ),
        "Contains an instruction-override phrase",
    ),
    (
        "medium",
        "git.global-config",
        re.compile(r"git\s+config\s+--global", re.I),
        "Modifies global Git configuration",
    ),
    (
        "low",
        "network.download",
        re.compile(r"\b(?:curl|wget)\b", re.I),
        "Uses a network download command",
    ),
)


def audit_skill(root: Path) -> tuple[str, list[Finding]]:
    findings: list[Finding] = []
    total_bytes = 0
    for path in _iter_tree_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = (path.parent / os.readlink(path)).resolve()
            severity = "high" if not path_is_within(target, root) else "medium"
            findings.append(
                Finding(
                    severity, "filesystem.symlink", "Skill contains a symlink", relative
                )
            )
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > 512_000 or total_bytes > 2_000_000:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            total_bytes += len(content.encode("utf-8", errors="replace"))
        except OSError:
            findings.append(
                Finding(
                    "medium",
                    "filesystem.unreadable",
                    "File could not be inspected",
                    relative,
                )
            )
            continue
        for severity, rule, pattern, message in AUDIT_RULES:
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                findings.append(Finding(severity, rule, message, relative, line))
                if len(findings) >= 100:
                    break
            if len(findings) >= 100:
                break
        if len(findings) >= 100:
            break
    severity = max(
        (item.severity for item in findings), key=SEVERITY_ORDER.get, default="none"
    )
    return severity, findings


def stable_skill_id(source_id: str, rel_path: str) -> str:
    try:
        namespace = uuid.UUID(source_id)
    except ValueError:
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, source_id)
    return str(uuid.uuid5(namespace, rel_path))


def scan_skill(source_id: str, source_root: Path, skill_file: Path) -> ScannedSkill:
    if skill_file.is_symlink():
        raise ValidationError(f"Refusing symlinked SKILL.md: {skill_file}")
    if not path_is_within(skill_file, source_root):
        raise ValidationError(f"Skill path escapes source root: {skill_file}")
    if skill_file.stat().st_size > 2_000_000:
        raise ValidationError(f"SKILL.md exceeds the 2 MB scan limit: {skill_file}")
    raw = skill_file.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    metadata, body, validation = parse_frontmatter(text)
    skill_root = skill_file.parent
    rel_path = skill_root.relative_to(source_root).as_posix()
    directory_name = skill_root.name
    name_value = metadata.get("name", "")
    description_value = metadata.get("description", "")
    name = str(name_value).strip() if not isinstance(name_value, list) else ""
    description = (
        str(description_value).strip()
        if not isinstance(description_value, list)
        else ""
    )

    if not name:
        validation.append(
            Finding(
                "high",
                "spec.name-required",
                "Frontmatter requires a name",
                "SKILL.md",
                1,
            )
        )
    elif len(name) > 64 or not NAME_PATTERN.fullmatch(name):
        validation.append(
            Finding(
                "high",
                "spec.name-format",
                "Name must be 1-64 lowercase alphanumeric or hyphen characters",
                "SKILL.md",
                1,
            )
        )
    elif name != directory_name:
        validation.append(
            Finding(
                "high",
                "spec.directory-name",
                "Skill directory must match frontmatter name",
                "SKILL.md",
                1,
            )
        )
    if not description:
        validation.append(
            Finding(
                "high",
                "spec.description-required",
                "Frontmatter requires a description",
                "SKILL.md",
                1,
            )
        )
    elif len(description) > 1024:
        validation.append(
            Finding(
                "high",
                "spec.description-length",
                "Description must not exceed 1024 characters",
                "SKILL.md",
                1,
            )
        )
    allowed = metadata.get("allowed-tools")
    if allowed is not None and not isinstance(allowed, str):
        validation.append(
            Finding(
                "medium",
                "spec.allowed-tools",
                "allowed-tools must be a space-delimited string",
                "SKILL.md",
                1,
            )
        )

    tree_hash, file_count = hash_skill_tree(skill_root)
    audit_severity, audit = audit_skill(skill_root)
    is_valid = not any(
        SEVERITY_ORDER[item.severity] >= SEVERITY_ORDER["high"] for item in validation
    )
    return ScannedSkill(
        id=stable_skill_id(source_id, rel_path),
        source_id=source_id,
        rel_path=rel_path,
        directory_name=directory_name,
        name=name or directory_name,
        description=description,
        license=str(metadata["license"])
        if metadata.get("license") is not None
        else None,
        compatibility=str(metadata["compatibility"])
        if metadata.get("compatibility") is not None
        else None,
        allowed_tools=allowed if isinstance(allowed, str) else None,
        metadata=metadata,
        body=body[:100_000],
        skill_md_path=str(skill_file),
        content_hash=_hash_bytes(raw),
        tree_hash=tree_hash,
        line_count=len(text.splitlines()),
        file_count=file_count,
        valid=is_valid,
        validation=validation,
        audit_severity=audit_severity,
        audit=audit,
    )


def discover_skill_files(source_root: Path) -> list[Path]:
    found: list[Path] = []
    for current, directories, files in os.walk(source_root, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name not in PRUNED_DIRECTORIES
            and not (Path(current) / name).is_symlink()
        )
        if "SKILL.md" in files:
            found.append(Path(current) / "SKILL.md")
    return sorted(found, key=lambda path: path.relative_to(source_root).as_posix())


class CatalogScanner:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)
        self.sources = SourceManager(settings, self.database)

    def scan(self, source: str | None = None) -> list[dict[str, Any]]:
        sources = [self.sources.get(source)] if source else self.sources.list()
        if not sources:
            raise NotFoundError("No sources are registered")
        return [self.scan_source(item) for item in sources]

    def scan_source(self, source: dict[str, Any]) -> dict[str, Any]:
        root = Path(source["local_path"]).resolve()
        if not root.is_dir():
            raise NotFoundError(f"Source path is missing: {root}")
        started = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO scan_runs(source_id, started_at) VALUES (?, ?)",
                (source["id"], started),
            )
            run_id = cursor.lastrowid

        scanned: list[ScannedSkill] = []
        failures: list[dict[str, str]] = []
        for skill_file in discover_skill_files(root):
            try:
                scanned.append(scan_skill(source["id"], root, skill_file))
            except (OSError, ValidationError) as exc:
                failures.append({"path": str(skill_file), "error": str(exc)})

        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE skills SET active = 0 WHERE source_id = ?", (source["id"],)
            )
            connection.execute(
                "DELETE FROM skill_fts WHERE skill_id IN (SELECT id FROM skills WHERE source_id = ?)",
                (source["id"],),
            )
            for item in scanned:
                connection.execute(
                    """
                    INSERT INTO skills(
                        id, source_id, rel_path, directory_name, name, description,
                        license, compatibility, allowed_tools, metadata_json, body,
                        skill_md_path, content_hash, tree_hash, line_count, file_count,
                        valid, validation_json, audit_severity, audit_json, active,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        rel_path=excluded.rel_path,
                        directory_name=excluded.directory_name,
                        name=excluded.name,
                        description=excluded.description,
                        license=excluded.license,
                        compatibility=excluded.compatibility,
                        allowed_tools=excluded.allowed_tools,
                        metadata_json=excluded.metadata_json,
                        body=excluded.body,
                        skill_md_path=excluded.skill_md_path,
                        content_hash=excluded.content_hash,
                        tree_hash=excluded.tree_hash,
                        line_count=excluded.line_count,
                        file_count=excluded.file_count,
                        valid=excluded.valid,
                        validation_json=excluded.validation_json,
                        audit_severity=excluded.audit_severity,
                        audit_json=excluded.audit_json,
                        active=1,
                        updated_at=excluded.updated_at
                    """,
                    (
                        item.id,
                        item.source_id,
                        item.rel_path,
                        item.directory_name,
                        item.name,
                        item.description,
                        item.license,
                        item.compatibility,
                        item.allowed_tools,
                        json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
                        item.body,
                        item.skill_md_path,
                        item.content_hash,
                        item.tree_hash,
                        item.line_count,
                        item.file_count,
                        int(item.valid),
                        json.dumps(
                            [asdict(finding) for finding in item.validation],
                            ensure_ascii=False,
                        ),
                        item.audit_severity,
                        json.dumps(
                            [asdict(finding) for finding in item.audit],
                            ensure_ascii=False,
                        ),
                        now,
                        now,
                    ),
                )
                self._index_skill(connection, item.id)
            valid_count = sum(item.valid for item in scanned)
            connection.execute(
                """
                UPDATE scan_runs
                SET completed_at = ?, discovered = ?, valid = ?, invalid = ?, error = ?
                WHERE id = ?
                """,
                (
                    now,
                    len(scanned) + len(failures),
                    valid_count,
                    len(scanned) - valid_count + len(failures),
                    json.dumps(failures, ensure_ascii=False) if failures else None,
                    run_id,
                ),
            )
            connection.execute(
                """
                UPDATE sources SET head_sha = ?, status = 'scanned',
                    last_scanned_at = ?, updated_at = ? WHERE id = ?
                """,
                (git_head(root), now, now, source["id"]),
            )
        return {
            "source_id": source["id"],
            "source": source["name"],
            "discovered": len(scanned) + len(failures),
            "valid": sum(item.valid for item in scanned),
            "invalid": sum(not item.valid for item in scanned) + len(failures),
            "critical": sum(item.audit_severity == "critical" for item in scanned),
            "failures": failures,
        }

    @staticmethod
    def _index_skill(connection, skill_id: str) -> None:
        row = connection.execute(
            """
            SELECT s.name, s.description, s.body,
                   a.category_l1, a.category_l2, a.problem, a.use_case,
                   a.notes, a.tags_json
            FROM skills s LEFT JOIN annotations a ON a.skill_id = s.id
            WHERE s.id = ?
            """,
            (skill_id,),
        ).fetchone()
        connection.execute("DELETE FROM skill_fts WHERE skill_id = ?", (skill_id,))
        if row is None:
            return
        annotations = " ".join(
            str(value or "")
            for value in (
                row["category_l1"],
                row["category_l2"],
                row["problem"],
                row["use_case"],
                row["notes"],
                " ".join(json_value(row["tags_json"], [])),
            )
        )
        connection.execute(
            "INSERT INTO skill_fts(skill_id, name, description, annotations, body) VALUES (?, ?, ?, ?, ?)",
            (skill_id, row["name"], row["description"], annotations, row["body"]),
        )
