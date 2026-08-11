"""Thin provider adapters for future independent semantic-review execution."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping, Protocol


SUPPORTED_PROVIDERS = ("anthropic", "gemini")
PROVIDER_ENVIRONMENT_VARIABLES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

ANTHROPIC_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {"minimum", "maximum", "minLength", "maxLength", "uniqueItems"}
)
GEMINI_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {"minLength", "maxLength", "pattern", "uniqueItems"}
)
GEMINI_BLOCK_CODES = frozenset(
    {
        "safety",
        "recitation",
        "language",
        "prohibited_content",
        "spii",
        "blocklist",
        "image_safety",
        "image_prohibited_content",
        "image_recitation",
        "image_other",
        "content_blocked",
        "escalation",
    }
)

_SCHEMA_MAP_KEYWORDS = frozenset(
    {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas"}
)
_SCHEMA_SINGLE_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})


class ProviderAdapterError(RuntimeError):
    """A provider adapter could not return a usable response."""


@dataclass(frozen=True)
class ProviderResult:
    raw_response: Any
    raw_response_text: str | None
    resolved_reported_model: str | None
    provider_request_id: str | None
    terminal_status: str | None = None
    provider_block_metadata: dict[str, Any] | None = None


class ReviewerProvider(Protocol):
    provider: str

    def review(
        self,
        *,
        requested_model: str,
        rendered_prompt: str,
        response_schema: Mapping[str, Any],
        model_settings: Mapping[str, Any],
    ) -> ProviderResult:
        """Return the provider response without performing local schema validation."""


def _sanitise_schema_node(
    schema: Mapping[str, Any],
    *,
    unsupported_keywords: frozenset[str],
    root_omissions: frozenset[str],
    translate_string_const: bool,
    is_root: bool = False,
) -> dict[str, Any]:
    """Return a provider transport copy without mutating the canonical schema."""

    sanitised: dict[str, Any] = {}
    for key, value in schema.items():
        if (is_root and key in root_omissions) or key in unsupported_keywords:
            continue
        if key == "const" and translate_string_const:
            if isinstance(value, str):
                sanitised["enum"] = [value]
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, Mapping):
            sanitised[key] = {
                str(name): _sanitise_schema_node(
                    child,
                    unsupported_keywords=unsupported_keywords,
                    root_omissions=root_omissions,
                    translate_string_const=translate_string_const,
                )
                if isinstance(child, Mapping)
                else deepcopy(child)
                for name, child in value.items()
            }
            continue
        if key in _SCHEMA_SINGLE_KEYWORDS and isinstance(value, Mapping):
            sanitised[key] = _sanitise_schema_node(
                value,
                unsupported_keywords=unsupported_keywords,
                root_omissions=root_omissions,
                translate_string_const=translate_string_const,
            )
            continue
        if key in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
            sanitised[key] = [
                _sanitise_schema_node(
                    child,
                    unsupported_keywords=unsupported_keywords,
                    root_omissions=root_omissions,
                    translate_string_const=translate_string_const,
                )
                if isinstance(child, Mapping)
                else deepcopy(child)
                for child in value
            ]
            continue
        sanitised[key] = deepcopy(value)
    return sanitised


def anthropic_transport_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Build the documented Anthropic structured-output transport subset."""

    return _sanitise_schema_node(
        schema,
        unsupported_keywords=ANTHROPIC_UNSUPPORTED_SCHEMA_KEYWORDS,
        root_omissions=frozenset({"$schema", "$id", "title"}),
        translate_string_const=False,
        is_root=True,
    )


