"""Structured LLM client helpers.

The core dataset-generation code depends only on the StructuredLLM protocol.
Provider adapters are deliberately thin. This keeps generation/test logic
independent of one vendor's SDK quirks.
"""

from __future__ import annotations

import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .prompts import Messages

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    def generate_structured(
        self,
        *,
        model: str,
        messages: Messages,
        response_model: type[T],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> T:
        """Return a parsed Pydantic model instance."""


class StructuredGenerationError(RuntimeError):
    pass


def _header_float(headers, *names: str) -> float | None:
    """Return a numeric HTTP header value, handling SDK header containers."""
    if not headers:
        return None
    for name in names:
        value = None
        if hasattr(headers, "get"):
            value = headers.get(name)
        if value is None and hasattr(headers, "get"):
            value = headers.get(name.lower())
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after_from_exception(exc: Exception) -> float | None:
    """Extract a provider-suggested retry delay when one is available.

    OpenAI-style SDK exceptions often expose response headers. The error body
    can also contain phrases such as "Please try again in 746ms". This helper
    is intentionally defensive so that it also works with OpenAI-compatible
    providers whose exceptions differ slightly.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)

    retry_after_ms = _header_float(headers, "retry-after-ms", "x-ratelimit-reset-tokens")
    if retry_after_ms is not None:
        # retry-after-ms is milliseconds; reset headers are often seconds as a
        # Unix-ish interval, but treating very large values as ms would be bad.
        return retry_after_ms / 1000.0 if retry_after_ms > 60 else retry_after_ms

    retry_after = _header_float(headers, "retry-after")
    if retry_after is not None:
        return retry_after

    message = str(exc)
    match_ms = re.search(r"try again in\s+([0-9]*\.?[0-9]+)\s*ms", message, flags=re.IGNORECASE)
    if match_ms:
        return float(match_ms.group(1)) / 1000.0

    match_s = re.search(r"try again in\s+([0-9]*\.?[0-9]+)\s*s", message, flags=re.IGNORECASE)
    if match_s:
        return float(match_s.group(1))

    return None


def _looks_like_rate_limit(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    message = str(exc).lower()
    return "rate limit" in message or "rate_limit" in message or "429" in message


def retry_with_exponential_backoff(
    fn,
    *,
    max_retries: int = 8,
    initial_delay: float = 2.0,
    max_delay: float = 90.0,
    jitter: float = 0.35,
    min_rate_limit_delay: float = 5.0,
):
    """Retry transient API failures with rate-limit-aware backoff.

    The previous short retry loop was too brittle for quota generation with
    concurrent calls. Token-per-minute limits can be hit even when the provider
    reports a sub-second retry delay, because several worker threads may be
    retrying together. For 429-style errors we therefore respect provider retry
    hints but also impose a modest minimum delay and jitter.
    """

    def wrapped(*args, **kwargs):
        delay = initial_delay
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # provider SDKs raise many different exception types
                last_exc = exc
                if attempt >= max_retries:
                    break

                retry_after = _retry_after_from_exception(exc)
                if _looks_like_rate_limit(exc):
                    base_sleep = max(min_rate_limit_delay, retry_after or delay)
                else:
                    base_sleep = retry_after or delay

                sleep_for = min(max_delay, base_sleep) * (1 + random.uniform(-jitter, jitter))
                time.sleep(max(0.0, sleep_for))
                delay = min(max_delay, delay * 2)
        raise StructuredGenerationError(f"Structured generation failed after retries: {last_exc}") from last_exc

    return wrapped


class OpenAIParseClient:
    """OpenAI adapter using the Python SDK parse helper.

    This is best for OpenAI models that support Pydantic response parsing. For
    OpenRouter or other OpenAI-compatible endpoints, use OpenAICompatibleJSONClient.
    """

    def __init__(self, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self.client = client

    def generate_structured(
        self,
        *,
        model: str,
        messages: Messages,
        response_model: type[T],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> T:
        def call() -> T:
            completion = self.client.chat.completions.parse(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_model,
            )
            msg = completion.choices[0].message
            parsed = getattr(msg, "parsed", None)
            if parsed is None:
                raise StructuredGenerationError(f"No parsed response returned; refusal={getattr(msg, 'refusal', None)!r}")
            return parsed

        return retry_with_exponential_backoff(call)()


class OpenAICompatibleJSONClient:
    """OpenAI-compatible JSON adapter with Pydantic validation.

    Useful for OpenRouter or endpoints where strict Pydantic parsing is not
    available. The model is asked for JSON and the result is validated locally.
    """

    def __init__(self, client=None, base_url: str | None = None, api_key: str | None = None):
        if client is None:
            from openai import OpenAI

            kwargs = {}
            if base_url:
                kwargs["base_url"] = base_url
            if api_key:
                kwargs["api_key"] = api_key
            client = OpenAI(**kwargs)
        self.client = client

    def generate_structured(
        self,
        *,
        model: str,
        messages: Messages,
        response_model: type[T],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> T:
        schema_hint = response_model.model_json_schema()
        schema_message = {
            "role": "system",
            "content": (
                "Return only valid JSON conforming to this JSON schema. "
                "Do not include markdown fences or explanatory text.\n"
                + json.dumps(schema_hint, ensure_ascii=False)
            ),
        }

        def call() -> T:
            completion = self.client.chat.completions.create(
                model=model,
                messages=[schema_message, *messages],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = completion.choices[0].message.content
            if not content:
                raise StructuredGenerationError("Empty JSON response")
            try:
                return response_model.model_validate_json(content)
            except ValidationError as exc:
                raise StructuredGenerationError(f"JSON response failed local Pydantic validation: {exc}\n{content}") from exc

        return retry_with_exponential_backoff(call)()


def generate_many_structured(
    *,
    llm: StructuredLLM,
    model: str,
    messages_list: list[Messages],
    response_model: type[T],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    max_workers: int | None = 6,
) -> list[T]:
    """Generate many structured responses concurrently, preserving input order."""

    def one(messages: Messages) -> T:
        return llm.generate_structured(
            model=model,
            messages=messages,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if max_workers is None or max_workers <= 1:
        return [one(messages) for messages in messages_list]

    ordered: list[T | None] = [None] * len(messages_list)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(one, messages): idx for idx, messages in enumerate(messages_list)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            ordered[idx] = future.result()

    return [item for item in ordered if item is not None]
