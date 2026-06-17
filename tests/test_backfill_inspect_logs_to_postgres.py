from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backfill_inspect_logs_to_postgres import (  # noqa: E402
    backfill_run_metadata,
    choose_canonical_duplicate,
    diagnostics_dry_run,
    diagnostics_write_enabled,
    empty_diagnostics_summary,
    extract_completion,
    is_promotable_sample,
    provider_from_model,
    repo_stem_label,
    sample_identifier,
)
from moral_sycophancy_eval.diagnostics import normalise_diagnostics_config  # noqa: E402


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

    def test_diagnostics_without_write_mode_is_dry_run_only(self) -> None:
        args = SimpleNamespace(diagnostics=True, write=False, promote_operational=False)

        self.assertFalse(diagnostics_write_enabled(args))
        self.assertTrue(diagnostics_dry_run(args))

    def test_diagnostics_runs_for_existing_promote_write_mode(self) -> None:
        args = SimpleNamespace(diagnostics=True, write=False, promote_operational=True)

        self.assertTrue(diagnostics_write_enabled(args))
        self.assertFalse(diagnostics_dry_run(args))

    def test_diagnostics_summary_contains_required_counters(self) -> None:
        summary = empty_diagnostics_summary(requested=True, dry_run=True)

        self.assertEqual(
            set(summary),
            {
                "diagnostics_requested",
                "diagnostics_dry_run",
                "inspect_samples_inserted_or_updated",
                "response_diagnostics_upserted",
                "model_call_diagnostics_upserted",
                "model_call_diagnostics_exact_linked",
                "model_call_diagnostics_unverified",
            },
        )
        self.assertTrue(summary["diagnostics_requested"])
        self.assertTrue(summary["diagnostics_dry_run"])
        self.assertEqual(summary["response_diagnostics_upserted"], 0)

    def test_backfill_run_metadata_preserves_diagnostics_and_eval_log_source(self) -> None:
        diagnostics_config = normalise_diagnostics_config(None)
        metadata = backfill_run_metadata(
            {
                "path": ROOT / "logs" / "example.eval",
                "sha256": "abc123",
                "model_name": "openai/test",
                "task": "task.py@eval",
                "sample_count": 3,
            },
            diagnostics_config,
        )

        self.assertEqual(metadata["backfill_source"], "inspect_eval_log")
        self.assertEqual(metadata["source_log_path"], "logs/example.eval")
        self.assertEqual(metadata["eval_log_path"], "logs/example.eval")
        self.assertEqual(metadata["source_log_sha256"], "abc123")
        self.assertEqual(metadata["diagnostics"], diagnostics_config)
        self.assertEqual(metadata["diagnostics_backfill_source"], "existing_eval_log")


if __name__ == "__main__":
    unittest.main()
