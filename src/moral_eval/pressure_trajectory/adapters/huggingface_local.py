"""Local Hugging Face backend with contextual nominated-label scoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain import (
    ContextualLabelToken,
    GenerationSettings,
    LabelTokenLogit,
    Message,
    ModelMetadata,
    NominatedLabelLogits,
)

BACKEND_IMPLEMENTATION = {
    "name": "moral_eval.pressure_trajectory.huggingface_local",
    "version": "v2",
}
SUPPORTED_DEVICES = {"auto", "cpu", "cuda"}
SUPPORTED_DTYPES = {"auto", "float32", "float16", "bfloat16"}


class HuggingFaceBackendError(RuntimeError):
    pass


def _token_id_list(value: Any) -> list[int]:
    if isinstance(value, Mapping):
        value = value.get("input_ids")
    elif hasattr(value, "input_ids"):
        value = value.input_ids
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        value = value[0]
    if not isinstance(value, list):
        raise HuggingFaceBackendError("Chat template did not return token IDs")
    return [int(token_id) for token_id in value]


def _resolve_device(torch: Any, requested: str) -> Any:
    if requested not in SUPPORTED_DEVICES:
        raise HuggingFaceBackendError(f"Unsupported device {requested!r}")
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise HuggingFaceBackendError("CUDA was requested but is unavailable")
    return torch.device(requested)


def _resolve_dtype(torch: Any, requested: str, device: Any) -> Any:
    if requested not in SUPPORTED_DTYPES:
        raise HuggingFaceBackendError(f"Unsupported dtype {requested!r}")
    dtype_values = {
        "auto": torch.float32,
        "float32": torch.float32,
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
    }
    resolved = dtype_values[requested]
    if device.type == "cpu" and resolved == torch.float16:
        raise HuggingFaceBackendError("float16 CPU inference is unsupported")
    return resolved


def _messages(transcript: Sequence[Message]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in transcript]


def validate_contextual_label_token(
    tokenizer: Any, transcript: Sequence[Message], label: str
) -> ContextualLabelToken:
    """Validate the canonical exact A or B surface as one contextual token."""
    if label not in {"A", "B"}:
        raise HuggingFaceBackendError(
            f"Canonical nominated labels are exactly 'A' and 'B'; received {label!r}"
        )
    if not getattr(tokenizer, "chat_template", None):
        raise HuggingFaceBackendError(
            "Tokenizer has no chat template; use a compatible instruction model"
        )
    try:
        rendered = tokenizer.apply_chat_template(
            _messages(transcript), tokenize=False, add_generation_prompt=True
        )
        prefix_ids = _token_id_list(
            tokenizer.apply_chat_template(
                _messages(transcript),
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
            )
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise HuggingFaceBackendError(
            "Chat-template rendering requires Jinja2; install it with "
            "'.\\.venv\\Scripts\\python.exe -m pip install -r "
            "requirements-pressure-trajectory.txt'"
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HuggingFaceBackendError(
            "Chat-template formatting failed during label validation"
        ) from exc
    if not isinstance(rendered, str):
        raise HuggingFaceBackendError("Chat template did not return rendered text")
    rendered_ids = _token_id_list(tokenizer.encode(rendered, add_special_tokens=False))
    if rendered_ids != prefix_ids:
        raise HuggingFaceBackendError(
            "Rendered chat prefix does not reproduce the tokenised generation boundary"
        )

    try:
        extended = _token_id_list(
            tokenizer.encode(rendered + label, add_special_tokens=False)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HuggingFaceBackendError(
            f"Canonical exact label {label!r} could not be tokenised"
        ) from exc
    if extended[: len(prefix_ids)] != prefix_ids:
        raise HuggingFaceBackendError(
            f"Canonical exact label {label!r} retokenises prior context"
        )
    continuation = extended[len(prefix_ids) :]
    if not continuation:
        raise HuggingFaceBackendError(
            f"Canonical exact label {label!r} produces no continuation token"
        )
    if len(continuation) != 1:
        raise HuggingFaceBackendError(
            f"Canonical exact label {label!r} requires more than one continuation "
            f"token: {continuation}"
        )
    token_id = continuation[0]
    decoded = tokenizer.decode(
        [token_id],
        skip_special_tokens=False,
        clean_up_tokenization_spaces=False,
    )
    if decoded != label:
        raise HuggingFaceBackendError(
            f"Canonical exact label {label!r} token decodes as {decoded!r}"
        )
    return ContextualLabelToken(
        label=label,
        token_id=token_id,
        decoded_text=decoded,
    )


def validate_contextual_label_tokens(
    tokenizer: Any, transcript: Sequence[Message], labels: Sequence[str]
) -> tuple[ContextualLabelToken, ...]:
    if not labels or len(labels) != len(set(labels)):
        raise HuggingFaceBackendError("Nominated labels must be unique and non-empty")
    return tuple(
        validate_contextual_label_token(tokenizer, transcript, label) for label in labels
    )


def load_huggingface_tokenizer(model_id: str, revision: str | None) -> Any:
    """Load only a tokenizer; importing Transformers remains lazy."""
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise HuggingFaceBackendError(
            "Tokenizer checks require Transformers"
        ) from exc
    kwargs = {"revision": revision} if revision is not None else {}
    try:
        return AutoTokenizer.from_pretrained(model_id, **kwargs)
    except (OSError, TypeError, ValueError) as exc:
        raise HuggingFaceBackendError(
            f"Could not load tokenizer for {model_id!r}: {exc}"
        ) from exc


class HuggingFaceLocalBackend:
    def __init__(
        self,
        *,
        model: Any,
        tokenizer: Any,
        torch_module: Any,
        transformers_version: str,
        model_id: str,
        requested_revision: str | None,
        device: Any,
        dtype: Any,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.torch = torch_module
        self.transformers_version = transformers_version
        self.model_id = model_id
        self.requested_revision = requested_revision
        self.device = device
        self.dtype = dtype
        self.model.eval()

    @classmethod
    def from_pretrained(
        cls,
        *,
        model_id: str,
        revision: str | None,
        device: str,
        dtype: str,
    ) -> "HuggingFaceLocalBackend":
        if device not in SUPPORTED_DEVICES:
            raise HuggingFaceBackendError(f"Unsupported device {device!r}")
        if dtype not in SUPPORTED_DTYPES:
            raise HuggingFaceBackendError(f"Unsupported dtype {dtype!r}")
        try:
            import torch
            import transformers
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise HuggingFaceBackendError(
                "Local Hugging Face runs require PyTorch and Transformers"
            ) from exc

        resolved_device = _resolve_device(torch, device)
        resolved_dtype = _resolve_dtype(torch, dtype, resolved_device)

        load_kwargs: dict[str, Any] = {"dtype": resolved_dtype}
        tokenizer_kwargs: dict[str, Any] = {}
        if revision is not None:
            load_kwargs["revision"] = revision
            tokenizer_kwargs["revision"] = revision
        try:
            tokenizer = AutoTokenizer.from_pretrained(model_id, **tokenizer_kwargs)
            model = AutoModelForCausalLM.from_pretrained(model_id, **load_kwargs)
            model.to(resolved_device)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HuggingFaceBackendError(
                f"Could not load local Hugging Face model {model_id!r}: {exc}"
            ) from exc
        return cls(
            model=model,
            tokenizer=tokenizer,
            torch_module=torch,
            transformers_version=str(transformers.__version__),
            model_id=model_id,
            requested_revision=revision,
            device=resolved_device,
            dtype=resolved_dtype,
        )

    def _require_chat_template(self) -> None:
        if not getattr(self.tokenizer, "chat_template", None):
            raise HuggingFaceBackendError(
                "Tokenizer has no chat template; use a compatible instruction model"
            )

    def _encoded_transcript(self, transcript: Sequence[Message]) -> dict[str, Any]:
        self._require_chat_template()
        try:
            encoded = self.tokenizer.apply_chat_template(
                _messages(transcript),
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise HuggingFaceBackendError(
                "Chat-template rendering requires Jinja2; install it with "
                "'.\\.venv\\Scripts\\python.exe -m pip install -r "
                "requirements-pressure-trajectory.txt'"
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise HuggingFaceBackendError("Chat-template tokenisation failed") from exc
        if not isinstance(encoded, Mapping) or "input_ids" not in encoded:
            raise HuggingFaceBackendError("Chat template returned malformed model inputs")
        return {key: value.to(self.device) for key, value in encoded.items()}

    def generate(
        self, transcript: Sequence[Message], settings: GenerationSettings
    ) -> str:
        encoded = self._encoded_transcript(transcript)
        self.torch.manual_seed(settings.seed)
        if self.device.type == "cuda":
            self.torch.cuda.manual_seed_all(settings.seed)
        pad_token_id = (
            self.tokenizer.pad_token_id
            if self.tokenizer.pad_token_id is not None
            else self.tokenizer.eos_token_id
        )
        try:
            with self.torch.inference_mode():
                generated = self.model.generate(
                    **encoded,
                    max_new_tokens=settings.max_new_tokens,
                    do_sample=settings.do_sample,
                    num_beams=settings.num_beams,
                    pad_token_id=pad_token_id,
                )
        except RuntimeError as exc:
            raise HuggingFaceBackendError(f"Model generation failed: {exc}") from exc
        input_length = int(encoded["input_ids"].shape[-1])
        new_tokens = generated[0, input_length:]
        return str(self.tokenizer.decode(new_tokens, skip_special_tokens=True)).strip()

    def next_token_label_logits(
        self, transcript: Sequence[Message], labels: Sequence[str]
    ) -> NominatedLabelLogits:
        validated_tokens = validate_contextual_label_tokens(
            self.tokenizer, transcript, labels
        )
        token_ids = {item.label: item.token_id for item in validated_tokens}
        encoded = self._encoded_transcript(transcript)
        try:
            with self.torch.inference_mode():
                output = self.model(**encoded, use_cache=False)
                full_next_token_logits = output.logits[0, -1].float()
                values = tuple(
                    LabelTokenLogit(
                        label=label,
                        token_id=token_ids[label],
                        raw_logit=float(full_next_token_logits[token_ids[label]].item()),
                    )
                    for label in labels
                )
        except (IndexError, RuntimeError) as exc:
            raise HuggingFaceBackendError(f"Next-token scoring failed: {exc}") from exc
        return NominatedLabelLogits(values=values)

    def reproducibility_metadata(self) -> ModelMetadata:
        model_revision = getattr(getattr(self.model, "config", None), "_commit_hash", None)
        tokenizer_kwargs = getattr(self.tokenizer, "init_kwargs", {})
        tokenizer_revision = (
            tokenizer_kwargs.get("_commit_hash")
            if isinstance(tokenizer_kwargs, Mapping)
            else None
        )
        return ModelMetadata(
            model_id=self.model_id,
            requested_revision=self.requested_revision,
            resolved_model_revision=(str(model_revision) if model_revision else None),
            tokenizer_id=str(getattr(self.tokenizer, "name_or_path", self.model_id)),
            resolved_tokenizer_revision=(
                str(tokenizer_revision) if tokenizer_revision else None
            ),
            device=str(self.device),
            dtype=str(self.dtype),
            software_versions={
                "python": __import__("platform").python_version(),
                "torch": str(self.torch.__version__),
                "transformers": self.transformers_version,
            },
        )
