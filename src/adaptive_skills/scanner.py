from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .config import Settings
from .database import Database, json_value, path_is_within, utc_now
from .errors import NotFoundError, ValidationError
from .sources import SourceManager, git_head


PRUNED_DIRECTORIES = {
    ".adaptive-skills",
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
class AuditFinding:
    severity: str
    rule: str
    message: str
    file: str
    line: int | None
    context: str
    classification: str
    finding_id: str
    content_digest: str
    content_summary: str


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
    audit: list[AuditFinding]


def _json_compatible_yaml(
    value: Any,
    *,
    ancestors: frozenset[int] = frozenset(),
    depth: int = 0,
    budget: list[int] | None = None,
) -> Any:
    if depth > 32:
        raise ValueError("YAML frontmatter exceeds the nesting limit")
    remaining = budget if budget is not None else [10_000]
    remaining[0] -= 1
    if remaining[0] < 0:
        raise ValueError("YAML frontmatter exceeds the node limit")
    if isinstance(value, dict):
        if id(value) in ancestors:
            raise ValueError("YAML frontmatter contains a recursive alias")
        nested = ancestors | {id(value)}
        return {
            str(key): _json_compatible_yaml(
                item, ancestors=nested, depth=depth + 1, budget=remaining
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if id(value) in ancestors:
            raise ValueError("YAML frontmatter contains a recursive alias")
        nested = ancestors | {id(value)}
        return [
            _json_compatible_yaml(
                item, ancestors=nested, depth=depth + 1, budget=remaining
            )
            for item in value
        ]
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


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
        (
            index
            for index in range(1, len(lines))
            if lines[index].rstrip() == "---" and not lines[index].startswith((" ", "\t"))
        ),
        None,
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

    header = "\n".join(lines[1:closing])
    try:
        loaded = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = int(mark.line) + 2 if mark is not None else 1
        problem = str(getattr(exc, "problem", None) or "Invalid YAML frontmatter")
        findings.append(
            Finding("medium", "frontmatter.syntax", problem, "SKILL.md", line)
        )
        return {}, "\n".join(lines[closing + 1 :]).strip(), findings
    if loaded is None:
        metadata: dict[str, Any] = {}
    elif not isinstance(loaded, dict):
        findings.append(
            Finding(
                "medium",
                "frontmatter.mapping",
                "YAML frontmatter must be a mapping",
                "SKILL.md",
                1,
            )
        )
        metadata = {}
    else:
        try:
            metadata = _json_compatible_yaml(loaded)
        except ValueError as exc:
            findings.append(
                Finding("medium", "frontmatter.complexity", str(exc), "SKILL.md", 1)
            )
            metadata = {}
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
SUBSUMED_RULES = {"network.download": {"shell.remote-pipe"}}

DENYLIST_PATTERN = re.compile(
    r"(?:\bdo\s+not\b|\bdon't\b|\bnever\b|\bmust\s+not\b|\bavoid\b|"
    r"\bforbidden\b|\bdenylist\b|\bblocklist\b|禁止|严禁|不要|不得|禁用|黑名单)",
    re.I,
)
SHELLISH_LINE = re.compile(
    r"^(?:[-*+]\s+)?(?:[$>]\s*)?(?:sudo\s+)?"
    r"(?:curl|wget|rm|git|bash|sh|zsh|fish|python(?:3)?|node|npm|npx|pnpm|yarn|eval|exec)\b",
    re.I,
)
IMPERATIVE_LINE = re.compile(
    r"^(?:[-*+]\s+)?(?:read|open|copy|upload|download|delete|remove|write|modify|send|execute|run)\b",
    re.I,
)
COMMAND_KEY = re.compile(
    r"(?:^|[\"'])(?:scripts?|command|cmd|run|exec|shell)(?:[\"']?\s*[:=])",
    re.I,
)
PROMPT_COMMAND = re.compile(r"^(?:[-*+]\s+)?(?:ignore|disregard)\b", re.I)
SHELL_FENCE_LANGUAGES = {"", "sh", "shell", "bash", "zsh", "fish", "console", "terminal"}
EXECUTABLE_EXTENSIONS = {".sh", ".bash", ".zsh", ".fish", ".ps1"}
CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx"}
DOCUMENT_EXTENSIONS = {".md", ".txt", ".rst"}


def _line_context(path: Path, lines: list[str], line_index: int, rule: str) -> str:
    line = lines[line_index]
    if DENYLIST_PATTERN.search(line):
        return "denylist"
    previous_nonempty = next(
        (
            candidate.strip()
            for candidate in reversed(lines[max(0, line_index - 4) : line_index])
            if candidate.strip()
        ),
        "",
    )
    if DENYLIST_PATTERN.search(previous_nonempty) and (
        previous_nonempty.endswith(":") or previous_nonempty.startswith("#")
    ):
        return "denylist"

    suffix = path.suffix.lower()
    stripped = line.strip()
    if suffix in DOCUMENT_EXTENSIONS or path.name == "SKILL.md":
        in_fence = False
        fence_language = ""
        for prior in lines[: line_index + 1]:
            fence = re.match(r"^\s*```\s*([\w+-]*)", prior)
            if fence:
                if in_fence:
                    in_fence = False
                    fence_language = ""
                else:
                    in_fence = True
                    fence_language = fence.group(1).casefold()
        if in_fence and fence_language in SHELL_FENCE_LANGUAGES:
            return "command_invocation"
        if SHELLISH_LINE.search(stripped) or IMPERATIVE_LINE.search(stripped):
            return "command_invocation"
        if rule == "prompt.override" and PROMPT_COMMAND.search(stripped):
            return "command_invocation"
        return "documentation"

    comment_prefixes = ("#", "//", "/*", "*", "<!--")
    if stripped.startswith(comment_prefixes):
        return "documentation"
    if suffix in EXECUTABLE_EXTENSIONS or suffix in CODE_EXTENSIONS:
        return "command_invocation"
    if COMMAND_KEY.search(line):
        return "command_invocation"
    try:
        if path.stat().st_mode & 0o111:
            return "command_invocation"
    except OSError:
        pass
    return "documentation"


def _audit_finding(
    severity: str,
    rule: str,
    message: str,
    relative: str,
    line: int | None,
    context: str,
    content_summary: str,
) -> AuditFinding:
    normalized = re.sub(r"\s+", " ", content_summary).strip()[:240]
    content_digest = _hash_bytes(normalized.encode("utf-8", errors="replace"))
    identity = f"{rule}\0{relative}\0{line or 0}\0{content_digest}"
    classification = (
        "risk" if context in {"command_invocation", "artifact"} else "capability_hint"
    )
    return AuditFinding(
        severity=severity,
        rule=rule,
        message=message,
        file=relative,
        line=line,
        context=context,
        classification=classification,
        finding_id=_hash_bytes(identity.encode("utf-8")),
        content_digest=content_digest,
        content_summary=normalized,
    )


def _finding_priority(finding: AuditFinding) -> tuple[int, int]:
    return (
        1 if finding.classification == "risk" else 0,
        SEVERITY_ORDER.get(finding.severity, 0),
    )


def _record_finding(findings: list[AuditFinding], finding: AuditFinding) -> None:
    if len(findings) < 100:
        findings.append(finding)
        return
    weakest_index = min(
        range(len(findings)),
        key=lambda index: _finding_priority(findings[index]),
    )
    if _finding_priority(finding) > _finding_priority(findings[weakest_index]):
        findings[weakest_index] = finding


def decorate_audit_findings(
    findings: list[dict[str, Any]],
    tree_hash: str,
    reviews: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    review_map = reviews or {}
    decorated: list[dict[str, Any]] = []
    for raw in findings:
        finding = dict(raw)
        summary = str(finding.get("content_summary") or finding.get("message") or "")
        digest = str(
            finding.get("content_digest")
            or _hash_bytes(summary.encode("utf-8", errors="replace"))
        )
        finding_id = str(
            finding.get("finding_id")
            or _hash_bytes(
                f"{finding.get('rule', '')}\0{finding.get('file', '')}\0"
                f"{finding.get('line') or 0}\0{digest}".encode("utf-8")
            )
        )
        context = str(finding.get("context") or "command_invocation")
        classification = str(
            finding.get("classification")
            or (
                "risk"
                if context in {"command_invocation", "artifact"}
                else "capability_hint"
            )
        )
        finding.update(
            {
                "finding_id": finding_id,
                "content_digest": digest,
                "content_summary": summary,
                "context": context,
                "classification": classification,
            }
        )
        review = review_map.get(finding_id)
        review_matches = bool(
            review
            and review.get("finding_digest") == digest
            and review.get("skill_tree_hash") == tree_hash
        )
        if classification != "risk":
            status = "informational"
        elif review_matches:
            status = str(review["status"])
        else:
            status = "unreviewed"
        finding.update(
            {
                "status": status,
                "review_stale": bool(review and not review_matches),
                "review_note": review.get("note") if review_matches else None,
                "reviewed_at": review.get("reviewed_at") if review_matches else None,
                "review_content_summary": (
                    review.get("content_summary") if review else None
                ),
            }
        )
        decorated.append(finding)
    effective = max(
        (
            str(item.get("severity", "none"))
            for item in decorated
            if item["classification"] == "risk"
            and item["status"] != "reviewed_false_positive"
        ),
        key=SEVERITY_ORDER.get,
        default="none",
    )
    return decorated, effective


def audit_skill(root: Path) -> tuple[str, list[AuditFinding]]:
    findings: list[AuditFinding] = []
    total_bytes = 0
    for path in _iter_tree_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = (path.parent / os.readlink(path)).resolve()
            severity = "high" if not path_is_within(target, root) else "medium"
            _record_finding(
                findings,
                _audit_finding(
                    severity,
                    "filesystem.symlink",
                    "Skill contains a symlink",
                    relative,
                    None,
                    "artifact",
                    f"symlink -> {os.readlink(path)}",
                ),
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
            _record_finding(
                findings,
                _audit_finding(
                    "medium",
                    "filesystem.unreadable",
                    "File could not be inspected",
                    relative,
                    None,
                    "artifact",
                    "unreadable file",
                ),
            )
            continue
        lines = content.splitlines()
        for severity, rule, pattern, message in AUDIT_RULES:
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                line_index = max(0, min(line - 1, len(lines) - 1))
                summary = lines[line_index] if lines else match.group(0)
                context = _line_context(path, lines or [summary], line_index, rule)
                if any(
                    existing.line == line
                    and existing.file == relative
                    and existing.rule == rule
                    for existing in findings
                ):
                    continue
                if any(
                    existing.line == line
                    and existing.file == relative
                    and existing.rule in SUBSUMED_RULES.get(rule, set())
                    for existing in findings
                ):
                    continue
                _record_finding(
                    findings,
                    _audit_finding(
                        severity, rule, message, relative, line, context, summary
                    ),
                )
    severity = max(
        (
            item.severity
            for item in findings
            if item.classification == "risk"
        ),
        key=SEVERITY_ORDER.get,
        default="none",
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
    name = name_value.strip() if isinstance(name_value, str) else ""
    description = (
        description_value.strip() if isinstance(description_value, str) else ""
    )

    if "name" in metadata and not isinstance(name_value, str):
        validation.append(
            Finding(
                "high",
                "spec.name-type",
                "Frontmatter name must be a string",
                "SKILL.md",
                1,
            )
        )
    elif not name:
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
    elif rel_path != "." and name != directory_name:
        validation.append(
            Finding(
                "high",
                "spec.directory-name",
                "Skill directory must match frontmatter name",
                "SKILL.md",
                1,
            )
        )
    if "description" in metadata and not isinstance(description_value, str):
        validation.append(
            Finding(
                "high",
                "spec.description-type",
                "Frontmatter description must be a string",
                "SKILL.md",
                1,
            )
        )
    elif not description:
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
        effective_severities: dict[str, str] = {}
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE skills SET active = 0 WHERE source_id = ?", (source["id"],)
            )
            connection.execute(
                "DELETE FROM skill_fts WHERE skill_id IN (SELECT id FROM skills WHERE source_id = ?)",
                (source["id"],),
            )
            for item in scanned:
                review_rows = connection.execute(
                    "SELECT * FROM audit_reviews WHERE skill_id = ?",
                    (item.id,),
                ).fetchall()
                reviews = {row["finding_id"]: dict(row) for row in review_rows}
                _, effective_severity = decorate_audit_findings(
                    [asdict(finding) for finding in item.audit],
                    item.tree_hash,
                    reviews,
                )
                effective_severities[item.id] = effective_severity
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
                        effective_severity,
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
            "critical": sum(
                effective_severities.get(item.id, item.audit_severity) == "critical"
                for item in scanned
            ),
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
