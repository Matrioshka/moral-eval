from __future__ import annotations

import math
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from moral_eval.pressure_trajectory.adapters.huggingface_local import (
    HuggingFaceBackendError,
    HuggingFaceLocalBackend,
    _resolve_device,
    _resolve_dtype,
    validate_contextual_label_token,
)
from moral_eval.pressure_trajectory.domain import (
    ActionChoice,
    ConversationCheckpoint,
    GenerationSettings,
    MEASUREMENT_TIMINGS,
    Message,
    OptionMapping,
    TrajectoryScenario,
    TOKEN_SELECTION_POLICY,
)
from moral_eval.pressure_trajectory.measurements import (
    INITIAL_PROMPT_VERSION,
    MEASUREMENT_VERSION,
    MEASUREMENT_PROMPT_VERSION,
    build_initial_user_prompt,
    build_shadow_measurement_prompt,
    measure_checkpoint,
    restricted_two_option_softmax,
)

from .fakes import FakeBackend


def scenario() -> TrajectoryScenario:
    return TrajectoryScenario(
        scenario_id="case",
        category="test",
        scenario_text="A decision is required.",
        action_choices=(
            ActionChoice("bounded", "Keep the bounded action.", "bounded"),
            ActionChoice("broader", "Take the broader action.", "broader"),
        ),
        pressure_turns=(),
        source_metadata={},
        initial_evidence=("The gate remains unresolved.",),
    )


def checkpoint() -> ConversationCheckpoint:
    return ConversationCheckpoint(
        checkpoint_id="checkpoint-0",
        parent_checkpoint_id=None,
        round_index=0,
        transcript=(
            Message("user", "Advise me."),
            Message("assistant", "The bounded action is justified."),
        ),
        current_evidence_state="unresolved",
    )


MAPPINGS = (
    OptionMapping("bounded_A_broader_B", "A", "B", ("bounded", "broader")),
    OptionMapping("broader_A_bounded_B", "B", "A", ("broader", "bounded")),
    OptionMapping(
        "bounded_A_broader_B__B_then_A", "A", "B", ("broader", "bounded")
    ),
    OptionMapping(
        "broader_A_bounded_B__B_then_A", "B", "A", ("bounded", "broader")
    ),
)


def test_mapping_margin_softmax_boundary_and_timing() -> None:
    backend = FakeBackend(responses=[], scripted_logits=[{"A": -1.0, "B": 2.0}])

    result = measure_checkpoint(
        backend=backend,
        scenario=scenario(),
        checkpoint=checkpoint(),
        mapping=MAPPINGS[0],
        measurement_timing="post_response",
    )

    assert result.measurement_version == MEASUREMENT_VERSION
    assert result.measurement_timing == "post_response"
    assert result.transcript_sha256
    assert result.token_selection_policy == TOKEN_SELECTION_POLICY
    assert result.bounded_token_id == ord("A")
    assert result.broader_token_id == ord("B")
    assert result.mapped_margin == 3.0
    assert result.boundary_classification == "broader_side"
    expected_bounded, expected_broader = restricted_two_option_softmax(-1.0, 2.0)
    assert result.restricted_bounded_probability == pytest.approx(expected_bounded)
    assert result.restricted_broader_probability == pytest.approx(expected_broader)
    assert (
        result.restricted_bounded_probability
        + result.restricted_broader_probability
        == pytest.approx(1.0)
    )


def test_shadow_prompt_has_no_model_visible_version_marker() -> None:
    prompt = build_shadow_measurement_prompt(scenario(), MAPPINGS[0])

    assert MEASUREMENT_PROMPT_VERSION not in prompt
    assert "Reply with exactly A or B and no explanation." in prompt


def test_all_mapping_permutations_preserve_semantic_margin() -> None:
    results = []
    for mapping in MAPPINGS:
        label_logits = {
            mapping.bounded_label: -0.75,
            mapping.broader_label: 1.25,
        }
        backend = FakeBackend(responses=[], scripted_logits=[label_logits])
        results.append(
            measure_checkpoint(
                backend=backend,
                scenario=scenario(),
                checkpoint=checkpoint(),
                mapping=mapping,
                measurement_timing="post_response",
            )
        )

    assert {result.mapped_margin for result in results} == {2.0}
    assert {result.boundary_classification for result in results} == {
        "broader_side"
    }


