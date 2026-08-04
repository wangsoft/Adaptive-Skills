from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .errors import AdaptiveSkillsError
from .scanner import CatalogScanner
from .sources import SourceManager, git_head


class SourceRefreshService:
    """Safely update and rescan every registered Git source."""

    def __init__(self, settings: Settings):
        self.sources = SourceManager(settings)
        self.scanner = CatalogScanner(settings)

    def refresh_all(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for source in self.sources.list():
            before_sha = git_head(Path(source["local_path"]))
            try:
                if source.get("update_policy", "remote") == "local":
                    scan = self.scanner.scan(source["id"])[0]
                    results.append(
                        {
                            "source_id": source["id"],
                            "source": source["name"],
                            "status": "local",
                            "before_sha": before_sha,
                            "after_sha": git_head(Path(source["local_path"])),
                            "scan": scan,
                        }
                    )
                    continue
                updated_source = self.sources.update(source["id"])
                scan = self.scanner.scan(source["id"])[0]
                after_sha = updated_source.get("head_sha")
                results.append(
                    {
                        "source_id": source["id"],
                        "source": source["name"],
                        "status": (
                            "updated" if before_sha != after_sha else "unchanged"
                        ),
                        "before_sha": before_sha,
                        "after_sha": after_sha,
                        "scan": scan,
                    }
                )
            except AdaptiveSkillsError as exc:
                results.append(
                    {
                        "source_id": source["id"],
                        "source": source["name"],
                        "status": "failed",
                        "before_sha": before_sha,
                        "type": type(exc).__name__,
                        "error": str(exc),
                    }
                )

        return {
            "total": len(results),
            "updated": sum(item["status"] == "updated" for item in results),
            "unchanged": sum(item["status"] == "unchanged" for item in results),
            "local": sum(item["status"] == "local" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "results": results,
        }
