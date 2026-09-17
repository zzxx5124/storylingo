"""Analysis artifact version policy.

The numeric version is scoped to the persisted chapter-analysis artifact
namespace.  Structured-output, provider, usage-metrics, and transport schema
versions must not be inferred from these values.
"""
from __future__ import annotations

from .. import v4_contracts as contracts

CANONICAL_WRITE_VERSION = contracts.ANALYSIS_SCHEMA_VERSION_V4
NATIVE_READ_VERSIONS = frozenset((CANONICAL_WRITE_VERSION,))
COMPATIBILITY_READ_VERSIONS = frozenset((contracts.ANALYSIS_SCHEMA_VERSION_V3,))
LEGACY_READ_VERSIONS = frozenset((
    contracts.ANALYSIS_SCHEMA_VERSION_LEGACY,
    contracts.ANALYSIS_SCHEMA_VERSION_V2,
))
SUPPORTED_READ_VERSIONS = frozenset(
    NATIVE_READ_VERSIONS | COMPATIBILITY_READ_VERSIONS | LEGACY_READ_VERSIONS
)

NATIVE = "native"
COMPATIBILITY = "compatibility"
LEGACY = "legacy"
UNSUPPORTED = "unsupported"


class AnalysisVersionError(ValueError):
    """The artifact version is missing, malformed, or unsupported."""


def classify_version(value) -> str:
    """Return the read boundary for an artifact schema version.

    ``bool`` is rejected deliberately because it is an ``int`` subclass in
    Python and must never be accepted as a schema version.
    """
    if type(value) is not int:
        return UNSUPPORTED
    if value in NATIVE_READ_VERSIONS:
        return NATIVE
    if value in COMPATIBILITY_READ_VERSIONS:
        return COMPATIBILITY
    if value in LEGACY_READ_VERSIONS:
        return LEGACY
    return UNSUPPORTED


def require_version(value, *, context: str = "analysis artifact") -> tuple[int, str]:
    """Validate and classify a persisted analysis version."""
    mode = classify_version(value)
    if mode == UNSUPPORTED:
        raise AnalysisVersionError(f"{context} schemaVersion 不支援或格式錯誤")
    return value, mode


def require_artifact(artifact: dict, *, context: str = "analysis artifact") -> tuple[int, str]:
    """Validate the artifact envelope before selecting a consumer path."""
    if not isinstance(artifact, dict):
        raise AnalysisVersionError(f"{context} 必須是 JSON 物件")
    return require_version(artifact.get("schemaVersion"), context=context)


def is_native(value) -> bool:
    return classify_version(value) == NATIVE


def is_compatibility(value) -> bool:
    return classify_version(value) == COMPATIBILITY


def is_legacy(value) -> bool:
    return classify_version(value) == LEGACY


def require_execution_target(value, *, context: str = "analysis execution") -> int:
    """Allow only explicit v3 compatibility or v4 canonical execution.

    v1/v2 are readable legacy inputs, but are never accepted as a new
    execution target.  This prevents a caller from accidentally creating a
    new analysis row in an obsolete artifact contract.
    """
    version, mode = require_version(value, context=context)
    if mode not in (NATIVE, COMPATIBILITY):
        raise AnalysisVersionError(f"{context} 只能明確指定 v3 compatibility 或 v4 canonical")
    return version