def test_softmax_is_stable_for_large_logits() -> None:
    bounded, broader = restricted_two_option_softmax(10_000.0, 10_001.0)
    assert math.isfinite(bounded)
    assert math.isfinite(broader)
    assert broader > bounded


def test_v2_initial_prompt_requests_a_concise_explicit_recommendation() -> None:
    prompt = build_initial_user_prompt(scenario())

    assert INITIAL_PROMPT_VERSION == "pressure_trajectory_initial_prompt_v2"
    assert "approximately 120 words or fewer" in prompt
    assert "Explicitly identify the recommended action" in prompt
    assert "JSON" not in prompt
    assert MEASUREMENT_TIMINGS == ("pre_response", "post_response")


class FakeTensor:
    def __init__(self, values: list[list[int]]) -> None:
        self.values = values
        self.moved_to = None

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.values), len(self.values[0])

    def to(self, device: object) -> "FakeTensor":
        self.moved_to = device
        return self

    def tolist(self) -> list[list[int]]:
        return self.values

    def __getitem__(self, key: tuple[int, slice]) -> list[int]:
        row, column = key
        return self.values[row][column]


class CharacterTokenizer:
    chat_template = "test"
    pad_token_id = None
    eos_token_id = 0
    name_or_path = "fake/tokenizer"
    init_kwargs = {"_commit_hash": "tokenizer-commit"}

    def __init__(self) -> None:
        self.template_calls: list[dict[str, object]] = []

    @staticmethod
    def _render(messages: list[dict[str, str]]) -> str:
        return (
            "".join(f"<{item['role']}>{item['content']}" for item in messages)
            + "<assistant>"
        )

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
        return_dict: bool = False,
        return_tensors: str | None = None,
        **_: object,
    ) -> str | list[int] | dict[str, object]:
        assert add_generation_prompt
        self.template_calls.append(
            {
                "messages": messages,
                "tokenize": tokenize,
                "add_generation_prompt": add_generation_prompt,
                "return_tensors": return_tensors,
            }
        )
        rendered = self._render(messages)
        if not tokenize:
            return rendered
        ids = self.encode(rendered, add_special_tokens=False)
        value: object = FakeTensor([ids]) if return_tensors == "pt" else ids
        return {"input_ids": value} if return_dict else ids

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        return [ord(character) for character in text]

    def decode(self, ids: object, **_: object) -> str:
        if hasattr(ids, "tolist"):
            ids = ids.tolist()
        if _.get("skip_special_tokens"):
            ids = [item for item in ids if item != self.eos_token_id]
        return "".join(chr(item) for item in ids)


class MultiTokenLabelTokenizer(CharacterTokenizer):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        ids = super().encode(text, add_special_tokens=add_special_tokens)
        if text.endswith("A"):
            return ids[:-1] + [9001, 9002]
        return ids


class WhitespaceLabelTokenizer(CharacterTokenizer):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        if text.endswith("<assistant>A"):
            prefix = text[:-1]
            return [ord(character) for character in prefix] + [9001, 9002]
        if text.endswith("<assistant> A"):
            prefix = text[:-2]
            return [ord(character) for character in prefix] + [9100]
        return [ord(character) for character in text]

    def decode(self, ids: object, **kwargs: object) -> str:
        if list(ids) == [9100]:
            return " A"
        return super().decode(ids, **kwargs)


class RetokenisingLabelTokenizer(CharacterTokenizer):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        ids = super().encode(text, add_special_tokens=add_special_tokens)
        if text.endswith("A"):
            return [ids[0] + 1, *ids[1:]]
        return ids


class UnrelatedDecodedLabelTokenizer(CharacterTokenizer):
    def decode(self, ids: object, **kwargs: object) -> str:
        if list(ids) == [ord("A")]:
            return "X"
        return super().decode(ids, **kwargs)


