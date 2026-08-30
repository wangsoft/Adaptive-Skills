from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .database import Database, path_is_within, row_dict, utc_now
from .errors import ConflictError, NotFoundError, ValidationError
from .operation_lock import serialized_catalog_operation


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


def _git_url_identity(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return f"file:{Path(parsed.path).resolve()}"
    if parsed.scheme:
        host = (parsed.hostname or parsed.netloc).casefold()
        path = parsed.path.strip("/")
        return f"remote:{host}:{path}"
    if re.match(r"^[^/@\s]+@[^:\s]+:.+", value):
        host, path = value.split(":", 1)
        return f"remote:{host.rsplit('@', 1)[-1].casefold()}:{path.strip('/')}"
    return value


def _source_owner(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    path = parsed.path
    if not parsed.scheme and re.match(r"^[^/@\s]+@[^:\s]+:.+", value):
        path = value.split(":", 1)[1]
    parts = [part for part in path.rstrip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner = re.sub(r"[^A-Za-z0-9._-]+", "-", parts[-2]).strip("-.")
    return owner or None


class SourceManager:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)

    @serialized_catalog_operation
    def add(
        self, url: str, name: str | None = None, tracked_ref: str | None = None
    ) -> dict:
        validate_git_url(url)
        self.settings.ensure()
        existing = self.list(include_removed=True)
        identity = _git_url_identity(url)
        same_remote = next(
            (
                item
                for item in existing
                if identity is not None
                and _git_url_identity(item.get("url")) == identity
            ),
            None,
        )
        if same_remote is not None:
            if same_remote["status"] == "removed":
                raise ConflictError(
                    f"Source is in removed history: {same_remote['name']}; restore it or permanently forget that record before cloning the same repository"
                )
            if not os.path.lexists(same_remote["local_path"]):
                if name is not None and name.casefold() != same_remote["name"].casefold():
                    raise ConflictError(
                        f"The missing repository is already registered as {same_remote['name']}; leave the display name empty or use that existing name to restore it"
                    )
                return self._recover_missing_remote(
                    same_remote,
                    url=url,
                    tracked_ref=tracked_ref,
                )
            raise ConflictError(f"Source repository is already registered: {same_remote['name']}")
        if name is not None:
            source_name = validate_source_name(name)
            self._assert_name_available(source_name, existing)
            destination = self.settings.sources_dir / source_name
            if os.path.lexists(destination):
                raise ConflictError(f"Source destination already exists: {destination}")
        else:
            source_name = self._available_name(
                derive_source_name(url),
                url=url,
                rows=existing,
                require_free_destination=True,
            )
        destination = self.settings.sources_dir / source_name

        self._clone_repository(url, destination, tracked_ref)
        try:
            return self._insert(source_name, destination, url, tracked_ref)
        except Exception:
            # The clone remains recoverable and visible if catalog registration fails.
            raise

    def _recover_missing_remote(
        self,
        item: dict,
        *,
        url: str,
        tracked_ref: str | None,
    ) -> dict:
        destination = Path(item["local_path"]).expanduser().absolute()
        sources_root = self.settings.sources_dir.resolve()
        if destination.parent.resolve() != sources_root:
            raise ConflictError(
                f"The registered repository directory is missing outside the managed Skill library: {destination}; restore that directory or permanently forget the source record first"
            )
        if item.get("update_policy", "remote") != "remote":
            raise ConflictError(
                f"The missing source is marked local-maintained: {item['name']}; switch it to remote-following or permanently forget the record before cloning"
            )
        if os.path.lexists(destination):
            raise ConflictError(f"Source destination already exists: {destination}")

        recovery_ref = tracked_ref or item.get("tracked_ref")
        self._clone_repository(url, destination, recovery_ref)
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT status, url, local_path, updated_at FROM sources WHERE id = ?",
                (item["id"],),
            ).fetchone()
            if (
                current is None
                or current["status"] == "removed"
                or current["local_path"] != item["local_path"]
                or current["updated_at"] != item["updated_at"]
                or _git_url_identity(current["url"]) != _git_url_identity(item["url"])
            ):
                raise ConflictError(
                    "The source record changed while its missing repository was being restored; the cloned directory was retained for manual reconciliation"
                )
            connection.execute(
                """
                UPDATE sources
                SET url = ?, tracked_ref = ?, head_sha = ?, status = 'registered',
                    updated_at = ?
                WHERE id = ?
                """,
                (url, recovery_ref, git_head(destination), now, item["id"]),
            )
        result = self.get(item["id"])
        result["recovered"] = True
        return result

    def _clone_repository(
        self, url: str, destination: Path, tracked_ref: str | None
    ) -> None:
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
        rows = self.list(include_removed=True)
        for item in rows:
            if Path(item["local_path"]).resolve() != local_path:
                continue
            if item["status"] == "removed":
                raise ConflictError(
                    f"A removed source record still owns this path: {item['name']}; restore it or permanently forget the record first"
                )
            raise ConflictError(f"Source path is already registered: {item['name']}")
        if name is not None:
            source_name = validate_source_name(name)
            self._assert_name_available(source_name, rows)
        else:
            source_name = self._available_name(
                local_path.name,
                url=url,
                rows=rows,
                require_free_destination=False,
            )
        return self._insert(source_name, local_path, url, tracked_ref)

    def register_local(
        self, path: str | Path, name: str | None = None
    ) -> dict:
        """Register a local-maintained source inside the configured library."""
        self.settings.ensure()
        lexical_path = Path(path).expanduser().absolute()
        if lexical_path.is_symlink():
            raise ValidationError(f"Local source cannot be a symlink: {lexical_path}")
        local_path = lexical_path.resolve()
        if not path_is_within(local_path, self.settings.library):
            raise ValidationError(
                f"Local source must be inside the Skill library: {local_path}"
            )
        if local_path == self.settings.library:
            raise ValidationError("The Skill library root cannot be a local source")
        local_path.mkdir(parents=True, exist_ok=True)
        for item in self.list():
            if Path(item["local_path"]).resolve() == local_path:
                return item
        source_name = validate_source_name(name or local_path.name)
        return self._insert(
            source_name,
            local_path,
            None,
            None,
            update_policy="local",
        )

    def discover(self) -> list[dict]:
        return self.discover_detailed()["sources"]

    def discover_detailed(self) -> dict:
        self.settings.ensure()
        added: list[dict] = []
        failures: list[dict] = []
        rows = self.list(include_removed=True)
        active_paths = {
            Path(item["local_path"]).resolve()
            for item in rows
            if item["status"] != "removed"
        }
        removed_paths = {
            Path(item["local_path"]).resolve(): item
            for item in rows
            if item["status"] == "removed"
        }
        for child in sorted(
            self.settings.library.iterdir(), key=lambda item: item.name.casefold()
        ):
            if (
                child.name.startswith(".")
                or not child.is_dir()
                or child.resolve() in active_paths
            ):
                continue
            probe = _run_git(child, "rev-parse", "--show-toplevel", check=False)
            if (
                probe.returncode == 0
                and Path(probe.stdout.strip()).resolve() == child.resolve()
            ):
                removed = removed_paths.get(child.resolve())
                if removed is not None:
                    remote = _run_git(
                        child, "remote", "get-url", "origin", check=False
                    )
                    current_url = remote.stdout.strip() if remote.returncode == 0 else None
                    same_remote = bool(current_url or removed.get("url")) and (
                        _git_url_identity(current_url)
                        == _git_url_identity(removed.get("url"))
                    )
                    current_head = git_head(child)
                    removed_head = removed.get("head_sha")
                    same_local_head = (
                        not current_url
                        and not removed.get("url")
                        and current_head is not None
                        and removed_head is not None
                        and current_head == removed_head
                    )
                    if same_remote or same_local_head:
                        continue
                    failures.append(
                        {
                            "source_id": removed["id"],
                            "source": child.name,
                            "path": str(child.resolve()),
                            "status": "failed",
                            "type": "ConflictError",
                            "error": (
                                f"A different repository now uses the path of removed source {removed['name']}; permanently forget that history record, then discover again"
                            ),
                        }
                    )
                    continue
                try:
                    added.append(self.register(child))
                except (ConflictError, ValidationError, NotFoundError) as exc:
                    failures.append(
                        {
                            "source_id": str(child.resolve()),
                            "source": child.name,
                            "path": str(child.resolve()),
                            "status": "failed",
                            "type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
        return {"sources": added, "failures": failures}

    def _assert_name_available(self, name: str, rows: list[dict] | None = None) -> None:
        existing = rows if rows is not None else self.list(include_removed=True)
        conflict = next(
            (item for item in existing if item["name"].casefold() == name.casefold()),
            None,
        )
        if conflict is None:
            return
        qualifier = "removed history" if conflict["status"] == "removed" else "catalog"
        raise ConflictError(
            f"Source name is already registered in {qualifier}: {conflict['name']}"
        )

    def _available_name(
        self,
        base: str,
        *,
        url: str | None,
        rows: list[dict],
        require_free_destination: bool,
    ) -> str:
        base = validate_source_name(base)
        owner = _source_owner(url)
        seeds = [base]
        if owner and owner.casefold() != base.casefold():
            owner_part = owner[:39]
            prefix = f"{owner_part}-"
            seeds.append(f"{prefix}{base[: 80 - len(prefix)]}")
        taken = {item["name"].casefold() for item in rows}

        def available(candidate: str) -> bool:
            if candidate.casefold() in taken:
                return False
            return not (
                require_free_destination
                and os.path.lexists(self.settings.sources_dir / candidate)
            )

        for candidate in seeds:
            candidate = validate_source_name(candidate)
            if available(candidate):
                return candidate
        for index in range(2, 10_000):
            suffix = f"-{index}"
            candidate = validate_source_name(f"{base[: 80 - len(suffix)]}{suffix}")
            if available(candidate):
                return candidate
        raise ConflictError(f"Could not allocate a unique source name for: {base}")

    def _insert(
        self,
        name: str,
        path: Path,
        url: str | None,
        tracked_ref: str | None,
        *,
        update_policy: str = "remote",
    ) -> dict:
        if update_policy not in UPDATE_POLICIES:
            raise ValidationError(f"Unknown source update policy: {update_policy}")
        now = utc_now()
        source_id = str(uuid.uuid4())
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO sources(
                        id, name, url, local_path, tracked_ref, update_policy, head_sha,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'registered', ?, ?)
                    """,
                    (
                        source_id,
                        name,
                        url,
                        str(path),
                        tracked_ref,
                        update_policy,
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

    def list(self, *, include_removed: bool = False) -> list[dict]:
        clause = "" if include_removed else "WHERE status != 'removed'"
        with self.database.transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM sources {clause} ORDER BY name"
                )
            ]

    def get(self, source: str, *, include_removed: bool = False) -> dict:
        clause = "" if include_removed else "AND status != 'removed'"
        with self.database.transaction() as connection:
            row = connection.execute(
                f"SELECT * FROM sources WHERE (id = ? OR name = ?) {clause}",
                (source, source),
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
