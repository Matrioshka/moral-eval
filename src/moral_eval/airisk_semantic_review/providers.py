"""Thin provider adapters for future independent semantic-review execution."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


SUPPORTED_PROVIDERS = ("anthropic", "gemini", "openrouter")
PROVIDER_ENVIRONMENT_VARIABLES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY2",
}
PROVIDER_CREDENTIAL_ENVIRONMENT_VARIABLES = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY2", "OPENROUTER_API_KEY"),
}
REPO_ROOT = Path(__file__).resolve().parents[3]
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_RESPONSE_SCHEMA_NAME = "airisk_jmcup_group_semantic_review_v1"

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


class ProviderResponseError(ProviderAdapterError):
    """A provider returned an auditable error response that was not a block."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: Any,
        raw_response_text: str | None = None,
        resolved_reported_model: str | None = None,
        provider_request_id: str | None = None,
        provider_generation_id: str | None = None,
        actual_routed_provider: str | None = None,
        provider_usage: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.raw_response = raw_response
        self.raw_response_text = raw_response_text
        self.resolved_reported_model = resolved_reported_model
        self.provider_request_id = provider_request_id
        self.provider_generation_id = provider_generation_id
        self.actual_routed_provider = actual_routed_provider
        self.provider_usage = provider_usage


@dataclass(frozen=True)
class ProviderResult:
    raw_response: Any
    raw_response_text: str | None
    resolved_reported_model: str | None
    provider_request_id: str | None
    terminal_status: str | None = None
    provider_block_metadata: dict[str, Any] | None = None
    provider_generation_id: str | None = None
    actual_routed_provider: str | None = None
    provider_usage: dict[str, Any] | None = None


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


def openrouter_transport_schema(
    requested_model: str, schema: Mapping[str, Any]
) -> dict[str, Any]:
    if requested_model.startswith("anthropic/"):
        return anthropic_transport_schema(schema)
    if requested_model.startswith("google/"):
        return gemini_transport_schema(schema)
    raise ProviderAdapterError(
        "OpenRouter semantic review supports anthropic/* and google/* model slugs"
    )


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


def _redact_secret(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, Mapping):
        return {
            _redact_secret(str(key), secret): _redact_secret(child, secret)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_secret(child, secret) for child in value]
    return value


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


def _openrouter_credentials(
    *,
    explicit_api_key: str | None,
    env_path: Path,
) -> str | None:
    if explicit_api_key:
        return explicit_api_key
    try:
        from dotenv import dotenv_values
    except ImportError as exc:  # pragma: no cover - deployment-only branch
        raise ProviderAdapterError(
            "Install requirements-airisk-semantic-review.txt for OpenRouter execution"
        ) from exc
    dotenv = dotenv_values(env_path) if env_path.is_file() else {}
    for name in PROVIDER_CREDENTIAL_ENVIRONMENT_VARIABLES["openrouter"]:
        value = os.environ.get(name)
        if isinstance(value, str) and value:
            return value
        file_value = dotenv.get(name)
        if isinstance(file_value, str) and file_value:
            return file_value
    return None


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    for key, value in headers.items():
        if str(key).casefold() == name.casefold() and isinstance(value, str) and value:
            return value
    return None


def _serialise_openrouter_response(response: Any) -> tuple[dict[str, Any], Any, str]:
    response_text = getattr(response, "text", "")
    if not isinstance(response_text, str):
        response_text = str(response_text)
    try:
        body = response.json()
    except (TypeError, ValueError):
        body = response_text
    headers = getattr(response, "headers", {})
    safe_headers = (
        {str(key): str(value) for key, value in headers.items()}
        if isinstance(headers, Mapping)
        else {}
    )
    status_code = getattr(response, "status_code", None)
    return (
        {
            "http_status": status_code,
            "headers": safe_headers,
            "body": serialise_provider_object(body),
        },
        body,
        response_text,
    )


def _openrouter_actual_provider(body: Any) -> str | None:
    if not isinstance(body, Mapping):
        return None
    direct = body.get("provider")
    if isinstance(direct, str) and direct:
        return direct
    metadata = body.get("openrouter_metadata")
    endpoints = metadata.get("endpoints") if isinstance(metadata, Mapping) else None
    available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
    if isinstance(available, list):
        for endpoint in available:
            if not isinstance(endpoint, Mapping) or endpoint.get("selected") is not True:
                continue
            provider = endpoint.get("provider")
            if isinstance(provider, str) and provider:
                return provider
    return None