class QwenLikeLabelTokenizer(CharacterTokenizer):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        if text.endswith("<assistant> A"):
            prefix = text[:-2]
            return [ord(character) for character in prefix] + [9100]
        if text.endswith("<assistant> B"):
            prefix = text[:-2]
            return [ord(character) for character in prefix] + [9200]
        return [ord(character) for character in text]

    def decode(self, ids: object, **kwargs: object) -> str:
        if list(ids) == [9100]:
            return " A"
        if list(ids) == [9200]:
            return " B"
        return super().decode(ids, **kwargs)


class MissingJinjaTokenizer(CharacterTokenizer):
    def apply_chat_template(self, *args: object, **kwargs: object):
        raise ImportError("No module named 'jinja2'")


class FakeScalar:
    def __init__(self, value: float) -> None:
        self.value = value

    def item(self) -> float:
        return self.value


class FakeFinalLogits:
    def __init__(self, values: dict[int, float]) -> None:
        self.values = values

    def float(self) -> "FakeFinalLogits":
        return self

    def __getitem__(self, token_id: int) -> FakeScalar:
        return FakeScalar(self.values[token_id])


class FakeLogitTensor:
    def __init__(self, values: dict[int, float]) -> None:
        self.values = values
        self.positions: list[tuple[int, int]] = []

    def __getitem__(self, position: tuple[int, int]) -> FakeFinalLogits:
        self.positions.append(position)
        return FakeFinalLogits(self.values)


class FakeModel:
    config = SimpleNamespace(_commit_hash="model-commit")

    def __init__(self) -> None:
        self.eval_called = False
        self.generation_config = SimpleNamespace(eos_token_id=None)
        self.generate_kwargs: dict[str, object] | None = None
        self.forward_kwargs: dict[str, object] | None = None
        self.logits = FakeLogitTensor({ord("A"): 1.25, ord("B"): -0.5})
        self.generated_suffix = [ord("O"), ord("K")]

    def eval(self) -> None:
        self.eval_called = True

    def generate(self, **kwargs: object) -> FakeTensor:
        self.generate_kwargs = kwargs
        input_ids = kwargs["input_ids"].tolist()[0]
        return FakeTensor([input_ids + self.generated_suffix])

    def __call__(self, **kwargs: object) -> SimpleNamespace:
        self.forward_kwargs = kwargs
        return SimpleNamespace(logits=self.logits)


class FakeDevice:
    def __init__(self, value: str) -> None:
        self.type = value

    def __str__(self) -> str:
        return self.type


class FakeCuda:
    def __init__(self, available: bool = False) -> None:
        self.available = available
        self.seeds: list[int] = []

    def is_available(self) -> bool:
        return self.available

    def manual_seed_all(self, seed: int) -> None:
        self.seeds.append(seed)


class FakeTorch:
    __version__ = "test-torch"
    float32 = "float32"
    float16 = "float16"
    bfloat16 = "bfloat16"

    def __init__(self, cuda_available: bool = False) -> None:
        self.cuda = FakeCuda(cuda_available)
        self.seeds: list[int] = []
        self.inference_entries = 0

    def device(self, value: str) -> FakeDevice:
        return FakeDevice(value)

    def manual_seed(self, seed: int) -> None:
        self.seeds.append(seed)

    @contextmanager
    def inference_mode(self):
        self.inference_entries += 1
        yield


def make_hf_backend(
    tokenizer: CharacterTokenizer | None = None,
) -> tuple[HuggingFaceLocalBackend, FakeModel, FakeTorch]:
    model = FakeModel()
    torch = FakeTorch()
    backend = HuggingFaceLocalBackend(
        model=model,
        tokenizer=tokenizer or CharacterTokenizer(),
        torch_module=torch,
        transformers_version="test-transformers",
        model_id="fake/model",
        requested_revision="requested-revision",
        device=FakeDevice("cpu"),
        dtype="float32",
    )
    return backend, model, torch


def test_contextual_multi_token_label_fails_clearly() -> None:
    tokenizer = MultiTokenLabelTokenizer()

    with pytest.raises(HuggingFaceBackendError, match="more than one continuation"):
        validate_contextual_label_token(
            tokenizer, (Message("user", "Choose."),), "A"
        )


