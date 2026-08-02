from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "log_prob"))

from analyse_action_logprob_cross_model import (  # noqa: E402
    AnalysisError,
    _boundary_rows,
    _boundary_summaries,
    _mean_median_range,
    _robustness_rows,
    _validate_result,
    _write_csv,
    _write_json,
    boundary_transition,
    load_manifest,
    pressure_to_resolution_ratio,
    sign_name,
    symmetric_relative_difference,
)


MANIFEST_PATH = ROOT / "experiments" / "action_logprob_cross_model_analysis_v0.json"


def synthetic_result(
    manifest: dict,
    model: dict,
    case: dict,
    *,
    token_offset: int = 0,
) -> dict:
    mappings = {}
    primary = {}
    for mapping_index, mapping in enumerate(manifest["mappings"]):
        conditions = {}
        margins = {
            "unresolved_neutral": -1.0 - mapping_index,
            "unresolved_directive": 1.0 - mapping_index,
            "resolved_neutral": 3.0 - mapping_index,
        }
        for condition_id, margin in margins.items():
            prompt = f"{case['case_key']}::{mapping['mapping_id']}::{condition_id}"
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            conditions[condition_id] = {
                "input_token_count": 100 + token_offset,
                "bounded": {
                    "semantic_role": "bounded",
                    "label": mapping["bounded_label"],
                    "token_id": 10 + token_offset,
                    "raw_logit": 0.0,
                    "restricted_two_label_probability": 0.5,
                },
                "broader": {
                    "semantic_role": "broader",
                    "label": mapping["broader_label"],
                    "token_id": 11 + token_offset,
                    "raw_logit": margin,
                    "restricted_two_label_probability": 0.5,
                },
                "broad_action_logit_margin": margin,
                "ordinary_greedy_generation": {
                    "text": mapping["broader_label"],
                    "conforms_to_nominated_labels": True,
                },
                "full_vocabulary_top_next_token": {
                    "token_id": 11 + token_offset,
                    "text": mapping["broader_label"],
                    "raw_logit": margin,
                },
                "condition_id": condition_id,
                "evidence_state": (
                    "resolved" if condition_id == "resolved_neutral" else "unresolved"
                ),
                "instruction": "Synthetic instruction",
                "prompt": prompt,
                "prompt_sha256": prompt_hash,
                "independently_justified_boundary": {
                    "semantic_action": "bounded",
                    "metadata_only_not_rendered": True,
                },
            }
        mappings[mapping["mapping_id"]] = {
            "bounded_label": mapping["bounded_label"],
            "broader_label": mapping["broader_label"],
            "presentation_order": [
                {
                    "semantic_role": role,
                    "label": mapping[f"{role}_label"],
                    "text": f"Synthetic {role}",
                }
                for role in mapping["presentation_roles"]
            ],
            "conditions": conditions,
        }
        primary[mapping["mapping_id"]] = {
            "directive_effect": 2.0,
            "resolution_effect": 4.0,
            "directive_effect_definition": "synthetic",
            "resolution_effect_definition": "synthetic",
        }
    model_metadata = {
        "identity": model["identity"],
        "requested_revision": model["revision"],
        "resolved_model_revision": model["revision"],
        "resolved_tokenizer_revision": (
            None
            if model["resolved_tokenizer_revision_policy"] == "legacy_null_only"
            else model["revision"]
        ),
    }
    if "revision_resolution_method" in model:
        model_metadata["revision_resolution_method"] = model[
            "revision_resolution_method"
        ]
    return {
        "schema_version": manifest["result_schema_version"],
        "created_at_utc": "2026-08-01T00:00:00+00:00",
        "dataset": {
            "path": "/synthetic/dataset.jsonl",
            "sha256": case["dataset_sha256"],
            "schema_version": manifest["dataset_schema_version"],
            "dataset_version": case["dataset_version"],
            "case_id": case["case_id"],
            "source_case_id": None,
        },
        "prompt_version": manifest["prompt_version"],
        "model": model_metadata,
        "runtime": copy.deepcopy(model["runtime"]),
        "semantic_action_roles": {
            "bounded": {"text": "Synthetic bounded"},
            "broader": {"text": "Synthetic broader"},
        },
        "mappings": mappings,
        "effects": {
            "primary_mapping_specific": primary,
            "secondary_summary": {},
        },
        "interpretation": {},
    }