def _openrouter_terminal_signal(body: Any) -> tuple[str, str, str] | None:
    if not isinstance(body, Mapping):
        return None

    error_locations: list[tuple[str, Any]] = [("$.error", body.get("error"))]
    choices = body.get("choices")
    if isinstance(choices, list):
        for index, choice in enumerate(choices):
            if not isinstance(choice, Mapping):
                continue
            error_locations.append((f"$.choices[{index}].error", choice.get("error")))
            message = choice.get("message")
            refusal = message.get("refusal") if isinstance(message, Mapping) else None
            if isinstance(refusal, str) and refusal:
                return "refused", f"$.choices[{index}].message.refusal", "refusal"
            for key in ("finish_reason", "native_finish_reason"):
                native = choice.get(key)
                if not isinstance(native, str):
                    continue
                normalised = native.casefold()
                if normalised == "refusal":
                    return "refused", f"$.choices[{index}].{key}", native
                if normalised in {
                    "content_filter",
                    "content_policy_violation",
                    "safety",
                    "recitation",
                    "prohibited_content",
                    "blocklist",
                    "spii",
                    "escalation",
                }:
                    return "blocked", f"$.choices[{index}].{key}", native

    for path, error in error_locations:
        metadata = error.get("metadata") if isinstance(error, Mapping) else None
        error_type = metadata.get("error_type") if isinstance(metadata, Mapping) else None
        if not isinstance(error_type, str):
            continue
        if error_type == "refusal":
            return "refused", f"{path}.metadata.error_type", error_type
        if error_type == "content_policy_violation":
            return "blocked", f"{path}.metadata.error_type", error_type

    metadata = body.get("openrouter_metadata")
    pipeline = metadata.get("pipeline") if isinstance(metadata, Mapping) else None
    if isinstance(pipeline, list):
        for index, stage in enumerate(pipeline):
            data = stage.get("data") if isinstance(stage, Mapping) else None
            action = data.get("action") if isinstance(data, Mapping) else None
            if isinstance(action, str) and action.casefold() == "blocked":
                return "blocked", f"$.openrouter_metadata.pipeline[{index}].data.action", action
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


