from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol

from .config import Settings
from .errors import ValidationError


KEYRING_SERVICE = "com.adaptive-skills.llm"


class SecretStore(Protocol):
    def get(self, profile_id: str) -> str | None: ...

    def set(self, profile_id: str, secret: str) -> None: ...

    def delete(self, profile_id: str) -> None: ...


class KeyringSecretStore:
    """Store API keys in the OS credential backend, never in library files."""

    def __init__(self, settings: Settings):
        digest = hashlib.sha256(str(settings.library).encode("utf-8")).hexdigest()[:20]
        self.account_prefix = f"library-{digest}"

    @staticmethod
    def available() -> bool:
        try:
            import keyring  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _module():
        try:
            import keyring
        except ImportError as exc:
            raise ValidationError(
                "OS credential storage is unavailable; install adaptive-skills with its default dependencies"
            ) from exc
        return keyring

    def _account(self, profile_id: str) -> str:
        return f"{self.account_prefix}:{profile_id}"

    @staticmethod
    def _environment_name(profile_id: str) -> str:
        suffix = re.sub(r"[^A-Za-z0-9]", "_", profile_id).upper()
        return f"ADAPTIVE_SKILLS_LLM_API_KEY_{suffix}"

    def get(self, profile_id: str) -> str | None:
        environment = os.environ.get(self._environment_name(profile_id)) or os.environ.get(
            "ADAPTIVE_SKILLS_LLM_API_KEY"
        )
        if environment:
            return environment
        try:
            return self._module().get_password(
                KEYRING_SERVICE, self._account(profile_id)
            )
        except Exception as exc:
            raise ValidationError("Could not read the OS credential store") from exc

    def set(self, profile_id: str, secret: str) -> None:
        if not isinstance(secret, str) or not secret or len(secret) > 8192:
            raise ValidationError("API key must contain 1-8192 characters")
        try:
            self._module().set_password(
                KEYRING_SERVICE, self._account(profile_id), secret
            )
        except Exception as exc:
            raise ValidationError("Could not write the OS credential store") from exc

    def delete(self, profile_id: str) -> None:
        try:
            keyring = self._module()
            try:
                keyring.delete_password(KEYRING_SERVICE, self._account(profile_id))
            except keyring.errors.PasswordDeleteError:
                return
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("Could not update the OS credential store") from exc