def gemini_transport_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Build the documented Gemini Interactions structured-output subset."""

    return _sanitise_schema_node(
        schema,
        unsupported_keywords=GEMINI_UNSUPPORTED_SCHEMA_KEYWORDS,
        root_omissions=frozenset({"$schema"}),
        translate_string_const=True,
        is_root=True,
    )


def provider_transport_schema(
    provider: str, schema: Mapping[str, Any]
) -> dict[str, Any]:
    if provider == "anthropic":
        return anthropic_transport_schema(schema)
    if provider == "gemini":
        return gemini_transport_schema(schema)
    raise ProviderAdapterError(f"No transport-schema policy for provider {provider!r}")


def serialise_provider_object(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): serialise_provider_object(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialise_provider_object(item) for item in value]
    if hasattr(value, "model_dump"):
        return serialise_provider_object(value.model_dump(mode="json"))
    if hasattr(value, "to_json_dict"):
        return serialise_provider_object(value.to_json_dict())
    if is_dataclass(value):
        return serialise_provider_object(asdict(value))
    public = {
        key: serialise_provider_object(item)
        for key, item in vars(value).items()
        if not key.startswith("_")
    } if hasattr(value, "__dict__") else {}
    return public or repr(value)


def serialise_provider_exception(exc: Exception) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "details": serialise_provider_object(exc),
    }
    for name in ("body", "response", "code", "status_code", "request_id"):
        value = getattr(exc, name, None)
        if value is not None:
            raw[name] = serialise_provider_object(value)
    return raw


def _mapping_or_attribute(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _find_gemini_block_signal(value: Any, path: str = "$") -> tuple[str, str] | None:
    signal_keys = {
        "blockReason",
        "block_reason",
        "code",
        "finishReason",
        "finish_reason",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in signal_keys and isinstance(child, str):
                if child.lower() in GEMINI_BLOCK_CODES:
                    return f"{path}.{key}", child
            found = _find_gemini_block_signal(child, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _find_gemini_block_signal(child, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _first_message(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("message", "finishMessage", "finish_message"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for child in value.values():
            candidate = _first_message(child)
            if candidate is not None:
                return candidate
    elif isinstance(value, (list, tuple)):
        for child in value:
            candidate = _first_message(child)
            if candidate is not None:
                return candidate
    return None


def _first_string_attribute(value: Any, names: tuple[str, ...]) -> str | None:
    for name in names:
        candidate = getattr(value, name, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


class AnthropicReviewerAdapter:
    provider = "anthropic"

    def __init__(self, client: Any | None = None, *, api_key: str | None = None):
        if client is None:
            if api_key is None:
                api_key = os.environ.get(PROVIDER_ENVIRONMENT_VARIABLES[self.provider])
            if not api_key:
                raise ProviderAdapterError(
                    f"{PROVIDER_ENVIRONMENT_VARIABLES[self.provider]} is required for --execute"
                )
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover - deployment-only branch
                raise ProviderAdapterError(
                    "Install requirements-airisk-semantic-review.txt for Anthropic execution"
                ) from exc
            client = Anthropic(api_key=api_key)
        self.client = client

    def review(
        self,
        *,
        requested_model: str,
        rendered_prompt: str,
        response_schema: Mapping[str, Any],
        model_settings: Mapping[str, Any],
    ) -> ProviderResult:
        request = {
            "model": requested_model,
            "max_tokens": int(model_settings["max_output_tokens"]),
            "messages": [{"role": "user", "content": rendered_prompt}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": anthropic_transport_schema(response_schema),
                }
            },
        }
        if model_settings.get("temperature") is not None:
            request["temperature"] = float(model_settings["temperature"])
        response = self.client.messages.create(
            **request,
        )
        raw_response = serialise_provider_object(response)
        text_blocks = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                text_blocks.append(text)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                text_blocks.append(block["text"])
        raw_text = "".join(text_blocks)
        stop_reason = _mapping_or_attribute(response, "stop_reason")
        if stop_reason == "refusal":
            stop_details = serialise_provider_object(
                _mapping_or_attribute(response, "stop_details")
            )
            details = stop_details if isinstance(stop_details, Mapping) else None
            return ProviderResult(
                raw_response=raw_response,
                raw_response_text=raw_text or None,
                resolved_reported_model=_first_string_attribute(
                    response, ("model", "model_version", "model_name")
                ),
                provider_request_id=_first_string_attribute(
                    response, ("id", "request_id")
                ),
                terminal_status="refused",
                provider_block_metadata={
                    "provider": "anthropic",
                    "provider_signal": "stop_reason",
                    "native_code": "refusal",
                    "category": details.get("category") if details else None,
                    "explanation": details.get("explanation") if details else None,
                    "details": stop_details,
                },
            )
        if not raw_text:
            raise ProviderAdapterError("Anthropic returned no text response block")
        return ProviderResult(
            raw_response=raw_response,
            raw_response_text=raw_text,
            resolved_reported_model=_first_string_attribute(
                response, ("model", "model_version", "model_name")
            ),
            provider_request_id=_first_string_attribute(response, ("id", "request_id")),
        )


class GeminiReviewerAdapter:
    provider = "gemini"

    def __init__(self, client: Any | None = None, *, api_key: str | None = None):
        if client is None:
            if api_key is None:
                api_key = os.environ.get(PROVIDER_ENVIRONMENT_VARIABLES[self.provider])
            if not api_key:
                raise ProviderAdapterError(
                    f"{PROVIDER_ENVIRONMENT_VARIABLES[self.provider]} is required for --execute"
                )
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - deployment-only branch
                raise ProviderAdapterError(
                    "Install requirements-airisk-semantic-review.txt for Gemini execution"
                ) from exc
            client = genai.Client(api_key=api_key)
        self.client = client

    def review(
        self,
        *,
        requested_model: str,
        rendered_prompt: str,
        response_schema: Mapping[str, Any],
        model_settings: Mapping[str, Any],
    ) -> ProviderResult:
        generation_config = {
            "max_output_tokens": int(model_settings["max_output_tokens"]),
        }
        if model_settings.get("temperature") is not None:
            generation_config["temperature"] = float(model_settings["temperature"])
        try:
            response = self.client.interactions.create(
                model=requested_model,
                input=rendered_prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": gemini_transport_schema(response_schema),
                },
                generation_config=generation_config,
                store=False,
            )
        except Exception as exc:
            raw_error = serialise_provider_exception(exc)
            block_signal = _find_gemini_block_signal(raw_error)
            if block_signal is None:
                raise
            signal_path, native_code = block_signal
            return ProviderResult(
                raw_response=raw_error,
                raw_response_text=None,
                resolved_reported_model=None,
                provider_request_id=_first_string_attribute(
                    exc, ("request_id", "id", "interaction_id")
                ),
                terminal_status="blocked",
                provider_block_metadata={
                    "provider": "gemini",
                    "provider_signal": signal_path,
                    "native_code": native_code,
                    "category": native_code,
                    "explanation": _first_message(raw_error),
                    "details": raw_error,
                },
            )
        raw_response = serialise_provider_object(response)
        block_signal = _find_gemini_block_signal(raw_response)
        if block_signal is not None:
            signal_path, native_code = block_signal
            return ProviderResult(
                raw_response=raw_response,
                raw_response_text=None,
                resolved_reported_model=_first_string_attribute(
                    response, ("model", "model_version", "model_name")
                ),
                provider_request_id=_first_string_attribute(
                    response, ("id", "interaction_id", "request_id")
                ),
                terminal_status="blocked",
                provider_block_metadata={
                    "provider": "gemini",
                    "provider_signal": signal_path,
                    "native_code": native_code,
                    "category": native_code,
                    "explanation": _first_message(raw_response),
                    "details": raw_response,
                },
            )
        raw_text = getattr(response, "output_text", None)
        if not isinstance(raw_text, str) or not raw_text:
            raw_text = getattr(response, "text", None)
        if not isinstance(raw_text, str) or not raw_text:
            raise ProviderAdapterError("Gemini returned no output_text")
        return ProviderResult(
            raw_response=raw_response,
            raw_response_text=raw_text,
            resolved_reported_model=_first_string_attribute(
                response, ("model", "model_version", "model_name")
            ),
            provider_request_id=_first_string_attribute(
                response, ("id", "interaction_id", "request_id")
            ),
        )


def create_provider_adapter(provider: str) -> ReviewerProvider:
    if provider == "anthropic":
        return AnthropicReviewerAdapter()
    if provider == "gemini":
        return GeminiReviewerAdapter()
    raise ProviderAdapterError(
        f"Unsupported provider {provider!r}; choose one of {SUPPORTED_PROVIDERS}"
    )


def parse_response_json(raw_text: str) -> dict[str, Any]:
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProviderAdapterError(f"Provider response was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProviderAdapterError("Provider response JSON must be an object")
    return value
