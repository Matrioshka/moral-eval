from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset

from moral_sycophancy_eval.behaviour import (
    MULTISTAGE_NATURAL_CONTEXT_PROMPT_STYLE,
    make_record_to_sample,
    multi_stage_pressure,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

MISCALIBRATED_CORRIGIBILITY_PILOT_V0_DATASET_PATH = (
    DATA_DIR / "moral_reasoning_integrity_behaviour_miscalibrated_corrigibility_pilot_v0.jsonl"
)

DATASET_CONFIGS = {
    "v6_miscalibrated_corrigibility_pilot_v0": {
        "path": MISCALIBRATED_CORRIGIBILITY_PILOT_V0_DATASET_PATH,
        "prompt_style": MULTISTAGE_NATURAL_CONTEXT_PROMPT_STYLE,
    },
}


@task
def miscalibrated_corrigibility_pressure(
    dataset_version: str = "v6_miscalibrated_corrigibility_pilot_v0",
) -> Task:
    """Natural multi-turn pressure eval focused on miscalibrated corrigibility.

    This task deliberately keeps the tested model in a natural behavioural
    dialogue. Structured judgement/extraction should be run as a separate
    sidecar pass over the saved responses, not inserted into the live dialogue.
    """

    if dataset_version not in DATASET_CONFIGS:
        valid = ", ".join(sorted(DATASET_CONFIGS))
        raise ValueError(f"Unknown dataset_version {dataset_version!r}. Valid options: {valid}")

    config = DATASET_CONFIGS[dataset_version]
    return Task(
        dataset=json_dataset(
            str(config["path"]),
            sample_fields=make_record_to_sample(str(config["prompt_style"])),
        ),
        solver=multi_stage_pressure(),
    )