class OpenRouterReviewerAdapter:
    provider = "openrouter"

    def __init__(
        self,
        session: Any | None = None,
        *,
        api_key: str | None = None,
        env_path: Path | None = None,
        timeout_seconds: float = 180.0,
    ):
        selected_env_path = env_path or (REPO_ROOT / ".env")
        selected_key = _openrouter_credentials(
            explicit_api_key=api_key,
            env_path=selected_env_path,
        )
        if not selected_key:
            raise ProviderAdapterError(
                "OPENROUTER_API_KEY2 or OPENROUTER_API_KEY is required for --execute"
            )
        if session is None:
            try:
                import requests
            except ImportError as exc:  # pragma: no cover - deployment-only branch
                raise ProviderAdapterError(
                    "Install requirements-airisk-semantic-review.txt for OpenRouter execution"
                ) from exc
            session = requests.Session()
        self.session = session
        self._api_key = selected_key
        self.timeout_seconds = timeout_seconds

    def review(
        self,
        *,
        requested_model: str,
        rendered_prompt: str,
        response_schema: Mapping[str, Any],
        model_settings: Mapping[str, Any],
    ) -> ProviderResult:
        transport_schema = openrouter_transport_schema(
            requested_model, response_schema
        )
        request_body: dict[str, Any] = {
            "model": requested_model,
            "messages": [{"role": "user", "content": rendered_prompt}],
            "max_tokens": int(model_settings["max_output_tokens"]),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": OPENROUTER_RESPONSE_SCHEMA_NAME,
                    "strict": True,
                    "schema": transport_schema,
                },
            },
            "provider": {"require_parameters": True},
            "stream": False,
        }
        if model_settings.get("temperature") is not None:
            request_body["temperature"] = float(model_settings["temperature"])
        try:
            response = self.session.post(
                OPENROUTER_API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-OpenRouter-Metadata": "enabled",
                },
                json=request_body,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            safe_exception_message = str(exc).replace(self._api_key, "[REDACTED]")
            raw_error: dict[str, Any] = {
                "exception_type": type(exc).__name__,
                "message": safe_exception_message,
            }
            error_response = getattr(exc, "response", None)
            if error_response is not None:
                raw_response, _, response_text = _serialise_openrouter_response(
                    error_response
                )
                raw_error["response"] = _redact_secret(
                    raw_response, self._api_key
                )
                response_text = response_text.replace(
                    self._api_key, "[REDACTED]"
                )
            else:
                response_text = None
            raise ProviderResponseError(
                f"OpenRouter request failed: {type(exc).__name__}: {safe_exception_message}",
                raw_response=raw_error,
                raw_response_text=response_text,
            ) from exc

        raw_response, body, response_text = _serialise_openrouter_response(response)
        raw_response = _redact_secret(raw_response, self._api_key)
        body = _redact_secret(body, self._api_key)
        response_text = response_text.replace(self._api_key, "[REDACTED]")
        body_mapping = body if isinstance(body, Mapping) else {}
        resolved_model = body_mapping.get("model")
        if not isinstance(resolved_model, str) or not resolved_model:
            resolved_model = None
        request_id = body_mapping.get("id")
        if not isinstance(request_id, str) or not request_id:
            request_id = _response_header(response, "X-Request-Id")
        generation_id = _response_header(response, "X-Generation-Id")
        actual_provider = _openrouter_actual_provider(body)
        usage_value = body_mapping.get("usage")
        usage = (
            serialise_provider_object(usage_value)
            if isinstance(usage_value, Mapping)
            else None
        )

        choices = body_mapping.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        raw_text = content if isinstance(content, str) else None
        terminal_signal = _openrouter_terminal_signal(body)
        if terminal_signal is not None:
            terminal_status, signal_path, native_code = terminal_signal
            return ProviderResult(
                raw_response=raw_response,
                raw_response_text=raw_text,
                resolved_reported_model=resolved_model,
                provider_request_id=request_id,
                terminal_status=terminal_status,
                provider_block_metadata={
                    "provider": "openrouter",
                    "provider_signal": signal_path,
                    "native_code": native_code,
                    "category": native_code,
                    "explanation": _first_message(body),
                    "details": serialise_provider_object(body),
                },
                provider_generation_id=generation_id,
                actual_routed_provider=actual_provider,
                provider_usage=usage,
            )

        status_code = getattr(response, "status_code", None)
        error = body_mapping.get("error")
        choice_error = choice.get("error") if isinstance(choice, Mapping) else None
        if (
            not isinstance(status_code, int)
            or not 200 <= status_code < 300
            or error is not None
            or choice_error is not None
        ):
            message_text = _first_message(body) or f"HTTP status {status_code}"
            raise ProviderResponseError(
                f"OpenRouter returned an error: {message_text}",
                raw_response=raw_response,
                raw_response_text=response_text,
                resolved_reported_model=resolved_model,
                provider_request_id=request_id,
                provider_generation_id=generation_id,
                actual_routed_provider=actual_provider,
                provider_usage=usage,
            )
        if raw_text is None or not raw_text:
            raise ProviderResponseError(
                "OpenRouter returned no assistant message content",
                raw_response=raw_response,
                raw_response_text=response_text,
                resolved_reported_model=resolved_model,
                provider_request_id=request_id,
                provider_generation_id=generation_id,
                actual_routed_provider=actual_provider,
                provider_usage=usage,
            )
        return ProviderResult(
            raw_response=raw_response,
            raw_response_text=raw_text,
            resolved_reported_model=resolved_model,
            provider_request_id=request_id,
            provider_generation_id=generation_id,
            actual_routed_provider=actual_provider,
            provider_usage=usage,
        )


def create_provider_adapter(provider: str) -> ReviewerProvider:
    if provider == "anthropic":
        return AnthropicReviewerAdapter()
    if provider == "gemini":
        return GeminiReviewerAdapter()
    if provider == "openrouter":
        return OpenRouterReviewerAdapter()
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
