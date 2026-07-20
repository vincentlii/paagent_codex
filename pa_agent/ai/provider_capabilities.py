"""Capability negotiation for model providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

StructuredOutputMode = Literal["json_schema", "json_object", "text"]


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    structured_output_mode: StructuredOutputMode
    supports_stream_usage: bool
    source: Literal["configured", "certified", "conservative_default"]


def resolve_provider_capabilities(
    settings: Any,
    *,
    client: object | None = None,
) -> ProviderCapabilities:
    """Resolve capabilities without claiming unsupported API behavior."""
    override = str(
        getattr(settings, "structured_output_mode", "auto") or "auto"
    ).strip().lower()
    base_url = str(getattr(settings, "base_url", "") or "").strip()
    model = str(getattr(settings, "model", "") or "").strip()
    provider_id = _provider_id(base_url, model, client)

    if override in {"json_schema", "json_object", "text"}:
        return ProviderCapabilities(
            provider_id=provider_id,
            structured_output_mode=override,  # type: ignore[arg-type]
            supports_stream_usage=provider_id not in {"cursor_sdk"},
            source="configured",
        )
    if provider_id == "openai":
        return ProviderCapabilities(
            provider_id=provider_id,
            structured_output_mode="json_schema",
            supports_stream_usage=True,
            source="certified",
        )
    if provider_id == "deepseek":
        return ProviderCapabilities(
            provider_id=provider_id,
            structured_output_mode="json_object",
            supports_stream_usage=True,
            source="certified",
        )
    if provider_id == "cursor_sdk":
        return ProviderCapabilities(
            provider_id=provider_id,
            structured_output_mode="text",
            supports_stream_usage=False,
            source="certified",
        )
    # Unknown OpenAI-compatible gateways may accept JSON mode, but assuming so
    # would turn a compatibility guess into a production requirement.
    return ProviderCapabilities(
        provider_id=provider_id,
        structured_output_mode="text",
        supports_stream_usage=False,
        source="conservative_default",
    )


def response_format_for(
    capabilities: ProviderCapabilities,
    *,
    schema: Mapping[str, Any] | None,
    schema_name: str,
) -> dict[str, Any] | None:
    mode = capabilities.structured_output_mode
    if mode == "text":
        return None
    if mode == "json_schema" and schema is not None and strict_schema_compatible(schema):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": _safe_schema_name(schema_name),
                "strict": True,
                "schema": dict(schema),
            },
        }
    return {"type": "json_object"}


def strict_schema_compatible(schema: Mapping[str, Any]) -> bool:
    """Return whether every object is closed and requires every property."""
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null = [value for value in schema_type if value != "null"]
        if len(non_null) == 1:
            schema_type = non_null[0]
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or schema.get("additionalProperties") is not False:
            return False
        if set(required or ()) != set(properties):
            return False
        if not all(
            strict_schema_compatible(value)
            for value in properties.values()
            if isinstance(value, Mapping)
        ):
            return False
    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, Mapping) and not strict_schema_compatible(items):
            return False
    for branch_key in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(branch_key)
        if isinstance(branches, list) and not all(
            not isinstance(branch, Mapping) or strict_schema_compatible(branch)
            for branch in branches
        ):
            return False
    return True


def _provider_id(base_url: str, model: str, client: object | None) -> str:
    class_name = type(client).__name__.lower() if client is not None else ""
    if "cursor" in class_name:
        return "cursor_sdk"
    host = (urlparse(base_url).hostname or "").lower()
    model_lower = model.lower()
    if host == "api.openai.com":
        return "openai"
    if host == "api.deepseek.com" or host.endswith(".deepseek.com"):
        return "deepseek"
    if "openclaw_cs" in model_lower:
        return "cursor_sdk"
    return host or class_name or "unknown"


def _safe_schema_name(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in str(value or "analysis")
    )
    return cleaned[:64] or "analysis"
