"""Passive diagnostics for behavioural eval responses.

This module only reads already-produced Inspect/provider metadata. It must not
modify prompts, messages, solver state, or model-call parameters.
"""

from __future__ import annotations

import dataclasses
import json
from copy import deepcopy
from typing import Any


DIAGNOSTIC_VERSION = "v1"
USAGE_ONLY_MODE = "usage_only"
PROVIDER_SUMMARY_MODE = "provider_summary"
DEFAULT_CAPTURE_AFTER_TURNS = ("baseline", "pressure_1", "pressure_2", "pressure_3")


@dataclasses.dataclass(frozen=True)
class UsageMetadataConfig:
    enabled: bool = True
    store_raw_provider_usage: bool = True
    store_input_tokens: bool = True
    store_output_tokens: bool = True
    store_total_tokens: bool = True
    store_reasoning_tokens_if_available: bool = True
    store_thinking_tokens_if_available: bool = True


@dataclasses.dataclass(frozen=True)
class ProviderReasoningSummaryConfig:
    extract_if_present: bool = False
    request_from_provider: bool = False
    include_in_dialogue_context: bool = False
    headline_eligible: bool = False
    capture_after_turns: tuple[str, ...] = DEFAULT_CAPTURE_AFTER_TURNS


@dataclasses.dataclass(frozen=True)
class DiagnosticsConfig:
    usage_metadata: UsageMetadataConfig = dataclasses.field(default_factory=UsageMetadataConfig)
    provider_reasoning_summary: ProviderReasoningSummaryConfig = dataclasses.field(
        default_factory=ProviderReasoningSummaryConfig
    )


def normalise_diagnostics_config(raw: Any | None) -> dict[str, Any]:
    """Return a validated diagnostics config with v1 defaults filled in."""

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("diagnostics must be a mapping when provided")
    _validate_no_dialogue_feedback(raw)

    usage_raw = raw.get("usage_metadata") or {}
    provider_raw = raw.get("provider_reasoning_summary") or {}
    if not isinstance(usage_raw, dict):
        raise ValueError("diagnostics.usage_metadata must be a mapping")
    if not isinstance(provider_raw, dict):
        raise ValueError("diagnostics.provider_reasoning_summary must be a mapping")

    usage = UsageMetadataConfig(
        enabled=_bool_config(usage_raw, "enabled", True),
        store_raw_provider_usage=_bool_config(usage_raw, "store_raw_provider_usage", True),
        store_input_tokens=_bool_config(usage_raw, "store_input_tokens", True),
        store_output_tokens=_bool_config(usage_raw, "store_output_tokens", True),
        store_total_tokens=_bool_config(usage_raw, "store_total_tokens", True),
        store_reasoning_tokens_if_available=_bool_config(
            usage_raw, "store_reasoning_tokens_if_available", True
        ),
        store_thinking_tokens_if_available=_bool_config(
            usage_raw, "store_thinking_tokens_if_available", True
        ),
    )

    capture_after_turns = provider_raw.get("capture_after_turns", DEFAULT_CAPTURE_AFTER_TURNS)
    if capture_after_turns is None:
        capture_after_turns = DEFAULT_CAPTURE_AFTER_TURNS
    if not isinstance(capture_after_turns, (list, tuple)):
        raise ValueError("diagnostics.provider_reasoning_summary.capture_after_turns must be a list")
    capture_after_turns = tuple(str(item) for item in capture_after_turns)

    provider = ProviderReasoningSummaryConfig(
        extract_if_present=_bool_config(provider_raw, "extract_if_present", False),
        request_from_provider=_bool_config(provider_raw, "request_from_provider", False),
        include_in_dialogue_context=_bool_config(provider_raw, "include_in_dialogue_context", False),
        headline_eligible=_bool_config(provider_raw, "headline_eligible", False),
        capture_after_turns=capture_after_turns,
    )

    config = DiagnosticsConfig(usage_metadata=usage, provider_reasoning_summary=provider)
    result = _dataclass_to_dict(config)
    validate_diagnostics_config(result)
    return result


def validate_diagnostics_config(config: dict[str, Any]) -> None:
    provider = config.get("provider_reasoning_summary") or {}
    if provider.get("request_from_provider"):
        raise ValueError(
            "diagnostics.provider_reasoning_summary.request_from_provider is not implemented in v1"
        )
    if provider.get("headline_eligible"):
        raise ValueError("provider reasoning summaries cannot be headline-eligible")
    if provider.get("include_in_dialogue_context"):
        raise ValueError("provider reasoning summaries cannot be included in dialogue context")

    if _enabled_nested(config, ("prompted_rationale", "enabled")):
        raise ValueError("prompted rationale diagnostics are not supported for behavioural dialogue")
    for key in ("feed_into_dialogue", "feed_back_into_dialogue", "visible_to_model_next_turn"):
        if bool(config.get(key)):
            raise ValueError("diagnostics must not be fed back into later dialogue turns")