def test_leading_space_token_does_not_replace_multi_token_exact_label() -> None:
    with pytest.raises(HuggingFaceBackendError, match="more than one continuation"):
        validate_contextual_label_token(
            WhitespaceLabelTokenizer(), (Message("user", "Choose."),), "A"
        )


@pytest.mark.parametrize(("label", "token_id"), [("A", 65), ("B", 66)])
def test_qwen_like_tokenizer_selects_only_canonical_exact_label(
    label: str, token_id: int
) -> None:
    token = validate_contextual_label_token(
        QwenLikeLabelTokenizer(), (Message("user", "Choose."),), label
    )

    assert token.label == label
    assert token.token_id == token_id
    assert token.decoded_text == label


@pytest.mark.parametrize(
    ("tokenizer", "message"),
    [
        (RetokenisingLabelTokenizer(), "retokenises prior context"),
        (UnrelatedDecodedLabelTokenizer(), "token decodes as 'X'"),
    ],
)
def test_contextual_validation_rejects_unsafe_label_boundaries(
    tokenizer: CharacterTokenizer, message: str
) -> None:
    with pytest.raises(HuggingFaceBackendError, match=message):
        validate_contextual_label_token(
            tokenizer, (Message("user", "Choose."),), "A"
        )


def test_missing_jinja_has_clear_runtime_dependency_error() -> None:
    with pytest.raises(HuggingFaceBackendError, match="requires Jinja2"):
        validate_contextual_label_token(
            MissingJinjaTokenizer(), (Message("user", "Choose."),), "A"
        )

    backend, _, _ = make_hf_backend(MissingJinjaTokenizer())
    with pytest.raises(HuggingFaceBackendError, match="requires Jinja2"):
        backend.generate(
            (Message("user", "Advise."),),
            GenerationSettings(),
        )


def test_adapter_uses_chat_boundary_final_logits_and_nominated_tokens_only() -> None:
    backend, model, torch = make_hf_backend()

    result = backend.next_token_label_logits(
        (Message("user", "Choose."),), ("A", "B")
    )

    assert model.eval_called
    assert torch.inference_entries == 1
    assert model.logits.positions == [(0, -1)]
    assert model.forward_kwargs is not None
    assert model.forward_kwargs["use_cache"] is False
    assert [(item.label, item.token_id, item.raw_logit) for item in result.values] == [
        ("A", ord("A"), 1.25),
        ("B", ord("B"), -0.5),
    ]


def test_leading_space_tokens_do_not_cross_backend_protocol() -> None:
    backend, _, _ = make_hf_backend(QwenLikeLabelTokenizer())

    result = backend.next_token_label_logits(
        (Message("user", "Choose."),), ("A", "B")
    )

    assert {item.token_id for item in result.values} == {ord("A"), ord("B")}
    assert {9100, 9200}.isdisjoint(item.token_id for item in result.values)


def test_generation_slices_prompt_and_uses_deterministic_inference_settings() -> None:
    tokenizer = CharacterTokenizer()
    backend, model, torch = make_hf_backend(tokenizer)
    settings = GenerationSettings(
        max_new_tokens=17, seed=42, do_sample=False, num_beams=1
    )

    result = backend.generate((Message("user", "Advise."),), settings)

    assert result.response_text == "OK"
    assert result.generated_token_count == 2
    assert not result.eos_reached
    assert not result.max_new_tokens_reached
    assert result.finish_reason is None
    assert result.generation_settings == settings
    assert torch.inference_entries == 1
    assert torch.seeds == [42]
    assert model.generate_kwargs is not None
    assert model.generate_kwargs["max_new_tokens"] == 17
    assert model.generate_kwargs["do_sample"] is False
    assert model.generate_kwargs["num_beams"] == 1
    assert model.generate_kwargs["pad_token_id"] == tokenizer.eos_token_id
    assert all(call["add_generation_prompt"] for call in tokenizer.template_calls)


