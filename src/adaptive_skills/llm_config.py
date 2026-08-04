from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .config import Settings
from .errors import NotFoundError, ValidationError


CONFIG_VERSION = 2
LEGACY_CONFIG_VERSION = 1
PROFILE_PROVIDERS = ("codex", "claude", "openai-compatible")
API_MODES = ("responses", "chat-completions", "auto")
PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class LLMProfile:
    id: str
    name: str
    provider: str
    model: str | None = None
    base_url: str | None = None
    api_mode: str | None = None
    timeout_seconds: int = 300
    max_per_run: int = 20
    credential_configured: bool = False

    def validate(self) -> "LLMProfile":
        if not isinstance(self.id, str) or not PROFILE_ID.fullmatch(self.id):
            raise ValidationError(
                "Profile ID must contain 1-64 letters, digits, dots, underscores, or hyphens"
            )
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValidationError("Profile name must contain 1-80 characters")
        if self.provider not in PROFILE_PROVIDERS:
            raise ValidationError(f"Unsupported LLM provider: {self.provider}")
        if self.model is not None and (
            not isinstance(self.model, str)
            or not self.model.strip()
            or len(self.model) > 160
        ):
            raise ValidationError("Model must be omitted or contain 1-160 characters")
        timeout = _integer(self.timeout_seconds, "LLM timeout", 30, 1800)
        maximum = _integer(self.max_per_run, "LLM max-per-run", 1, 100)
        if not isinstance(self.credential_configured, bool):
            raise ValidationError("credential_configured must be boolean")

        model = self.model.strip() if self.model else None
        if self.provider in {"codex", "claude"}:
            if self.base_url is not None or self.api_mode is not None:
                raise ValidationError("CLI profiles cannot define an API URL or mode")
            return replace(
                self,
                name=self.name.strip(),
                model=model,
                timeout_seconds=timeout,
                max_per_run=maximum,
                credential_configured=False,
            )

        if not model:
            raise ValidationError("OpenAI-compatible profiles require a model")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValidationError("OpenAI-compatible profiles require a base URL")
        base_url = self.base_url.strip().rstrip("/")
        if len(base_url) > 2048:
            raise ValidationError("LLM base URL is too long")
        if any(character.isspace() for character in base_url):
            raise ValidationError("LLM base URL cannot contain whitespace")
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationError("LLM base URL must be an absolute HTTP(S) URL without credentials, query, or fragment")
        if parsed.scheme != "https" and parsed.hostname not in LOOPBACK_HOSTS:
            raise ValidationError("Remote LLM endpoints must use HTTPS; HTTP is allowed only for loopback")
        if self.api_mode not in API_MODES:
            raise ValidationError(
                "OpenAI-compatible API mode must be responses, chat-completions, or auto"
            )
        return replace(
            self,
            name=self.name.strip(),
            model=model,
            base_url=base_url,
            timeout_seconds=timeout,
            max_per_run=maximum,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self.validate())


