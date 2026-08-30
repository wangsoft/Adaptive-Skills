from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


GITHUB_API_VERSION = "2026-03-10"
GITHUB_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class GitHubRepositoryMetadata:
    stars: int | None
    etag: str | None
    not_modified: bool = False


def github_repository_slug(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    raw = value.strip()
    parsed = urlparse(raw)
    if parsed.scheme:
        host = (parsed.hostname or "").casefold()
        path = parsed.path
    else:
        match = re.fullmatch(r"[^/@\s]+@([^:\s]+):(.+)", raw)
        if match is None:
            return None
        host = match.group(1).casefold()
        path = match.group(2)
    if host != "github.com":
        return None
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        return None
    if not GITHUB_REPOSITORY_PART.fullmatch(
        owner
    ) or not GITHUB_REPOSITORY_PART.fullmatch(repository):
        return None
    return owner, repository


def fetch_github_repository_metadata(
    value: str | None,
    *,
    etag: str | None = None,
    timeout: float = 4.0,
) -> GitHubRepositoryMetadata | None:
    slug = github_repository_slug(value)
    if slug is None:
        return None
    owner, repository = slug
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Adaptive-Skills",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if etag:
        headers["If-None-Match"] = etag
    request = Request(
        f"https://api.github.com/repos/{owner}/{repository}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            stars = payload.get("stargazers_count")
            if not isinstance(stars, int) or stars < 0:
                return None
            return GitHubRepositoryMetadata(
                stars=stars,
                etag=response.headers.get("ETag"),
            )
    except HTTPError as exc:
        if exc.code == 304:
            return GitHubRepositoryMetadata(
                stars=None,
                etag=exc.headers.get("ETag") or etag,
                not_modified=True,
            )
        return None
    except (OSError, URLError, UnicodeError, json.JSONDecodeError):
        return None
