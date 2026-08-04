from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .database import Database, row_dict, utc_now
from .errors import ConflictError, NotFoundError, ValidationError


SOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
UPDATE_POLICIES = {"remote", "local"}


def _run_git(
    path: Path | None, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if path is not None:
        command.extend(["-C", str(path)])
    command.extend(args)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise ValidationError(f"Git command failed: {detail}")
    return result


def git_head(path: Path) -> str | None:
    result = _run_git(path, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def git_dirty(path: Path) -> bool:
    result = _run_git(path, "status", "--porcelain", "--untracked-files=normal")
    return bool(result.stdout.strip())


def derive_source_name(value: str) -> str:
    candidate = value.rstrip("/").rsplit("/", 1)[-1]
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-.")
    if not candidate:
        raise ValidationError("Could not derive a source name; pass --name explicitly")
    return candidate[:80]


def validate_source_name(name: str) -> str:
    if not SOURCE_NAME.fullmatch(name) or name in {".", "..", ".adaptive-skills"}:
        raise ValidationError(
            "Source name must be 1-80 safe filename characters and cannot be reserved"
        )
    return name


def validate_git_url(url: str) -> None:
    if url.startswith("-") or "\x00" in url:
        raise ValidationError("Invalid Git URL")
    parsed = urlparse(url)
    supported = {"http", "https", "ssh", "git", "file"}
    is_scp_style = bool(re.match(r"^[^/@\s]+@[^:\s]+:.+", url))
    if parsed.scheme not in supported and not is_scp_style:
        raise ValidationError(
            "Git URL must use http(s), ssh, git, file, or SCP-style SSH"
        )


class SourceManager:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)

    def add(
        self, url: str, name: str | None = None, tracked_ref: str | None = None
    ) -> dict:
        validate_git_url(url)
        source_name = validate_source_name(name or derive_source_name(url))
        self.settings.ensure()
        destination = self.settings.sources_dir / source_name
        if os.path.lexists(destination):
            raise ConflictError(f"Source destination already exists: {destination}")

        temporary = (
            self.settings.sources_dir / f".adaptive-skills-clone-{uuid.uuid4().hex}"
        )
        try:
            args = ["clone", "--origin", "origin"]
            if tracked_ref:
                args.extend(["--branch", tracked_ref])
            args.extend(["--", url, str(temporary)])
            _run_git(None, *args)
            temporary.rename(destination)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        try:
            return self._insert(source_name, destination, url, tracked_ref)
        except Exception:
            # The clone remains recoverable and visible if catalog registration fails.
            raise

    def register(
        self,
        path: str | Path,
        name: str | None = None,
        url: str | None = None,
        tracked_ref: str | None = None,
    ) -> dict:
        local_path = Path(path).expanduser().resolve()
        if not local_path.is_dir():
            raise NotFoundError(f"Source path does not exist: {local_path}")
        probe = _run_git(local_path, "rev-parse", "--show-toplevel", check=False)
        if probe.returncode:
            raise ValidationError(f"Source is not a Git repository: {local_path}")
        top_level = Path(probe.stdout.strip()).resolve()
        if top_level != local_path:
            raise ValidationError(
                f"Register the Git repository root instead: {top_level}"
            )
        source_name = validate_source_name(name or local_path.name)
        if url is None:
            remote = _run_git(local_path, "remote", "get-url", "origin", check=False)
            url = remote.stdout.strip() if remote.returncode == 0 else None
        return self._insert(source_name, local_path, url, tracked_ref)

    def discover(self) -> list[dict]:
        self.settings.ensure()
        added: list[dict] = []
        known = {Path(item["local_path"]).resolve() for item in self.list()}
        for child in sorted(
            self.settings.library.iterdir(), key=lambda item: item.name.casefold()
        ):
            if (
                child.name.startswith(".")
                or not child.is_dir()
                or child.resolve() in known
            ):
                continue
            probe = _run_git(child, "rev-parse", "--show-toplevel", check=False)
            if (
                probe.returncode == 0
                and Path(probe.stdout.strip()).resolve() == child.resolve()
            ):
                added.append(self.register(child))
        return added

    def _insert(
        self,
        name: str,
        path: Path,
        url: str | None,
        tracked_ref: str | None,
    ) -> dict:
        now = utc_now()
        source_id = str(uuid.uuid4())
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO sources(
                        id, name, url, local_path, tracked_ref, head_sha,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'registered', ?, ?)
                    """,
                    (
                        source_id,
                        name,
                        url,
                        str(path),
                        tracked_ref,
                        git_head(path),
                        now,
                        now,
                    ),
                )
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise ConflictError(
                    f"Source name or path is already registered: {name}"
                ) from exc
            raise
        return self.get(source_id)

    def list(self) -> list[dict]:
        with self.database.transaction() as connection:
            return [
                dict(row)
                for row in connection.execute("SELECT * FROM sources ORDER BY name")
            ]

    def get(self, source: str) -> dict:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE id = ? OR name = ?", (source, source)
            ).fetchone()
        value = row_dict(row)
        if value is None:
            raise NotFoundError(f"Unknown source: {source}")
        return value

    def set_update_policy(self, source: str, policy: str) -> dict:
        if policy not in UPDATE_POLICIES:
            raise ValidationError(
                f"Update policy must be one of: {', '.join(sorted(UPDATE_POLICIES))}"
            )
        item = self.get(source)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE sources SET update_policy = ?, updated_at = ? WHERE id = ?",
                (policy, utc_now(), item["id"]),
            )
        return self.get(item["id"])

    def update(self, source: str) -> dict:
        item = self.get(source)
        path = Path(item["local_path"])
        if item.get("update_policy", "remote") == "local":
            raise ConflictError(
                f"Source is local-maintained; switch to remote policy before updating: {item['name']}"
            )
        if git_dirty(path):
            raise ConflictError(f"Refusing to update dirty source: {item['name']}")
        if not item.get("url"):
            raise ValidationError(f"Source has no remote URL: {item['name']}")

        tracked_ref = item.get("tracked_ref")
        if tracked_ref:
            _run_git(path, "fetch", "--prune", "origin")
            current = _run_git(path, "branch", "--show-current").stdout.strip()
            if current != tracked_ref:
                switch = _run_git(path, "switch", tracked_ref, check=False)
                if switch.returncode:
                    _run_git(
                        path,
                        "switch",
                        "--create",
                        tracked_ref,
                        "--track",
                        f"origin/{tracked_ref}",
                    )
            _run_git(path, "merge", "--ff-only", f"origin/{tracked_ref}")
        else:
            _run_git(path, "pull", "--ff-only")

        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE sources SET head_sha = ?, status = 'updated', updated_at = ? WHERE id = ?",
                (git_head(path), now, item["id"]),
            )
        return self.get(item["id"])