@dataclass(frozen=True, slots=True)
class LLMConfig:
    # The flattened fields retain the version-1 Python/CLI contract. Version-2
    # storage treats profiles + active_profile_id as authoritative.
    version: int = CONFIG_VERSION
    provider: str = "disabled"
    model: str | None = None
    timeout_seconds: int = 300
    max_per_run: int = 20
    active_profile_id: str | None = None
    profiles: tuple[LLMProfile, ...] = ()

    @property
    def active_profile(self) -> LLMProfile | None:
        if self.active_profile_id:
            return next(
                (item for item in self.profiles if item.id == self.active_profile_id),
                None,
            )
        return None

    def validate(self) -> "LLMConfig":
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise ValidationError("LLM config version must be an integer")
        if self.version not in {LEGACY_CONFIG_VERSION, CONFIG_VERSION}:
            raise ValidationError(f"Unsupported LLM config version: {self.version}")
        profiles = tuple(item.validate() for item in self.profiles)
        ids = [item.id for item in profiles]
        if len(ids) != len(set(ids)):
            raise ValidationError("LLM profile IDs must be unique")

        active_id = self.active_profile_id
        if not profiles and self.provider != "disabled":
            if self.provider not in {"codex", "claude"}:
                raise ValidationError(f"Unsupported legacy LLM provider: {self.provider}")
            legacy = LLMProfile(
                id=f"legacy-{self.provider}",
                name="Codex CLI" if self.provider == "codex" else "Claude Code",
                provider=self.provider,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                max_per_run=self.max_per_run,
            ).validate()
            profiles = (legacy,)
            active_id = legacy.id
        elif self.provider not in {"disabled", *PROFILE_PROVIDERS}:
            raise ValidationError(f"Unsupported LLM provider: {self.provider}")

        if active_id is not None and active_id not in {item.id for item in profiles}:
            raise ValidationError(f"Unknown active LLM profile: {active_id}")
        active = next((item for item in profiles if item.id == active_id), None)
        return LLMConfig(
            version=CONFIG_VERSION,
            provider=active.provider if active else "disabled",
            model=active.model if active else None,
            timeout_seconds=active.timeout_seconds if active else 300,
            max_per_run=active.max_per_run if active else 20,
            active_profile_id=active_id,
            profiles=profiles,
        )

    def as_dict(self) -> dict[str, Any]:
        value = self.validate()
        return {
            "version": CONFIG_VERSION,
            "provider": value.provider,
            "model": value.model,
            "timeout_seconds": value.timeout_seconds,
            "max_per_run": value.max_per_run,
            "active_profile_id": value.active_profile_id,
            "profiles": [item.as_dict() for item in value.profiles],
        }

    def storage_dict(self) -> dict[str, Any]:
        value = self.validate()
        return {
            "version": CONFIG_VERSION,
            "active_profile_id": value.active_profile_id,
            "profiles": [item.as_dict() for item in value.profiles],
        }

    def with_profile(self, profile: LLMProfile, *, activate: bool) -> "LLMConfig":
        profile = profile.validate()
        by_id = {item.id: item for item in self.validate().profiles}
        by_id[profile.id] = profile
        return LLMConfig(
            active_profile_id=profile.id if activate else self.active_profile_id,
            profiles=tuple(by_id[key] for key in sorted(by_id)),
        ).validate()

    def activate(self, profile_id: str | None) -> "LLMConfig":
        return LLMConfig(
            active_profile_id=profile_id,
            profiles=self.validate().profiles,
        ).validate()

    def without_profile(self, profile_id: str) -> "LLMConfig":
        value = self.validate()
        if profile_id not in {item.id for item in value.profiles}:
            raise NotFoundError(f"Unknown LLM profile: {profile_id}")
        return LLMConfig(
            active_profile_id=(
                None if value.active_profile_id == profile_id else value.active_profile_id
            ),
            profiles=tuple(item for item in value.profiles if item.id != profile_id),
        ).validate()


class LLMConfigStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def load(self) -> LLMConfig:
        path = self.settings.llm_config
        if not path.is_file():
            return LLMConfig()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("configuration root must be an object")
            version = payload.get("version", LEGACY_CONFIG_VERSION)
            if version == CONFIG_VERSION:
                raw_profiles = payload.get("profiles", [])
                if not isinstance(raw_profiles, list):
                    raise TypeError("profiles must be a list")
                profiles = tuple(LLMProfile(**item) for item in raw_profiles)
                config = LLMConfig(
                    version=version,
                    active_profile_id=payload.get("active_profile_id"),
                    profiles=profiles,
                )
            else:
                config = LLMConfig(
                    version=version,
                    provider=payload.get("provider", "disabled"),
                    model=payload.get("model"),
                    timeout_seconds=payload.get("timeout_seconds", 300),
                    max_per_run=payload.get("max_per_run", 20),
                )
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValidationError(f"Could not read LLM configuration: {exc}") from exc
        return config.validate()

    def save(self, config: LLMConfig) -> LLMConfig:
        config = config.validate()
        self.settings.ensure()
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".llm.", dir=self.settings.state_dir
        )
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config.storage_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.settings.llm_config)
        finally:
            if temporary.exists():
                temporary.unlink()
        return config

    def get_profile(self, profile_id: str) -> LLMProfile:
        config = self.load()
        profile = next((item for item in config.profiles if item.id == profile_id), None)
        if profile is None:
            raise NotFoundError(f"Unknown LLM profile: {profile_id}")
        return profile

    def replace_profiles(
        self, profiles: Iterable[LLMProfile], active_profile_id: str | None
    ) -> LLMConfig:
        return self.save(
            LLMConfig(
                profiles=tuple(profiles), active_profile_id=active_profile_id
            )
        )
