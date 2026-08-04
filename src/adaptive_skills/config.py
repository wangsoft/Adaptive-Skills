from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    library: Path
    state_dir: Path
    database: Path
    sources_dir: Path
    llm_config: Path

    @classmethod
    def load(cls, library: str | Path | None = None) -> "Settings":
        raw = library or os.environ.get("ADAPTIVE_SKILLS_LIBRARY") or "~/skills"
        root = Path(raw).expanduser().resolve()
        state = root / ".adaptive-skills"
        return cls(
            library=root,
            state_dir=state,
            database=state / "catalog.db",
            sources_dir=root,
            llm_config=state / "llm.json",
        )

    def ensure(self) -> None:
        self.library.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
