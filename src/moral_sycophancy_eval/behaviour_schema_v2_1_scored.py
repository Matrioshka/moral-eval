from __future__ import annotations

import sys
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.solver import generate

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from behaviour import DATASET_CONFIGS, make_record_to_sample  # noqa: E402
from schema_v2_1_scorer import schema_v2_1_access_scorer  # noqa: E402

SCHEMA_V2_1_DATASET_VERSIONS = {
    "v4_justifiable_corrigibility_release_governance_schema_v2_1_candidate",
    "v4_justifiable_corrigibility_release_governance_schema_v2_1_11item_pilot",
}


@task
def moral_reasoning_integrity_behaviour_schema_v2_1_scored(
    dataset_version: str = "v4_justifiable_corrigibility_release_governance_schema_v2_1_11item_pilot",
) -> Task:
    """Free-response schema-v2.1 behavioural eval with deterministic tuple scoring.

    This task keeps the normal behavioural generation setup, but adds a narrow
    deterministic scorer for the schema-v2.1 five-field access tuple. The scorer
    checks extraction, enum validity, tuple consistency, and configured minimum /
    maximum acceptable access bounds. It is not a replacement for manual audit of
    the model's substantive safety reasoning.
    """
    if dataset_version not in SCHEMA_V2_1_DATASET_VERSIONS:
        valid = ", ".join(sorted(SCHEMA_V2_1_DATASET_VERSIONS))
        raise ValueError(
            f"Unsupported schema-v2.1 scored dataset_version {dataset_version!r}. "
            f"Valid options: {valid}"
        )

    config = DATASET_CONFIGS[dataset_version]
    return Task(
        dataset=json_dataset(
            str(config["path"]),
            sample_fields=make_record_to_sample(str(config["prompt_style"])),
        ),
        solver=generate(),
        scorer=schema_v2_1_access_scorer(),
    )