def test_generation_records_eos_and_max_token_completion_from_token_ids() -> None:
    backend, model, _ = make_hf_backend()
    model.generated_suffix = [ord("O"), 0]

    eos_result = backend.generate(
        (Message("user", "Advise."),),
        GenerationSettings(max_new_tokens=2),
    )

    assert eos_result.response_text == "O"
    assert eos_result.generated_token_count == 2
    assert eos_result.eos_reached
    assert not eos_result.max_new_tokens_reached
    assert eos_result.finish_reason == "eos_token"

    model.generated_suffix = [ord("O"), ord("K")]
    max_result = backend.generate(
        (Message("user", "Advise."),),
        GenerationSettings(max_new_tokens=2),
    )

    assert not max_result.eos_reached
    assert max_result.max_new_tokens_reached
    assert max_result.finish_reason == "max_new_tokens"


def test_generation_uses_model_eos_ids_before_tokenizer_fallback() -> None:
    backend, model, _ = make_hf_backend()
    model.generation_config.eos_token_id = [9001, 9002]
    model.generated_suffix = [ord("O"), 9002, ord("K")]

    result = backend.generate(
        (Message("user", "Advise."),),
        GenerationSettings(max_new_tokens=3),
    )

    assert result.generated_token_count == 3
    assert result.eos_reached
    assert not result.max_new_tokens_reached
    assert result.finish_reason == "eos_token"


def test_generation_does_not_infer_eos_from_decoded_prose() -> None:
    class EosLookingTokenizer(CharacterTokenizer):
        def decode(self, ids: object, **_: object) -> str:
            return "</s>"

    backend, model, _ = make_hf_backend(EosLookingTokenizer())
    model.generated_suffix = [ord("O")]

    result = backend.generate(
        (Message("user", "Advise."),),
        GenerationSettings(max_new_tokens=2),
    )

    assert result.response_text == "</s>"
    assert not result.eos_reached
    assert not result.max_new_tokens_reached
    assert result.finish_reason is None


def test_empty_generation_has_explicit_zero_and_unknown_finish() -> None:
    backend, model, _ = make_hf_backend()
    model.generated_suffix = []

    result = backend.generate(
        (Message("user", "Advise."),),
        GenerationSettings(max_new_tokens=2),
    )

    assert result.response_text == ""
    assert result.generated_token_count == 0
    assert not result.eos_reached
    assert not result.max_new_tokens_reached
    assert result.finish_reason is None


def test_model_and_tokenizer_metadata_are_recorded() -> None:
    backend, _, _ = make_hf_backend()

    metadata = backend.reproducibility_metadata()

    assert metadata.model_id == "fake/model"
    assert metadata.requested_revision == "requested-revision"
    assert metadata.resolved_model_revision == "model-commit"
    assert metadata.tokenizer_id == "fake/tokenizer"
    assert metadata.resolved_tokenizer_revision == "tokenizer-commit"
    assert metadata.device == "cpu"
    assert metadata.dtype == "float32"
    assert metadata.software_versions["torch"] == "test-torch"
    assert metadata.software_versions["transformers"] == "test-transformers"


def test_invalid_device_and_dtype_are_rejected() -> None:
    torch = FakeTorch()

    with pytest.raises(HuggingFaceBackendError, match="Unsupported device"):
        HuggingFaceLocalBackend.from_pretrained(
            model_id="fake",
            revision=None,
            device="directml",
            dtype="float32",
        )
    with pytest.raises(HuggingFaceBackendError, match="Unsupported dtype"):
        HuggingFaceLocalBackend.from_pretrained(
            model_id="fake",
            revision=None,
            device="cpu",
            dtype="int8",
        )
    with pytest.raises(HuggingFaceBackendError, match="Unsupported device"):
        _resolve_device(torch, "directml")
    with pytest.raises(HuggingFaceBackendError, match="CUDA.*unavailable"):
        _resolve_device(torch, "cuda")
    with pytest.raises(HuggingFaceBackendError, match="Unsupported dtype"):
        _resolve_dtype(torch, "int8", FakeDevice("cpu"))
    with pytest.raises(HuggingFaceBackendError, match="float16 CPU"):
        _resolve_dtype(torch, "float16", FakeDevice("cpu"))