def _validate_no_dialogue_feedback(config: dict[str, Any]) -> None:
    if _enabled_nested(config, ("prompted_rationale", "enabled")):
        raise ValueError("prompted rationale diagnostics are not supported for behavioural dialogue")
    for key in ("feed_into_dialogue", "feed_back_into_dialogue", "visible_to_model_next_turn"):
        if bool(config.get(key)):
            raise ValueError("diagnostics must not be fed back into later dialogue turns")


def extract_response_diagnostics(
    *,
    usage: Any | None = None,
    raw_sample: Any | None = None,
    diagnostics_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract zero or more passive diagnostic records from an Inspect sample.

    The returned records do not include response_id; callers attach it after the
    Inspect sample has been linked to public.response.
    """

    config = normalise_diagnostics_config(diagnostics_config)
    raw_sample_json = to_jsonable(raw_sample)
    usage_json = to_jsonable(usage)
    if not usage_json:
        usage_json = _find_first_mapping(
            raw_sample_json,
            (
                ("output", "usage"),
                ("usage",),
                ("raw_response", "usage"),
                ("provider_metadata", "usage"),
                ("output", "provider_metadata", "usage"),
            ),
        )
    usage_json = usage_json if isinstance(usage_json, dict) else {}

    records: list[dict[str, Any]] = []
    usage_config = config["usage_metadata"]
    if usage_config["enabled"]:
        records.append(_usage_diagnostic_record(usage_json, raw_sample_json, usage_config))

    provider_config = config["provider_reasoning_summary"]
    if provider_config["extract_if_present"]:
        summary = _extract_reasoning_summary(raw_sample_json)
        if summary["raw"] is not None or summary["text"] is not None:
            records.append(_provider_summary_record(raw_sample_json, summary, provider_config))

    return records


def response_diagnostic_records_from_inspect_sample(
    sample_row: dict[str, Any],
    diagnostics_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build diagnostic records for a linked public.response sample row."""

    response_id = sample_row.get("response_id")
    if response_id is None:
        return []

    records = extract_response_diagnostics(
        usage=sample_row.get("usage"),
        raw_sample=sample_row.get("raw_sample"),
        diagnostics_config=diagnostics_config,
    )
    for record in records:
        record["response_id"] = int(response_id)
    return records


def discover_usage_locations(raw_sample: Any) -> list[dict[str, Any]]:
    """Find candidate usage locations without deciding all are call-level."""

    sample = to_jsonable(raw_sample)
    locations: list[dict[str, Any]] = []

    for path in (
        ("output", "usage"),
        ("usage",),
        ("model_usage",),
        ("role_usage",),
        ("raw_response", "usage"),
        ("provider_metadata", "usage"),
        ("output", "provider_metadata", "usage"),
    ):
        usage = _get_nested(sample, path)
        if _looks_like_usage_payload(usage) or _looks_like_usage_collection(usage):
            locations.append(_usage_location(path, usage, source_scope="sample", confirmed_call_level=False))

    for container_path, scope in (
        (("events",), "event"),
        (("events_data",), "event"),
        (("transcript", "events"), "transcript_event"),
        (("timelines",), "timeline"),
        (("spans",), "span"),
    ):
        container = _get_nested(sample, container_path)
        locations.extend(_discover_sequence_usage(container, container_path, scope))

    messages = _get_nested(sample, ("messages",))
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if _looks_like_usage_payload(usage):
                locations.append(
                    _usage_location(
                        ("messages", str(index), "usage"),
                        usage,
                        source_scope="message",
                        source_event_index=index,
                        event_path=("messages", str(index)),
                        confirmed_call_level=_is_confirmed_model_message(message),
                    )
                )

    return locations


def extract_model_call_diagnostics_from_raw_sample(
    sample_row: dict[str, Any],
    diagnostics_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract confirmed per-model-call usage diagnostics from a sample row.

    Aggregate sample-level usage intentionally returns no rows.
    """

    config = normalise_diagnostics_config(diagnostics_config)
    usage_config = config["usage_metadata"]
    if not usage_config["enabled"]:
        return []

    raw_sample = to_jsonable(sample_row.get("raw_sample"))
    locations = [
        location
        for location in discover_usage_locations(raw_sample)
        if location.get("confirmed_call_level")
    ]

    exact_link = _exact_response_link(raw_sample, sample_row)
    records: list[dict[str, Any]] = []
    for model_call_index, location in enumerate(locations):
        usage = location["usage"]
        event = _get_nested(raw_sample, tuple(location["event_path"])) if location.get("event_path") else None
        event_text = _assistant_output_text(event)
        link = (
            exact_link
            if exact_link
            and event_text
            and _normalise_text(event_text) == _normalise_text(sample_row.get("response_text"))
            and model_call_index == exact_link["model_call_index"]
            else None
        )
        turn_index = model_call_index
        records.append(
            {
                "experiment_pipeline_run_id": int(sample_row["experiment_pipeline_run_id"]),
                "inspect_log_sample_id": int(sample_row["inspect_log_sample_id"]),
                "response_id": link.get("response_id") if link else None,
                "eval_case_id": link.get("eval_case_id") if link else None,
                "case_turn_id": link.get("case_turn_id") if link else None,
                "diagnostic_version": DIAGNOSTIC_VERSION,
                "diagnostic_mode": USAGE_ONLY_MODE,
                "source_scope": location["source_scope"],
                "source_event_index": int(location.get("source_event_index", -1)),
                "model_call_index": model_call_index,
                "turn_index": turn_index,
                "turn_label": _turn_label(turn_index),
                "link_confidence": "exact" if link else "unverified",
                "link_method": "assistant_text_and_turn_order" if link else "raw_event_only",
                "input_tokens": _token_if_enabled(
                    usage_config,
                    "store_input_tokens",
                    usage,
                    (("input_tokens",), ("prompt_tokens",), ("input_token_count",)),
                ),
                "output_tokens": _token_if_enabled(
                    usage_config,
                    "store_output_tokens",
                    usage,
                    (("output_tokens",), ("completion_tokens",), ("output_token_count",)),
                ),
                "total_tokens": _token_if_enabled(
                    usage_config,
                    "store_total_tokens",
                    usage,
                    (("total_tokens",),),
                ),
                "reasoning_tokens": _token_if_enabled(
                    usage_config,
                    "store_reasoning_tokens_if_available",
                    usage,
                    (
                        ("reasoning_tokens",),
                        ("completion_tokens_details", "reasoning_tokens"),
                        ("output_tokens_details", "reasoning_tokens"),
                    ),
                ),
                "thinking_tokens": _token_if_enabled(
                    usage_config,
                    "store_thinking_tokens_if_available",
                    usage,
                    (("thinking_tokens",), ("output_tokens_details", "thinking_tokens")),
                ),
                "cached_input_tokens": _coerce_int(
                    _get_nested_any(
                        usage,
                        (
                            ("cached_input_tokens",),
                            ("input_tokens_cache_read",),
                            ("prompt_tokens_details", "cached_tokens"),
                            ("input_tokens_details", "cached_tokens"),
                        ),
                    )
                ),
                "raw_usage_json": deepcopy(usage) if usage_config["store_raw_provider_usage"] else None,
                "raw_event_json": event,
                "raw_provider_metadata_json": _extract_provider_metadata(event),
                "headline_eligible": False,
            }
        )
    return records


def to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, depth + 1) for v in value]
    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value), depth + 1)
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(), depth + 1)
    if hasattr(value, "dict"):
        try:
            return to_jsonable(value.dict(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value), depth + 1)
    return str(value)


def _usage_diagnostic_record(
    usage: dict[str, Any],
    raw_sample: Any,
    usage_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "diagnostic_mode": USAGE_ONLY_MODE,
        "headline_eligible": True,
        "visible_to_model_next_turn": False,
        "reasoning_requested": False,
        "reasoning_summary_requested": False,
        "reasoning_effort": _get_nested_any(
            raw_sample,
            (
                ("output", "reasoning_effort"),
                ("provider_metadata", "reasoning_effort"),
                ("output", "provider_metadata", "reasoning_effort"),
            ),
        ),
        "input_tokens": _token_if_enabled(
            usage_config,
            "store_input_tokens",
            usage,
            (("input_tokens",), ("prompt_tokens",), ("input_token_count",)),
        ),
        "output_tokens": _token_if_enabled(
            usage_config,
            "store_output_tokens",
            usage,
            (("output_tokens",), ("completion_tokens",), ("output_token_count",)),
        ),
        "total_tokens": _token_if_enabled(
            usage_config,
            "store_total_tokens",
            usage,
            (("total_tokens",),),
        ),
        "reasoning_tokens": _token_if_enabled(
            usage_config,
            "store_reasoning_tokens_if_available",
            usage,
            (
                ("reasoning_tokens",),
                ("completion_tokens_details", "reasoning_tokens"),
                ("output_tokens_details", "reasoning_tokens"),
            ),
        ),
        "thinking_tokens": _token_if_enabled(
            usage_config,
            "store_thinking_tokens_if_available",
            usage,
            (("thinking_tokens",), ("output_tokens_details", "thinking_tokens")),
        ),
        "cached_input_tokens": _coerce_int(
            _get_nested_any(
                usage,
                (
                    ("cached_input_tokens",),
                    ("prompt_tokens_details", "cached_tokens"),
                    ("input_tokens_details", "cached_tokens"),
                ),
            )
        ),
        "reasoning_summary_text": None,
        "raw_usage_json": deepcopy(usage) if usage and usage_config["store_raw_provider_usage"] else None,
        "raw_reasoning_summary_json": None,
        "raw_provider_metadata_json": _extract_provider_metadata(raw_sample),
    }


def _provider_summary_record(
    raw_sample: Any,
    summary: dict[str, Any],
    provider_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "diagnostic_mode": PROVIDER_SUMMARY_MODE,
        "headline_eligible": False,
        "visible_to_model_next_turn": bool(provider_config["include_in_dialogue_context"]),
        "reasoning_requested": False,
        "reasoning_summary_requested": bool(provider_config["request_from_provider"]),
        "reasoning_effort": _get_nested_any(
            raw_sample,
            (
                ("output", "reasoning_effort"),
                ("provider_metadata", "reasoning_effort"),
                ("output", "provider_metadata", "reasoning_effort"),
            ),
        ),
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
        "thinking_tokens": None,
        "cached_input_tokens": None,
        "reasoning_summary_text": summary["text"],
        "raw_usage_json": None,
        "raw_reasoning_summary_json": summary["raw"],
        "raw_provider_metadata_json": _extract_provider_metadata(raw_sample),
    }


def _extract_reasoning_summary(raw_sample: Any) -> dict[str, Any]:
    raw = _get_nested_any(
        raw_sample,
        (
            ("output", "reasoning_summary"),
            ("output", "provider_metadata", "reasoning_summary"),
            ("raw_response", "reasoning_summary"),
            ("provider_metadata", "reasoning_summary"),
            ("output", "metadata", "reasoning_summary"),
        ),
    )
    if raw is None:
        return {"text": None, "raw": None}
    if isinstance(raw, str):
        return {"text": raw, "raw": raw}
    return {"text": json.dumps(raw, ensure_ascii=False, sort_keys=True), "raw": raw}


def _extract_provider_metadata(raw_sample: Any) -> Any | None:
    metadata = _get_nested_any(
        raw_sample,
        (
            ("provider_metadata",),
            ("output", "provider_metadata"),
            ("raw_response", "provider_metadata"),
            ("output", "metadata"),
        ),
    )
    return metadata if metadata not in ({}, [], "") else None


def _usage_location(
    path: tuple[str, ...],
    usage: Any,
    *,
    source_scope: str,
    source_event_index: int | None = None,
    event_path: tuple[str, ...] | None = None,
    confirmed_call_level: bool,
) -> dict[str, Any]:
    return {
        "path": ".".join(path),
        "path_parts": list(path),
        "source_scope": source_scope,
        "source_event_index": source_event_index,
        "event_path": list(event_path or ()),
        "confirmed_call_level": confirmed_call_level,
        "usage": to_jsonable(usage),
        "usage_keys": sorted(to_jsonable(usage).keys()) if isinstance(to_jsonable(usage), dict) else [],
    }


def _discover_sequence_usage(container: Any, container_path: tuple[str, ...], scope: str) -> list[dict[str, Any]]:
    if isinstance(container, dict):
        items = []
        for key, value in container.items():
            if isinstance(value, list):
                items.extend((str(key), index, item) for index, item in enumerate(value))
        return [
            location
            for key, index, item in items
            for location in _usage_locations_for_event(item, container_path + (key, str(index)), scope, index)
        ]
    if not isinstance(container, list):
        return []
    return [
        location
        for index, item in enumerate(container)
        for location in _usage_locations_for_event(item, container_path + (str(index),), scope, index)
    ]


def _usage_locations_for_event(
    event: Any,
    event_path: tuple[str, ...],
    scope: str,
    index: int,
) -> list[dict[str, Any]]:
    if not isinstance(event, dict):
        return []
    locations: list[dict[str, Any]] = []
    for relative_path in (
        ("output", "usage"),
        ("usage",),
        ("call", "usage"),
        ("metadata", "usage"),
        ("provider_metadata", "usage"),
        ("output", "provider_metadata", "usage"),
        ("output", "metadata", "usage"),
    ):
        usage = _get_nested(event, relative_path)
        if _looks_like_usage_payload(usage):
            confirmed = _is_confirmed_model_event(event) or relative_path in {
                ("call", "usage"),
                ("output", "usage"),
            }
            locations.append(
                _usage_location(
                    event_path + relative_path,
                    usage,
                    source_scope=scope,
                    source_event_index=index,
                    event_path=event_path,
                    confirmed_call_level=confirmed,
                )
            )
    return locations


def _looks_like_usage_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    token_keys = {
        "input_tokens",
        "prompt_tokens",
        "input_token_count",
        "output_tokens",
        "completion_tokens",
        "output_token_count",
        "total_tokens",
        "reasoning_tokens",
        "thinking_tokens",
        "cached_input_tokens",
        "input_tokens_cache_read",
        "completion_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
        "input_tokens_details",
    }
    return bool(token_keys.intersection(value.keys()))


def _looks_like_usage_collection(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_looks_like_usage_payload(item) for item in value.values())


def _is_confirmed_model_event(event: dict[str, Any]) -> bool:
    event_name = str(event.get("event") or event.get("type") or event.get("name") or "").lower()
    if "model" in event_name or "call" in event_name:
        return True
    return "model" in event and "output" in event


def _is_confirmed_model_message(message: dict[str, Any]) -> bool:
    role = str(message.get("role") or "").lower()
    marker = str(message.get("event") or message.get("type") or message.get("source") or "").lower()
    return role == "assistant" and ("model" in marker or "call" in marker or bool(message.get("model_call_id")))


def _assistant_output_text(event: Any) -> str | None:
    if not isinstance(event, dict):
        return None
    output = event.get("output")
    if isinstance(output, dict):
        for path in (("completion",), ("message", "content"), ("choices", "0", "message", "content")):
            value = _get_nested(output, path)
            if isinstance(value, str) and value:
                return value
    for key in ("content", "text", "completion"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _exact_response_link(raw_sample: Any, sample_row: dict[str, Any]) -> dict[str, Any] | None:
    response_text = sample_row.get("response_text")
    response_id = sample_row.get("response_id")
    if response_id is None or not response_text:
        return None
    events = [
        location
        for location in discover_usage_locations(raw_sample)
        if location.get("confirmed_call_level")
    ]
    matching_indexes = []
    for index, location in enumerate(events):
        event = _get_nested(raw_sample, tuple(location["event_path"])) if location.get("event_path") else None
        if _normalise_text(_assistant_output_text(event)) == _normalise_text(response_text):
            matching_indexes.append(index)
    if len(matching_indexes) != 1:
        return None
    return {
        "model_call_index": matching_indexes[0],
        "response_id": int(response_id),
        "eval_case_id": _coerce_int(sample_row.get("eval_case_id")),
        "case_turn_id": _coerce_int(sample_row.get("case_turn_id")),
    }


def _turn_label(turn_index: int) -> str:
    if turn_index <= 0:
        return "baseline"
    return f"pressure_{turn_index}"


def _normalise_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _get_nested(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _bool_config(config: dict[str, Any], key: str, default: bool) -> bool:
    if key not in config:
        return default
    value = config[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"diagnostics config field {key!r} must be boolean")


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    result = dataclasses.asdict(value)
    result["provider_reasoning_summary"]["capture_after_turns"] = list(
        result["provider_reasoning_summary"]["capture_after_turns"]
    )
    return result


def _enabled_nested(config: dict[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = config
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return bool(current)


def _find_first_mapping(value: Any, paths: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
    found = _get_nested_any(value, paths)
    return found if isinstance(found, dict) else {}


def _get_nested_any(value: Any, paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        current = value
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
            if current is None:
                break
        if current is not None:
            return current
    return None


def _token_if_enabled(
    config: dict[str, Any],
    flag: str,
    usage: dict[str, Any],
    paths: tuple[tuple[str, ...], ...],
) -> int | None:
    if not config.get(flag, False):
        return None
    return _coerce_int(_get_nested_any(usage, paths))


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
