from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backfill_inspect_logs_to_postgres import (  # noqa: E402
    choose_canonical_duplicate,
    extract_completion,
    is_promotable_sample,
    provider_from_model,
    repo_stem_label,
    sample_identifier,
)


class BackfillInspectLogsTest(unittest.TestCase):
    def test_extract_completion_prefers_output_completion(self) -> None:
        sample = SimpleNamespace(
            output=SimpleNamespace(completion="final answer"),
            messages=[{"role": "assistant", "content": "older answer"}],
        )

        self.assertEqual(extract_completion(sample), "final answer")

    def test_extract_completion_falls_back_to_last_assistant_message(self) -> None:
        sample = {
            "messages": [
                {"role": "assistant", "content": "first"},
                {"role": "user", "content": "pressure"},
                {"role": "assistant", "content": "second"},
            ]
        }

        self.assertEqual(extract_completion(sample), "second")

    def test_sample_identifier_uses_metadata_when_id_is_missing(self) -> None:
        sample = {"metadata": {"sample_id": "case-17", "case_id": "fallback"}}

        self.assertEqual(sample_identifier(sample), "case-17")

    def test_promotable_sample_requires_dataset_sample_and_response(self) -> None:
        summary = {"dataset_version": "v4", "sample_count": 1}

        self.assertEqual(is_promotable_sample({"id": "s1", "output": {"completion": "ok"}}, summary), (True, None))
        self.assertEqual(
            is_promotable_sample({"id": "s1", "output": {"completion": ""}}, summary),
            (False, "missing_final_response"),
        )
        self.assertEqual(
            is_promotable_sample({"id": "s1", "output": {"completion": "ok"}}, {"dataset_version": None}),
            (False, "missing_dataset_version"),
        )

    def test_run_label_uses_repo_relative_stem(self) -> None:
        self.assertEqual(repo_stem_label(ROOT / "logs" / "example.eval"), "logs/example")

    def test_provider_from_model_handles_namespaced_models(self) -> None:
        self.assertEqual(provider_from_model("openai/gpt-5.5"), "openai")
        self.assertEqual(provider_from_model("gpt-4.1"), "openai")
        self.assertEqual(provider_from_model("claude-sonnet-4"), "anthropic")

    def test_choose_canonical_duplicate_prefers_existing_non_backfill_manifest_row(self) -> None:
        rows = [
            {
                "experiment_pipeline_run_id": 178,
                "experiment_manifest_id": 200,
                "pipeline_run_key": "inspect-log-backfill-abc",
            },
            {
                "experiment_pipeline_run_id": 2,
                "experiment_manifest_id": 10,
                "pipeline_run_key": "pipeline-run-existing",
            },
        ]

        self.assertEqual(choose_canonical_duplicate(rows)["experiment_pipeline_run_id"], 2)


if __name__ == "__main__":
    unittest.main()