class CrossModelFormulaTests(unittest.TestCase):
    def test_signs_and_boundary_transitions_include_zero(self) -> None:
        self.assertEqual(sign_name(-0.1), "negative")
        self.assertEqual(sign_name(0.0), "zero")
        self.assertEqual(sign_name(0.1), "positive")
        self.assertEqual(
            boundary_transition(-1.0, 2.0),
            {
                "source_sign": "negative",
                "target_sign": "positive",
                "transition": "negative_to_positive",
                "crosses_zero": True,
                "touches_or_leaves_zero": False,
            },
        )
        self.assertTrue(boundary_transition(-1.0, 0.0)["touches_or_leaves_zero"])

    def test_pressure_ratio_handles_defined_zero_and_near_zero(self) -> None:
        self.assertEqual(
            pressure_to_resolution_ratio(2.0, 4.0, near_zero_tolerance=1e-12),
            (0.5, "defined"),
        )
        self.assertEqual(
            pressure_to_resolution_ratio(2.0, 0.0, near_zero_tolerance=1e-12),
            (None, "undefined_zero_resolution"),
        )
        self.assertEqual(
            pressure_to_resolution_ratio(2.0, 1e-13, near_zero_tolerance=1e-12),
            (None, "undefined_near_zero_resolution"),
        )

    def test_ratio_summary_is_over_mapping_ratios(self) -> None:
        summary = _mean_median_range([0.5, 1.5, None, 1.0])
        self.assertEqual(summary["mean"], 1.0)
        self.assertEqual(summary["median"], 1.0)
        self.assertEqual(summary["minimum"], 0.5)
        self.assertEqual(summary["maximum"], 1.5)
        self.assertEqual(summary["defined_count"], 3)
        self.assertEqual(summary["undefined_count"], 1)

    def test_symmetric_relative_difference_handles_zero_zero(self) -> None:
        self.assertEqual(symmetric_relative_difference(2.0, 4.0), (2.0 / 3.0, "defined"))
        self.assertEqual(symmetric_relative_difference(0.0, 0.0), (None, "both_zero"))
        self.assertEqual(symmetric_relative_difference(0.0, 3.0), (2.0, "defined"))


class CrossModelValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(MANIFEST_PATH)
        cls.case = cls.manifest["cases"][0]

    def validate(self, payload: dict, model: dict, prompt_reference: dict | None = None):
        return _validate_result(
            payload,
            model_spec=model,
            case_spec=self.case,
            manifest=self.manifest,
            result_path=ROOT / "tmp" / "synthetic.json",
            prompt_reference={} if prompt_reference is None else prompt_reference,
        )

    def test_valid_synthetic_result_recomputes_twelve_margins_and_four_effects(self) -> None:
        model = self.manifest["models"][0]
        original = synthetic_result(self.manifest, model, self.case)
        before = json.dumps(original, sort_keys=True)
        margins, effects = self.validate(original, model)
        self.assertEqual(len(margins), 12)
        self.assertEqual(len(effects), 4)
        self.assertTrue(all(row["directive_effect"] == 2.0 for row in effects))
        self.assertTrue(all(row["resolution_effect"] == 4.0 for row in effects))
        self.assertEqual(before, json.dumps(original, sort_keys=True))

    def test_logical_prompts_match_across_native_tokenisation_differences(self) -> None:
        qwen = self.manifest["models"][0]
        mistral = self.manifest["models"][1]
        reference: dict = {}
        self.validate(synthetic_result(self.manifest, qwen, self.case), qwen, reference)
        margins, _ = self.validate(
            synthetic_result(self.manifest, mistral, self.case, token_offset=1000),
            mistral,
            reference,
        )
        self.assertEqual(len(margins), 12)

    def test_differing_logical_prompt_is_rejected(self) -> None:
        qwen = self.manifest["models"][0]
        mistral = self.manifest["models"][1]
        reference: dict = {}
        self.validate(synthetic_result(self.manifest, qwen, self.case), qwen, reference)
        changed = synthetic_result(self.manifest, mistral, self.case)
        condition = changed["mappings"]["bounded_A_broader_B"]["conditions"][
            "unresolved_neutral"
        ]
        condition["prompt"] += " changed"
        condition["prompt_sha256"] = hashlib.sha256(
            condition["prompt"].encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(AnalysisError, "logical prompt differs"):
            self.validate(changed, mistral, reference)

    def test_legacy_null_tokenizer_revision_policy_is_narrow(self) -> None:
        legacy = self.manifest["models"][0]
        payload = synthetic_result(self.manifest, legacy, self.case)
        self.validate(payload, legacy)
        payload["model"]["resolved_tokenizer_revision"] = legacy["revision"]
        with self.assertRaisesRegex(AnalysisError, "must remain null"):
            self.validate(payload, legacy)

        qwen14 = self.manifest["models"][2]
        payload = synthetic_result(self.manifest, qwen14, self.case)
        payload["model"]["resolved_tokenizer_revision"] = None
        with self.assertRaisesRegex(AnalysisError, "tokenizer revision"):
            self.validate(payload, qwen14)

    def test_qwen14_resolution_method_is_required(self) -> None:
        qwen14 = self.manifest["models"][2]
        payload = synthetic_result(self.manifest, qwen14, self.case)
        del payload["model"]["revision_resolution_method"]
        with self.assertRaisesRegex(AnalysisError, "resolution method"):
            self.validate(payload, qwen14)

    def test_invalid_provenance_structure_and_numbers_are_rejected(self) -> None:
        model = self.manifest["models"][0]
        mutations = {
            "schema": lambda p: p.__setitem__("schema_version", "wrong"),
            "revision": lambda p: p["model"].__setitem__("requested_revision", "f" * 40),
            "dataset": lambda p: p["dataset"].__setitem__("dataset_version", "wrong"),
            "prompt_version": lambda p: p.__setitem__("prompt_version", "wrong"),
            "runtime": lambda p: p["runtime"].__setitem__("dtype", "torch.float16"),
            "mapping": lambda p: p["mappings"].pop("bounded_A_broader_B"),
            "condition": lambda p: p["mappings"]["bounded_A_broader_B"]["conditions"].pop("resolved_neutral"),
            "nonfinite": lambda p: p["mappings"]["bounded_A_broader_B"]["conditions"]["unresolved_neutral"]["broader"].__setitem__("raw_logit", math.nan),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = synthetic_result(self.manifest, model, self.case)
                mutate(payload)
                with self.assertRaises(AnalysisError):
                    self.validate(payload, model)

    def test_stored_margin_and_effect_are_consistency_checks(self) -> None:
        model = self.manifest["models"][0]
        payload = synthetic_result(self.manifest, model, self.case)
        payload["mappings"]["bounded_A_broader_B"]["conditions"]["unresolved_neutral"][
            "broad_action_logit_margin"
        ] = 999.0
        with self.assertRaisesRegex(AnalysisError, "stored margin"):
            self.validate(payload, model)

        payload = synthetic_result(self.manifest, model, self.case)
        payload["effects"]["primary_mapping_specific"]["bounded_A_broader_B"][
            "directive_effect"
        ] = 999.0
        with self.assertRaisesRegex(AnalysisError, "stored directive_effect"):
            self.validate(payload, model)


class CrossModelOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(MANIFEST_PATH)
        cls.case = cls.manifest["cases"][0]
        cls.model = cls.manifest["models"][0]
        cls.margins, cls.effects = _validate_result(
            synthetic_result(cls.manifest, cls.model, cls.case),
            model_spec=cls.model,
            case_spec=cls.case,
            manifest=cls.manifest,
            result_path=ROOT / "tmp" / "synthetic.json",
            prompt_reference={},
        )

    def test_robustness_uses_exact_label_and_position_pairs(self) -> None:
        mini_manifest = {
            "models": [self.model],
            "cases": [self.case],
            "conditions": self.manifest["conditions"],
        }
        rows = _robustness_rows(self.margins, self.effects, mini_manifest)
        self.assertEqual(len(rows), 20)
        label = next(
            row
            for row in rows
            if row["dimension"] == "label_assignment"
            and row["pair_id"] == "labels_A_then_B"
            and row["metric"] == "directive_effect"
        )
        self.assertEqual(label["left_mapping"], "bounded_A_broader_B")
        self.assertEqual(label["right_mapping"], "broader_A_bounded_B")
        position = next(
            row
            for row in rows
            if row["dimension"] == "presentation_position"
            and row["pair_id"] == "bounded_A_broader_B"
            and row["metric"] == "resolution_effect"
        )
        self.assertEqual(
            position["right_mapping"], "bounded_A_broader_B__B_then_A"
        )

    def test_boundary_rows_and_summaries_are_explicit(self) -> None:
        mini_manifest = {
            "models": [self.model],
            "cases": [self.case],
            "mappings": self.manifest["mappings"],
        }
        rows = _boundary_rows(self.margins, mini_manifest)
        self.assertEqual(len(rows), 8)
        summaries = _boundary_summaries(rows, mini_manifest)
        self.assertTrue(any(row["effect_type"] == "directive" for row in summaries))
        self.assertTrue(any(row["aggregation_level"] == "model" for row in summaries))

    def test_json_and_csv_serialisation_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            root = Path(directory)
            value = {"z": 1.25, "a": [True, None]}
            _write_json(root / "one.json", value)
            _write_json(root / "two.json", value)
            self.assertEqual((root / "one.json").read_bytes(), (root / "two.json").read_bytes())
            rows = [{"name": "x", "value": 1.25, "missing": None}]
            _write_csv(root / "one.csv", rows, ("name", "value", "missing"))
            _write_csv(root / "two.csv", rows, ("name", "value", "missing"))
            self.assertEqual((root / "one.csv").read_bytes(), (root / "two.csv").read_bytes())

    def test_manifest_rejects_output_outside_fixed_analysis_root(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            path = Path(directory) / "manifest.json"
            manifest = copy.deepcopy(self.manifest)
            manifest["output_root"] = "tmp/not-the-approved-root"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(AnalysisError, "Output root"):
                load_manifest(path)

    def test_analysis_script_has_no_model_loading_imports(self) -> None:
        script = ROOT / "scripts" / "log_prob" / "analyse_action_logprob_cross_model.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(
            imported.isdisjoint(
                {"torch", "transformers", "huggingface_hub", "mistral_common"}
            )
        )


if __name__ == "__main__":
    unittest.main()
