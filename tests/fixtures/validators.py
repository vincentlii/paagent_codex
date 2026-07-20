"""Shared JsonValidator instances for unit/property tests."""
from __future__ import annotations

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.config.settings import ValidationSettings


def schema_test_validator() -> JsonValidator:
    """Production validation used by schema/coherence tests."""
    return JsonValidator(ValidationSettings())


def strict_test_validator() -> JsonValidator:
    """Production-like validation for trace-semantic tests."""
    return JsonValidator(ValidationSettings())
