from __future__ import annotations

import subprocess
from pathlib import Path


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    git(path, "config", "user.email", "tests@example.invalid")
    git(path, "config", "user.name", "Adaptive Skills Tests")
    return path


def write_skill(
    repo: Path,
    name: str,
    description: str,
    *,
    body: str = "# Instructions\n\nDo the requested work safely.",
    directory: str | None = None,
) -> Path:
    root = repo / "skills" / (directory or name)
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return root


def commit_all(repo: Path, message: str = "fixtures") -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")
