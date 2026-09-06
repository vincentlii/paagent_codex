"""Capability negotiation for model providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

StructuredOutputMode = Literal["json_schema", "json_object", "text"]
_UNSUPPORTED_STRICT_OUTPUT_KEYWORDS = frozenset(
    {
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
    }
)
_NAMED_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions"})


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
                "schema": project_strict_output_schema(schema),
            },
        }
    return {"type": "json_object"}


def project_strict_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt Draft-07 constraints to the OpenAI/Codex strict-output subset.

    This schema is generation guidance only. Callers must keep the original
    schema as the authoritative post-generation validation contract.
    """
    projected: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_STRICT_OUTPUT_KEYWORDS or key == "const":
            continue
        if key in _NAMED_SCHEMA_MAPS and isinstance(value, Mapping):
            projected[key] = {
                name: project_strict_output_schema(child)
                for name, child in value.items()
                if isinstance(child, Mapping)
            }
        elif isinstance(value, Mapping):
            projected[key] = project_strict_output_schema(value)
        elif isinstance(value, (list, tuple)):
            projected[key] = [
                project_strict_output_schema(item)
                if isinstance(item, Mapping)
                else item
                for item in value
            ]
        else:
            projected[key] = value
    if "const" in schema:
        constant = schema["const"]
        projected["enum"] = [constant]
        projected.setdefault("type", _json_schema_type(constant))
    if projected.get("type") == "object" or "properties" in projected:
        properties = projected.get("properties")
        if isinstance(properties, dict):
            originally_required = set(schema.get("required") or ())
            for name, child in tuple(properties.items()):
                if name not in originally_required:
                    properties[name] = _nullable_schema(child)
            projected["required"] = list(properties)
            projected["additionalProperties"] = False
    return projected


def normalize_strict_output_instance(
    value: Any,
    schema: Mapping[str, Any],
) -> tuple[Any, int]:
    """Remove only null placeholders introduced for originally optional fields."""
    return _normalize_strict_value(value, schema, root=schema)


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
    for definitions_key in ("$defs", "definitions"):
        definitions = schema.get(definitions_key)
        if isinstance(definitions, Mapping) and not all(
            not isinstance(child, Mapping) or strict_schema_compatible(child)
            for child in definitions.values()
        ):
            return False
    return True


def _nullable_schema(schema: Any) -> Any:
    if not isinstance(schema, Mapping) or _schema_allows_null(schema):
        return schema
    return {"anyOf": [dict(schema), {"type": "null"}]}


def _schema_allows_null(schema: Mapping[str, Any]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "null" or (
        isinstance(schema_type, list) and "null" in schema_type
    ):
        return True
    return any(
        isinstance(branch, Mapping) and branch.get("type") == "null"
        for key in ("anyOf", "oneOf")
        for branch in (schema.get(key) or [])
    )


def _normalize_strict_value(
    value: Any,
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any],
) -> tuple[Any, int]:
    resolved = _resolve_local_ref(schema, root)
    branches = resolved.get("anyOf") or resolved.get("oneOf")
    if isinstance(branches, list) and value is not None:
        for branch in branches:
            if isinstance(branch, Mapping) and _value_matches_schema_type(value, branch, root):
                return _normalize_strict_value(value, branch, root=root)
    properties = resolved.get("properties")
    if isinstance(value, dict) and isinstance(properties, Mapping):
        required = set(resolved.get("required") or ())
        normalized: dict[str, Any] = {}
        removed = 0
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, Mapping) and key not in required and child is None:
                removed += 1
                continue
            if isinstance(child_schema, Mapping):
                nested, nested_removed = _normalize_strict_value(
                    child,
                    child_schema,
                    root=root,
                )
                normalized[key] = nested
                removed += nested_removed
            else:
                normalized[key] = child
        return normalized, removed
    items = resolved.get("items")
    if isinstance(value, list) and isinstance(items, Mapping):
        normalized_items: list[Any] = []
        removed = 0
        for child in value:
            normalized, nested_removed = _normalize_strict_value(child, items, root=root)
            normalized_items.append(normalized)
            removed += nested_removed
        return normalized_items, removed
    return value, 0


def _resolve_local_ref(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
) -> Mapping[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            return schema
        current = current[part]
    return current if isinstance(current, Mapping) else schema


def _value_matches_schema_type(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
) -> bool:
    resolved = _resolve_local_ref(schema, root)
    schema_type = resolved.get("type")
    if isinstance(schema_type, list):
        return any(
            _value_matches_schema_type(value, {"type": item}, root)
            for item in schema_type
        )
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }.get(str(schema_type), True)


def _json_schema_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    raise ValueError("schema const has no JSON type")


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
