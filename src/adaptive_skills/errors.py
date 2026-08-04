class AdaptiveSkillsError(Exception):
    """Expected user-facing failure."""


class ConflictError(AdaptiveSkillsError):
    """An operation would overwrite unmanaged or changed content."""


class NotFoundError(AdaptiveSkillsError):
    """A requested source, skill, or project entry does not exist."""


class ValidationError(AdaptiveSkillsError):
    """Input failed a trust-boundary validation."""
