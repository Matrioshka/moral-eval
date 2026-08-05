"""Run or validate the Stage 1 pressure-trajectory evaluator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.pressure_trajectory.adapters.existing_datasets import (  # noqa: E402
    load_trajectory,
)
from moral_eval.pressure_trajectory.adapters.huggingface_local import (  # noqa: E402
    BACKEND_IMPLEMENTATION,
    HuggingFaceBackendError,
    HuggingFaceLocalBackend,
    load_huggingface_tokenizer,
    validate_contextual_label_tokens,
)
from moral_eval.pressure_trajectory.domain import (  # noqa: E402
    GenerationSettings,
    Message,
    RunMetadata,
)
from moral_eval.pressure_trajectory.measurements import (  # noqa: E402
    ANSWER_LABELS,
    MEASUREMENT_PROMPT_VERSION,
    MEASUREMENT_TIMING,
    build_initial_user_prompt,
    build_shadow_measurement_prompt,
)
from moral_eval.pressure_trajectory.provenance import (  # noqa: E402
    build_experiment_configuration,
    experiment_configuration_sha256,
)
from moral_eval.pressure_trajectory.runner import TrajectoryRunner  # noqa: E402
from moral_eval.pressure_trajectory.storage import JsonlEventRecorder  # noqa: E402

DEFAULT_DATASET = (
    ROOT
    / "data"
    / "datasets"
    / "action_logprob"
    / "action_logprob_positive_control_gate_v0.jsonl"
)
DEFAULT_FIXTURE = ROOT / "experiments" / "pressure_trajectory_mvp_v0.json"
DEFAULT_CASE_ID = "deployment_gate__autonomous_cyber_defence_pilot_001"
DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
DEFAULT_OUTPUT = ROOT / "tmp" / "pressure_trajectory" / "stage1_mvp.jsonl"
TOKENIZER_CHECK_PLACEHOLDER = (
    "[TOKENIZER CHECK PLACEHOLDER: NOT A MODEL RESPONSE; CHECKPOINT {index}]"
)


class CliError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a replayable pressure trajectory with shadow A/B measurements."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--trajectory", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--case-id", default=DEFAULT_CASE_ID)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--dtype",
        choices=("auto", "float32", "float16", "bfloat16"),
        default="auto",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print configuration without loading a model.",
    )
    modes.add_argument(
        "--tokenizer-check",
        action="store_true",
        help=(
            "Load only the tokenizer and validate A/B boundaries using explicit "
            "placeholder assistant responses; do not generate or write events."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace the nominated event log instead of refusing it.",
    )
    return parser.parse_args(argv)


def validate_output_path(path: Path, *, overwrite: bool) -> None:
    if path.suffix.lower() != ".jsonl":
        raise CliError("--output must name a .jsonl event log")
    if path.exists() and path.is_dir():
        raise CliError(f"Output path is a directory: {path}")
    if path.exists() and not overwrite:
        raise CliError(f"Refusing to overwrite existing event log: {path}")
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.exists() or not ancestor.is_dir():
        raise CliError(f"Output path has no existing directory ancestor: {path}")


def build_cli_experiment_configuration(
    args: argparse.Namespace, loaded: object, settings: GenerationSettings
) -> dict[str, object]:
    return build_experiment_configuration(
        scenario=loaded.scenario,
        option_mappings=loaded.option_mappings,
        model_id=args.model,
        requested_model_revision=args.revision,
        tokenizer_id=args.model,
        requested_tokenizer_revision=args.revision,
        generation_settings=settings,
        requested_device=args.device,
        requested_dtype=args.dtype,
        measurement_prompt_version=MEASUREMENT_PROMPT_VERSION,
        measurement_timing=MEASUREMENT_TIMING,
        backend_implementation=BACKEND_IMPLEMENTATION,
    )


def planned_configuration(
    args: argparse.Namespace,
    experiment_configuration: dict[str, object],
) -> dict[str, object]:
    if args.tokenizer_check:
        mode = "tokenizer_check"
    elif args.dry_run:
        mode = "dry_run"
    else:
        mode = "run"
    return {
        "experiment_configuration": experiment_configuration,
        "experiment_configuration_sha256": experiment_configuration_sha256(
            experiment_configuration
        ),
        "execution_metadata": {
            "mode": mode,
            "output": {
                "path": str(args.output),
                "exists": args.output.exists(),
                "overwrite_requested": args.overwrite,
            },
        },
    }


def tokenizer_check_contexts(loaded: object, tokenizer: object) -> list[dict[str, object]]:
    """Validate stable prompt structures using marked non-experimental placeholders."""
    scenario = loaded.scenario
    transcript: tuple[Message, ...] = (
        Message(role="user", content=build_initial_user_prompt(scenario)),
        Message(
            role="assistant",
            content=TOKENIZER_CHECK_PLACEHOLDER.format(index=0),
        ),
    )
    checkpoints = [transcript]
    for turn in scenario.pressure_turns:
        transcript = (
            *transcript,
            Message(role="user", content=turn.user_followup),
            Message(
                role="assistant",
                content=TOKENIZER_CHECK_PLACEHOLDER.format(index=turn.turn_index),
            ),
        )
        checkpoints.append(transcript)

    results: list[dict[str, object]] = []
    for checkpoint_index, checkpoint_transcript in enumerate(checkpoints):
        for mapping in loaded.option_mappings:
            shadow = (
                *checkpoint_transcript,
                Message(
                    role="user",
                    content=build_shadow_measurement_prompt(scenario, mapping),
                ),
            )
            tokens = validate_contextual_label_tokens(
                tokenizer, shadow, ANSWER_LABELS
            )
            results.append(
                {
                    "checkpoint_index": checkpoint_index,
                    "mapping_id": mapping.mapping_id,
                    "labels": [
                        {
                            "label": token.label,
                            "token_id": token.token_id,
                            "decoded_text": token.decoded_text,
                        }
                        for token in tokens
                    ],
                }
            )
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_new_tokens <= 0:
        raise CliError("--max-new-tokens must be positive")
    loaded = load_trajectory(
        dataset_path=args.dataset,
        case_id=args.case_id,
        fixture_path=args.trajectory,
    )
    settings = GenerationSettings(max_new_tokens=args.max_new_tokens, seed=args.seed)
    experiment_configuration = build_cli_experiment_configuration(
        args, loaded, settings
    )
    plan = planned_configuration(args, experiment_configuration)
    if not args.tokenizer_check:
        validate_output_path(args.output, overwrite=args.overwrite)
    print(json.dumps(plan, indent=2, sort_keys=True))
    if args.dry_run:
        print("Validation succeeded; no model was loaded and no event log was written.")
        return 0
    if args.tokenizer_check:
        tokenizer = load_huggingface_tokenizer(args.model, args.revision)
        results = tokenizer_check_contexts(loaded, tokenizer)
        print(json.dumps({"tokenizer_check": results}, indent=2, sort_keys=True))
        print(
            "Tokenizer check succeeded for stable prompt structures using marked "
            "placeholder assistant responses. This validates tokenisation only, not "
            "model behaviour or exact contexts containing future generated responses."
        )
        return 0

    backend = HuggingFaceLocalBackend.from_pretrained(
        model_id=args.model,
        revision=args.revision,
        device=args.device,
        dtype=args.dtype,
    )
    if args.output.exists():
        args.output.unlink()
    run_id = str(uuid4())
    runner = TrajectoryRunner(
        scenario=loaded.scenario,
        backend=backend,
        recorder=JsonlEventRecorder(args.output),
        option_mappings=loaded.option_mappings,
        run_metadata=RunMetadata(
            run_id=run_id,
            trajectory_id=str(loaded.trajectory_metadata["trajectory_id"]),
            experiment_configuration=experiment_configuration,
            experiment_configuration_sha256=experiment_configuration_sha256(
                experiment_configuration
            ),
            execution_metadata={
                "invocation": "cli",
                "overwrite_requested": args.overwrite,
            },
        ),
        generation_settings=settings,
    )
    summary = runner.run()
    actual_measurements = sum(len(item.measurements) for item in summary.checkpoints)
    print(
        f"Completed run {summary.run_id}: {len(summary.checkpoints)} checkpoints, "
        f"{actual_measurements} measurements, {summary.event_count} events."
    )
    print(f"Saved event log: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CliError, HuggingFaceBackendError, ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
