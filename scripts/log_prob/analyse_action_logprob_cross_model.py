#!/usr/bin/env python
"""Deterministic, model-free analysis of the nine canonical action-logprob results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "experiments" / "action_logprob_cross_model_analysis_v0.json"
REQUIRED_OUTPUT_ROOT = ROOT / "tmp" / "action_logprob_mvp" / "analysis" / "cross_model_v0"
ANALYSIS_SCHEMA_VERSION = "action_logprob_cross_model_analysis_v0"
MANIFEST_SCHEMA_VERSION = "action_logprob_cross_model_analysis_manifest_v0"
SIGN_NAMES = ("negative", "zero", "positive")

LABEL_SWAP_PAIRS = (
    ("labels_A_then_B", "bounded_A_broader_B", "broader_A_bounded_B"),
    (
        "labels_B_then_A",
        "bounded_A_broader_B__B_then_A",
        "broader_A_bounded_B__B_then_A",
    ),
)
POSITION_SWAP_PAIRS = (
    (
        "bounded_A_broader_B",
        "bounded_A_broader_B",
        "bounded_A_broader_B__B_then_A",
    ),
    (
        "broader_A_bounded_B",
        "broader_A_bounded_B",
        "broader_A_bounded_B__B_then_A",
    ),
)


class AnalysisError(RuntimeError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse the nine pinned action-logprob result artefacts without models."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args(argv)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(value: str, *, root: Path = ROOT) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise AnalysisError(f"Path escapes repository root: {value}") from exc
    return path


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON root must be an object: {path}")
    return value


def load_manifest(path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> dict[str, Any]:
    manifest = _load_json(path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise AnalysisError("Unexpected cross-model analysis manifest schema")
    output_root = _repo_path(str(manifest.get("output_root")), root=root)
    required = (
        root / "tmp" / "action_logprob_mvp" / "analysis" / "cross_model_v0"
    ).resolve()
    if output_root != required:
        raise AnalysisError(f"Output root must be exactly {required}")
    if len(manifest.get("models", [])) != 3 or len(manifest.get("cases", [])) != 3:
        raise AnalysisError("Manifest must pin exactly three models and three cases")
    return manifest


def _require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisError(f"{context} must be an object")
    return value


def _require_keys(value: Mapping[str, Any], keys: Iterable[str], context: str) -> None:
    missing = sorted(set(keys) - set(value))
    if missing:
        raise AnalysisError(f"{context} is missing fields: {missing}")


def _require_exact_order(value: Mapping[str, Any], expected: Sequence[str], context: str) -> None:
    if list(value) != list(expected):
        raise AnalysisError(f"{context} must have exact ordered keys {list(expected)}")


def _finite(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError(f"{context} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise AnalysisError(f"{context} must be finite")
    return result


def sign_name(value: float) -> str:
    if value < 0:
        return "negative"
    if value > 0:
        return "positive"
    return "zero"


def pressure_to_resolution_ratio(
    directive_effect: float,
    resolution_effect: float,
    *,
    near_zero_tolerance: float,
) -> tuple[float | None, str]:
    if resolution_effect == 0:
        return None, "undefined_zero_resolution"
    if abs(resolution_effect) <= near_zero_tolerance:
        return None, "undefined_near_zero_resolution"
    return directive_effect / resolution_effect, "defined"


def symmetric_relative_difference(x: float, y: float) -> tuple[float | None, str]:
    denominator = abs(x) + abs(y)
    if denominator == 0:
        return None, "both_zero"
    return 2.0 * abs(x - y) / denominator, "defined"


def boundary_transition(source: float, target: float) -> dict[str, Any]:
    source_sign = sign_name(source)
    target_sign = sign_name(target)
    return {
        "source_sign": source_sign,
        "target_sign": target_sign,
        "transition": f"{source_sign}_to_{target_sign}",
        "crosses_zero": (
            (source_sign == "negative" and target_sign == "positive")
            or (source_sign == "positive" and target_sign == "negative")
        ),
        "touches_or_leaves_zero": source_sign == "zero" or target_sign == "zero",
    }


def _mean_median_range(values: Sequence[float | None]) -> dict[str, Any]:
    defined = [float(value) for value in values if value is not None]
    return {
        "mean": statistics.fmean(defined) if defined else None,
        "median": statistics.median(defined) if defined else None,
        "minimum": min(defined) if defined else None,
        "maximum": max(defined) if defined else None,
        "defined_count": len(defined),
        "undefined_count": len(values) - len(defined),
    }


def _verify_manifest_file(
    directory: Path, manifest_path: Path, expected_entries: int, *, root: Path = ROOT
) -> dict[str, Any]:
    pattern = re.compile(r"^([0-9a-f]{64})\s+(.+)$")
    entries = []
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = pattern.fullmatch(line.strip())
        if not match:
            raise AnalysisError(
                f"Malformed artefact manifest line {line_number}: {manifest_path}"
            )
        expected_hash, recorded_path = match.groups()
        target = directory / Path(recorded_path).name
        if not target.is_file():
            raise AnalysisError(f"Manifest target not found: {target}")
        actual_hash = sha256_path(target)
        if actual_hash != expected_hash:
            raise AnalysisError(f"Manifest hash mismatch: {target}")
        entries.append(
            {
                "path": target.relative_to(root).as_posix(),
                "sha256": actual_hash,
            }
        )
    if len(entries) != expected_entries:
        raise AnalysisError(
            f"{manifest_path} has {len(entries)} entries; expected {expected_entries}"
        )
    return {
        "manifest": manifest_path.relative_to(root).as_posix(),
        "manifest_sha256": sha256_path(manifest_path),
        "entry_count": len(entries),
        "entries": entries,
    }


def _mapping_specs(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["mapping_id"]: item for item in manifest["mappings"]}


def _validate_result(
    payload: dict[str, Any],
    *,
    model_spec: Mapping[str, Any],
    case_spec: Mapping[str, Any],
    manifest: Mapping[str, Any],
    result_path: Path,
    prompt_reference: dict[tuple[str, str, str], tuple[str, str]],
    root: Path = ROOT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    context = f"{model_spec['model_key']}/{case_spec['case_key']}"
    _require_keys(
        payload,
        (
            "schema_version",
            "created_at_utc",
            "dataset",
            "prompt_version",
            "model",
            "runtime",
            "semantic_action_roles",
            "mappings",
            "effects",
            "interpretation",
        ),
        context,
    )
    if payload["schema_version"] != manifest["result_schema_version"]:
        raise AnalysisError(f"{context} has unexpected result schema")
    try:
        datetime.fromisoformat(str(payload["created_at_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalysisError(f"{context} has invalid created_at_utc") from exc

    dataset = _require_object(payload["dataset"], f"{context}.dataset")
    expected_dataset = {
        "schema_version": manifest["dataset_schema_version"],
        "dataset_version": case_spec["dataset_version"],
        "case_id": case_spec["case_id"],
        "sha256": case_spec["dataset_sha256"],
    }
    for field, expected in expected_dataset.items():
        if dataset.get(field) != expected:
            raise AnalysisError(f"{context}.dataset.{field} does not match manifest")
    if payload["prompt_version"] != manifest["prompt_version"]:
        raise AnalysisError(f"{context} has unexpected prompt version")

    model = _require_object(payload["model"], f"{context}.model")
    if model.get("identity") != model_spec["identity"]:
        raise AnalysisError(f"{context} has unexpected model identity")
    for field in ("requested_revision", "resolved_model_revision"):
        if model.get(field) != model_spec["revision"]:
            raise AnalysisError(f"{context}.model.{field} does not match exact revision")
    tokenizer_revision = model.get("resolved_tokenizer_revision")
    policy = model_spec["resolved_tokenizer_revision_policy"]
    if policy == "must_match_revision" and tokenizer_revision != model_spec["revision"]:
        raise AnalysisError(f"{context} tokenizer revision does not match exact revision")
    if policy == "legacy_null_only" and tokenizer_revision is not None:
        raise AnalysisError(f"{context} legacy tokenizer revision must remain null")
    expected_resolution_method = model_spec.get("revision_resolution_method")
    if expected_resolution_method is not None and model.get(
        "revision_resolution_method"
    ) != expected_resolution_method:
        raise AnalysisError(f"{context} revision resolution method mismatch")

    runtime = _require_object(payload["runtime"], f"{context}.runtime")
    for field, expected in model_spec["runtime"].items():
        if runtime.get(field) != expected:
            raise AnalysisError(f"{context}.runtime.{field} does not match manifest")

    expected_conditions = manifest["conditions"]
    mapping_specs = _mapping_specs(manifest)
    mappings = _require_object(payload["mappings"], f"{context}.mappings")
    semantic_roles = _require_object(
        payload["semantic_action_roles"], f"{context}.semantic_action_roles"
    )
    if set(semantic_roles) != {"bounded", "broader"}:
        raise AnalysisError(f"{context} must define bounded and broader semantic roles")
    _require_exact_order(mappings, list(mapping_specs), f"{context}.mappings")
    tolerance = float(manifest["numeric_absolute_tolerance"])
    margin_rows: list[dict[str, Any]] = []
    effect_rows: list[dict[str, Any]] = []

    for mapping_id, mapping_spec in mapping_specs.items():
        mapping = _require_object(mappings[mapping_id], f"{context}/{mapping_id}")
        _require_keys(
            mapping,
            ("bounded_label", "broader_label", "presentation_order", "conditions"),
            f"{context}/{mapping_id}",
        )
        for field in ("bounded_label", "broader_label"):
            if mapping[field] != mapping_spec[field]:
                raise AnalysisError(f"{context}/{mapping_id} has wrong {field}")
        presentation = mapping["presentation_order"]
        if not isinstance(presentation, list) or len(presentation) != 2:
            raise AnalysisError(f"{context}/{mapping_id} has invalid presentation order")
        roles = [item.get("semantic_role") for item in presentation if isinstance(item, dict)]
        labels = [item.get("label") for item in presentation if isinstance(item, dict)]
        expected_labels = [
            mapping_spec[f"{role}_label"] for role in mapping_spec["presentation_roles"]
        ]
        if roles != mapping_spec["presentation_roles"] or labels != expected_labels:
            raise AnalysisError(f"{context}/{mapping_id} presentation metadata mismatch")

        conditions = _require_object(mapping["conditions"], f"{context}/{mapping_id}.conditions")
        _require_exact_order(conditions, expected_conditions, f"{context}/{mapping_id}.conditions")
        margins: dict[str, float] = {}
        for condition_id in expected_conditions:
            condition_context = f"{context}/{mapping_id}/{condition_id}"
            condition = _require_object(conditions[condition_id], condition_context)
            _require_keys(
                condition,
                (
                    "bounded",
                    "broader",
                    "input_token_count",
                    "broad_action_logit_margin",
                    "ordinary_greedy_generation",
                    "full_vocabulary_top_next_token",
                    "condition_id",
                    "evidence_state",
                    "instruction",
                    "prompt",
                    "prompt_sha256",
                    "independently_justified_boundary",
                ),
                condition_context,
            )
            if condition["condition_id"] != condition_id:
                raise AnalysisError(f"{condition_context} condition_id mismatch")
            if (
                isinstance(condition["input_token_count"], bool)
                or not isinstance(condition["input_token_count"], int)
                or condition["input_token_count"] <= 0
            ):
                raise AnalysisError(f"{condition_context} input_token_count must be positive")
            expected_evidence = "resolved" if condition_id == "resolved_neutral" else "unresolved"
            if condition["evidence_state"] != expected_evidence:
                raise AnalysisError(f"{condition_context} evidence_state mismatch")
            prompt = condition["prompt"]
            if not isinstance(prompt, str) or not prompt:
                raise AnalysisError(f"{condition_context} prompt must be non-empty text")
            if not isinstance(condition["instruction"], str):
                raise AnalysisError(f"{condition_context} instruction must be text")
            _require_object(
                condition["independently_justified_boundary"],
                f"{condition_context}.independently_justified_boundary",
            )
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if condition["prompt_sha256"] != prompt_hash:
                raise AnalysisError(f"{condition_context} pre-template prompt hash mismatch")
            prompt_key = (case_spec["case_key"], mapping_id, condition_id)
            if prompt_key in prompt_reference and prompt_reference[prompt_key] != (prompt, prompt_hash):
                raise AnalysisError(
                    f"{condition_context} frozen logical prompt differs across models"
                )
            prompt_reference.setdefault(prompt_key, (prompt, prompt_hash))

            bounded = _require_object(condition["bounded"], f"{condition_context}.bounded")
            broader = _require_object(condition["broader"], f"{condition_context}.broader")
            for item, role, label in (
                (bounded, "bounded", mapping_spec["bounded_label"]),
                (broader, "broader", mapping_spec["broader_label"]),
            ):
                if item.get("semantic_role") != role or item.get("label") != label:
                    raise AnalysisError(f"{condition_context} action role/label mismatch")
                if isinstance(item.get("token_id"), bool) or not isinstance(item.get("token_id"), int):
                    raise AnalysisError(f"{condition_context} token_id must be an integer")
                probability = _finite(
                    item.get("restricted_two_label_probability"),
                    f"{condition_context}.{role}.restricted_two_label_probability",
                )
                if not 0.0 <= probability <= 1.0:
                    raise AnalysisError(f"{condition_context} restricted probability outside [0, 1]")
            bounded_logit = _finite(bounded.get("raw_logit"), f"{condition_context}.bounded.raw_logit")
            broader_logit = _finite(broader.get("raw_logit"), f"{condition_context}.broader.raw_logit")
            margin = broader_logit - bounded_logit
            stored_margin = _finite(
                condition.get("broad_action_logit_margin"),
                f"{condition_context}.broad_action_logit_margin",
            )
            if not math.isclose(margin, stored_margin, rel_tol=0.0, abs_tol=tolerance):
                raise AnalysisError(f"{condition_context} stored margin does not recompute")
            probability_sum = (
                float(bounded["restricted_two_label_probability"])
                + float(broader["restricted_two_label_probability"])
            )
            if not math.isclose(probability_sum, 1.0, rel_tol=0.0, abs_tol=1e-6):
                raise AnalysisError(f"{condition_context} restricted probabilities do not sum to one")
            top = _require_object(
                condition["full_vocabulary_top_next_token"],
                f"{condition_context}.full_vocabulary_top_next_token",
            )
            _finite(top.get("raw_logit"), f"{condition_context}.top.raw_logit")
            if isinstance(top.get("token_id"), bool) or not isinstance(
                top.get("token_id"), int
            ):
                raise AnalysisError(f"{condition_context} top token_id must be an integer")
            if not isinstance(top.get("text"), str):
                raise AnalysisError(f"{condition_context} top token text must be text")
            generation = _require_object(
                condition["ordinary_greedy_generation"],
                f"{condition_context}.ordinary_greedy_generation",
            )
            generation_text = generation.get("text")
            conforms = generation.get("conforms_to_nominated_labels")
            if not isinstance(generation_text, str) or not isinstance(conforms, bool):
                raise AnalysisError(f"{condition_context} generation fields have wrong types")
            inferred_conformance = generation_text in {
                mapping_spec["bounded_label"],
                mapping_spec["broader_label"],
            }
            if conforms != inferred_conformance:
                raise AnalysisError(f"{condition_context} generation conformance mismatch")

            margins[condition_id] = margin
            margin_rows.append(
                {
                    "model_key": model_spec["model_key"],
                    "model": model_spec["display_name"],
                    "case_key": case_spec["case_key"],
                    "case": case_spec["display_name"],
                    "case_id": case_spec["case_id"],
                    "mapping_id": mapping_id,
                    "condition_id": condition_id,
                    "bounded_label": mapping_spec["bounded_label"],
                    "broader_label": mapping_spec["broader_label"],
                    "semantic_margin": margin,
                    "margin_sign": sign_name(margin),
                    "ordinary_generation_text": generation_text,
                    "ordinary_generation_conforms": conforms,
                    "prompt_sha256": prompt_hash,
                    "source_path": result_path.relative_to(root).as_posix(),
                }
            )

        directive_effect = margins["unresolved_directive"] - margins["unresolved_neutral"]
        resolution_effect = margins["resolved_neutral"] - margins["unresolved_neutral"]
        ratio, ratio_status = pressure_to_resolution_ratio(
            directive_effect,
            resolution_effect,
            near_zero_tolerance=float(
                manifest["near_zero_resolution_effect_absolute_tolerance"]
            ),
        )
        stored_primary = payload["effects"]["primary_mapping_specific"][mapping_id]
        for name, recomputed in (
            ("directive_effect", directive_effect),
            ("resolution_effect", resolution_effect),
        ):
            stored = _finite(stored_primary.get(name), f"{context}.effects.{mapping_id}.{name}")
            if not math.isclose(stored, recomputed, rel_tol=0.0, abs_tol=tolerance):
                raise AnalysisError(f"{context}/{mapping_id} stored {name} does not recompute")
        effect_rows.append(
            {
                "model_key": model_spec["model_key"],
                "model": model_spec["display_name"],
                "case_key": case_spec["case_key"],
                "case": case_spec["display_name"],
                "case_id": case_spec["case_id"],
                "mapping_id": mapping_id,
                "directive_effect": directive_effect,
                "directive_sign": sign_name(directive_effect),
                "resolution_effect": resolution_effect,
                "resolution_sign": sign_name(resolution_effect),
                "pressure_to_resolution_ratio": ratio,
                "ratio_status": ratio_status,
            }
        )
    return margin_rows, effect_rows


def _summaries(
    margin_rows: list[dict[str, Any]], effect_rows: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    case_summaries = []
    model_summaries = []
    sign_counts = []
    margin_summaries = []
    conformance = []
    cases = [case["case_key"] for case in manifest["cases"]]
    conditions = manifest["conditions"]
    for model in manifest["models"]:
        model_key = model["model_key"]
        model_margins = [row for row in margin_rows if row["model_key"] == model_key]
        model_effects = [row for row in effect_rows if row["model_key"] == model_key]
        for case_key in cases:
            rows = [row for row in model_margins if row["case_key"] == case_key]
            effects = [row for row in model_effects if row["case_key"] == case_key]
            ratio_stats = _mean_median_range(
                [row["pressure_to_resolution_ratio"] for row in effects]
            )
            case_summaries.append(
                {
                    "model_key": model_key,
                    "model": model["display_name"],
                    "case_key": case_key,
                    "case": rows[0]["case"],
                    "mapping_count": len(effects),
                    "mean_directive_effect": statistics.fmean(
                        row["directive_effect"] for row in effects
                    ),
                    "median_directive_effect": statistics.median(
                        row["directive_effect"] for row in effects
                    ),
                    "minimum_directive_effect": min(row["directive_effect"] for row in effects),
                    "maximum_directive_effect": max(row["directive_effect"] for row in effects),
                    "positive_directive_effects": sum(
                        row["directive_effect"] > 0 for row in effects
                    ),
                    "mean_resolution_effect": statistics.fmean(
                        row["resolution_effect"] for row in effects
                    ),
                    "median_resolution_effect": statistics.median(
                        row["resolution_effect"] for row in effects
                    ),
                    "minimum_resolution_effect": min(row["resolution_effect"] for row in effects),
                    "maximum_resolution_effect": max(row["resolution_effect"] for row in effects),
                    "positive_resolution_effects": sum(
                        row["resolution_effect"] > 0 for row in effects
                    ),
                    **{f"ratio_{key}": value for key, value in ratio_stats.items()},
                    "conforming_generations": sum(
                        row["ordinary_generation_conforms"] for row in rows
                    ),
                    "generation_count": len(rows),
                }
            )
            conformance.append(
                {
                    "aggregation_level": "case",
                    "model_key": model_key,
                    "model": model["display_name"],
                    "case_key": case_key,
                    "case": rows[0]["case"],
                    "conforming": sum(row["ordinary_generation_conforms"] for row in rows),
                    "total": len(rows),
                }
            )
            for condition in conditions:
                values = [row for row in rows if row["condition_id"] == condition]
                counts = Counter(row["margin_sign"] for row in values)
                numeric_values = [row["semantic_margin"] for row in values]
                margin_summaries.append(
                    {
                        "aggregation_level": "case",
                        "model_key": model_key,
                        "model": model["display_name"],
                        "case_key": case_key,
                        "case": rows[0]["case"],
                        "condition_id": condition,
                        "mean": statistics.fmean(numeric_values),
                        "median": statistics.median(numeric_values),
                        "minimum": min(numeric_values),
                        "maximum": max(numeric_values),
                        "count": len(numeric_values),
                    }
                )
                sign_counts.append(
                    {
                        "aggregation_level": "case",
                        "model_key": model_key,
                        "model": model["display_name"],
                        "case_key": case_key,
                        "case": rows[0]["case"],
                        "condition_id": condition,
                        **{name: counts[name] for name in SIGN_NAMES},
                        "total": len(values),
                    }
                )

        ratio_stats = _mean_median_range(
            [row["pressure_to_resolution_ratio"] for row in model_effects]
        )
        model_summaries.append(
            {
                "model_key": model_key,
                "model": model["display_name"],
                "case_count": len(cases),
                "mapping_effect_count": len(model_effects),
                "mean_directive_effect": statistics.fmean(
                    row["directive_effect"] for row in model_effects
                ),
                "median_directive_effect": statistics.median(
                    row["directive_effect"] for row in model_effects
                ),
                "minimum_directive_effect": min(
                    row["directive_effect"] for row in model_effects
                ),
                "maximum_directive_effect": max(
                    row["directive_effect"] for row in model_effects
                ),
                "positive_directive_effects": sum(
                    row["directive_effect"] > 0 for row in model_effects
                ),
                "mean_resolution_effect": statistics.fmean(
                    row["resolution_effect"] for row in model_effects
                ),
                "median_resolution_effect": statistics.median(
                    row["resolution_effect"] for row in model_effects
                ),
                "minimum_resolution_effect": min(
                    row["resolution_effect"] for row in model_effects
                ),
                "maximum_resolution_effect": max(
                    row["resolution_effect"] for row in model_effects
                ),
                "positive_resolution_effects": sum(
                    row["resolution_effect"] > 0 for row in model_effects
                ),
                **{f"ratio_{key}": value for key, value in ratio_stats.items()},
                "conforming_generations": sum(
                    row["ordinary_generation_conforms"] for row in model_margins
                ),
                "generation_count": len(model_margins),
            }
        )
        conformance.append(
            {
                "aggregation_level": "model",
                "model_key": model_key,
                "model": model["display_name"],
                "case_key": "",
                "case": "",
                "conforming": sum(
                    row["ordinary_generation_conforms"] for row in model_margins
                ),
                "total": len(model_margins),
            }
        )
        for condition in conditions:
            values = [row for row in model_margins if row["condition_id"] == condition]
            counts = Counter(row["margin_sign"] for row in values)
            numeric_values = [row["semantic_margin"] for row in values]
            margin_summaries.append(
                {
                    "aggregation_level": "model",
                    "model_key": model_key,
                    "model": model["display_name"],
                    "case_key": "",
                    "case": "",
                    "condition_id": condition,
                    "mean": statistics.fmean(numeric_values),
                    "median": statistics.median(numeric_values),
                    "minimum": min(numeric_values),
                    "maximum": max(numeric_values),
                    "count": len(numeric_values),
                }
            )
            sign_counts.append(
                {
                    "aggregation_level": "model",
                    "model_key": model_key,
                    "model": model["display_name"],
                    "case_key": "",
                    "case": "",
                    "condition_id": condition,
                    **{name: counts[name] for name in SIGN_NAMES},
                    "total": len(values),
                }
            )
    return {
        "case_summaries": case_summaries,
        "model_summaries": model_summaries,
        "condition_sign_counts": sign_counts,
        "condition_margin_summaries": margin_summaries,
        "generation_conformance": conformance,
    }


def _boundary_rows(
    margin_rows: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    by_key = {
        (row["model_key"], row["case_key"], row["mapping_id"], row["condition_id"]): row
        for row in margin_rows
    }
    rows = []
    for model in manifest["models"]:
        for case in manifest["cases"]:
            for mapping in manifest["mappings"]:
                base_key = (model["model_key"], case["case_key"], mapping["mapping_id"])
                source = by_key[(*base_key, "unresolved_neutral")]["semantic_margin"]
                for effect_type, target_condition in (
                    ("directive", "unresolved_directive"),
                    ("resolution", "resolved_neutral"),
                ):
                    target = by_key[(*base_key, target_condition)]["semantic_margin"]
                    rows.append(
                        {
                            "model_key": model["model_key"],
                            "model": model["display_name"],
                            "case_key": case["case_key"],
                            "case": case["display_name"],
                            "mapping_id": mapping["mapping_id"],
                            "effect_type": effect_type,
                            "source_condition": "unresolved_neutral",
                            "target_condition": target_condition,
                            "source_margin": source,
                            "target_margin": target,
                            **boundary_transition(source, target),
                        }
                    )
    return rows


def _boundary_summaries(
    rows: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    summaries = []
    for model in manifest["models"]:
        model_rows = [row for row in rows if row["model_key"] == model["model_key"]]
        groups = [
            ("case", case["case_key"], case["display_name"])
            for case in manifest["cases"]
        ] + [("model", "", "")]
        for aggregation_level, case_key, case_name in groups:
            selected = (
                [row for row in model_rows if row["case_key"] == case_key]
                if aggregation_level == "case"
                else model_rows
            )
            for effect_type in ("directive", "resolution"):
                effect_rows = [
                    row for row in selected if row["effect_type"] == effect_type
                ]
                transitions = Counter(row["transition"] for row in effect_rows)
                for source_sign in SIGN_NAMES:
                    for target_sign in SIGN_NAMES:
                        transition = f"{source_sign}_to_{target_sign}"
                        count = transitions[transition]
                        if not count:
                            continue
                        summaries.append(
                            {
                                "aggregation_level": aggregation_level,
                                "model_key": model["model_key"],
                                "model": model["display_name"],
                                "case_key": case_key,
                                "case": case_name,
                                "effect_type": effect_type,
                                "transition": transition,
                                "count": count,
                                "total": len(effect_rows),
                                "crosses_zero_count": sum(
                                    row["crosses_zero"] for row in effect_rows
                                ),
                                "touches_or_leaves_zero_count": sum(
                                    row["touches_or_leaves_zero"] for row in effect_rows
                                ),
                            }
                        )
    return summaries


def _robustness_rows(
    margin_rows: list[dict[str, Any]],
    effect_rows: list[dict[str, Any]],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    margin_values = {
        (row["model_key"], row["case_key"], row["mapping_id"], row["condition_id"]): row[
            "semantic_margin"
        ]
        for row in margin_rows
    }
    effect_values = {
        (row["model_key"], row["case_key"], row["mapping_id"], metric): row[metric]
        for row in effect_rows
        for metric in ("directive_effect", "resolution_effect")
    }
    rows = []
    for model in manifest["models"]:
        for case in manifest["cases"]:
            metrics = [("semantic_margin", condition) for condition in manifest["conditions"]]
            metrics += [("directive_effect", ""), ("resolution_effect", "")]
            for metric, condition in metrics:
                for dimension, pairs in (
                    ("label_assignment", LABEL_SWAP_PAIRS),
                    ("presentation_position", POSITION_SWAP_PAIRS),
                ):
                    for pair_id, left_mapping, right_mapping in pairs:
                        if metric == "semantic_margin":
                            left = margin_values[
                                (model["model_key"], case["case_key"], left_mapping, condition)
                            ]
                            right = margin_values[
                                (model["model_key"], case["case_key"], right_mapping, condition)
                            ]
                        else:
                            left = effect_values[
                                (model["model_key"], case["case_key"], left_mapping, metric)
                            ]
                            right = effect_values[
                                (model["model_key"], case["case_key"], right_mapping, metric)
                            ]
                        relative, status = symmetric_relative_difference(left, right)
                        rows.append(
                            {
                                "model_key": model["model_key"],
                                "model": model["display_name"],
                                "case_key": case["case_key"],
                                "case": case["display_name"],
                                "dimension": dimension,
                                "pair_id": pair_id,
                                "metric": metric,
                                "condition_id": condition,
                                "left_mapping": left_mapping,
                                "right_mapping": right_mapping,
                                "left_value": left,
                                "right_value": right,
                                "left_sign": sign_name(left),
                                "right_sign": sign_name(right),
                                "sign_agreement": sign_name(left) == sign_name(right),
                                "symmetric_relative_difference": relative,
                                "comparison_status": status,
                            }
                        )
    return rows


def _regression_checks(
    summaries: Mapping[str, list[dict[str, Any]]],
    sign_counts: list[dict[str, Any]],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    tolerance = float(manifest["numeric_absolute_tolerance"])
    summaries_by_model = {row["model_key"]: row for row in summaries["model_summaries"]}
    checks = []
    for model in manifest["models"]:
        actual = summaries_by_model[model["model_key"]]
        expected = model["regression_expectations"]
        for expected_name, actual_name in (
            ("positive_directive_effects", "positive_directive_effects"),
            ("positive_resolution_effects", "positive_resolution_effects"),
            ("conforming_generations", "conforming_generations"),
        ):
            passed = actual[actual_name] == expected[expected_name]
            checks.append(
                {
                    "model_key": model["model_key"],
                    "check": expected_name,
                    "expected": expected[expected_name],
                    "actual": actual[actual_name],
                    "passed": passed,
                }
            )
        numeric_fields = (
            "mean_directive_effect",
            "mean_resolution_effect",
            "directive_effect_minimum",
            "directive_effect_maximum",
            "resolution_effect_minimum",
            "resolution_effect_maximum",
        )
        actual_field_map = {
            "directive_effect_minimum": "minimum_directive_effect",
            "directive_effect_maximum": "maximum_directive_effect",
            "resolution_effect_minimum": "minimum_resolution_effect",
            "resolution_effect_maximum": "maximum_resolution_effect",
        }
        for field in numeric_fields:
            if field not in expected:
                continue
            actual_value = actual[actual_field_map.get(field, field)]
            passed = math.isclose(
                actual_value, expected[field], rel_tol=0.0, abs_tol=tolerance
            )
            checks.append(
                {
                    "model_key": model["model_key"],
                    "check": field,
                    "expected": expected[field],
                    "actual": actual_value,
                    "absolute_tolerance": tolerance,
                    "passed": passed,
                }
            )
        for condition, expected_counts in expected.get("condition_sign_counts", {}).items():
            actual_counts = next(
                row
                for row in sign_counts
                if row["aggregation_level"] == "model"
                and row["model_key"] == model["model_key"]
                and row["condition_id"] == condition
            )
            for sign in SIGN_NAMES:
                checks.append(
                    {
                        "model_key": model["model_key"],
                        "check": f"{condition}_{sign}_margin_count",
                        "expected": expected_counts[sign],
                        "actual": actual_counts[sign],
                        "passed": actual_counts[sign] == expected_counts[sign],
                    }
                )
    failures = [check for check in checks if not check["passed"]]
    if failures:
        raise AnalysisError(f"Regression checks failed: {failures}")
    return checks


def analyse(manifest: Mapping[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    bundle_checks = []
    for item in manifest.get("bundle_checks", []):
        path = _repo_path(item["path"], root=root)
        actual = sha256_path(path)
        if actual != item["sha256"]:
            raise AnalysisError(f"Bundle hash mismatch: {path}")
        bundle_checks.append({"path": item["path"], "sha256": actual})

    artefact_checks = []
    for item in manifest.get("artifact_manifest_checks", []):
        directory = _repo_path(item["directory"], root=root)
        artefact_checks.append(
            _verify_manifest_file(
                directory,
                directory / item["manifest"],
                item["expected_entries"],
                root=root,
            )
        )

    for case in manifest["cases"]:
        dataset_path = _repo_path(case["dataset_path"], root=root)
        if sha256_path(dataset_path) != case["dataset_sha256"]:
            raise AnalysisError(f"Frozen dataset hash mismatch: {dataset_path}")

    prompt_reference: dict[tuple[str, str, str], tuple[str, str]] = {}
    margin_rows = []
    effect_rows = []
    source_files = []
    cases_by_key = {case["case_key"]: case for case in manifest["cases"]}
    for model in manifest["models"]:
        if list(model["results"]) != list(cases_by_key):
            raise AnalysisError(f"{model['model_key']} does not have exactly three ordered cases")
        for case_key, result_spec in model["results"].items():
            result_path = _repo_path(result_spec["path"], root=root)
            actual_hash = sha256_path(result_path)
            if actual_hash != result_spec["sha256"]:
                raise AnalysisError(f"Canonical result hash mismatch: {result_path}")
            source_files.append(
                {
                    "model_key": model["model_key"],
                    "case_key": case_key,
                    "path": result_spec["path"],
                    "sha256": actual_hash,
                }
            )
            margins, effects = _validate_result(
                _load_json(result_path),
                model_spec=model,
                case_spec=cases_by_key[case_key],
                manifest=manifest,
                result_path=result_path,
                prompt_reference=prompt_reference,
                root=root,
            )
            margin_rows.extend(margins)
            effect_rows.extend(effects)

    summaries = _summaries(margin_rows, effect_rows, manifest)
    boundaries = _boundary_rows(margin_rows, manifest)
    boundary_summaries = _boundary_summaries(boundaries, manifest)
    robustness = _robustness_rows(margin_rows, effect_rows, manifest)
    checks = _regression_checks(
        summaries, summaries["condition_sign_counts"], manifest
    )
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_version": manifest["analysis_version"],
        "definitions": {
            "semantic_margin": "broader-action raw logit minus bounded-action raw logit",
            "directive_effect": "unresolved-directive margin minus unresolved-neutral margin",
            "resolution_effect": "resolved-neutral margin minus unresolved-neutral margin",
            "pressure_to_resolution_ratio": "mapping-specific directive effect divided by mapping-specific resolution effect",
            "ratio_aggregation": "arithmetic summaries of defined mapping-specific ratios; never a ratio of aggregate mean effects",
            "symmetric_relative_difference": "2 * abs(x - y) / (abs(x) + abs(y))",
        },
        "source_files": source_files,
        "bundle_checks": bundle_checks,
        "artifact_manifest_checks": artefact_checks,
        "condition_margins": margin_rows,
        "mapping_effects": effect_rows,
        "boundary_crossings": boundaries,
        "boundary_crossing_summaries": boundary_summaries,
        "mapping_robustness": robustness,
        "regression_checks": checks,
        **summaries,
    }


def _float_text(value: Any) -> Any:
    if isinstance(value, float):
        return format(value, ".17g")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _float_text(row.get(field)) for field in fields})


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _wide_margin_rows(rows: list[dict[str, Any]], manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["model_key"], row["case_key"], row["mapping_id"])
        wide = grouped.setdefault(
            key,
            {
                "model_key": row["model_key"],
                "model": row["model"],
                "case_key": row["case_key"],
                "case": row["case"],
                "mapping_id": row["mapping_id"],
            },
        )
        wide[f"{row['condition_id']}_margin"] = row["semantic_margin"]
        wide[f"{row['condition_id']}_sign"] = row["margin_sign"]
    return list(grouped.values())


def _plot_outputs(output: Path, analysis: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    models = manifest["models"]
    conditions = manifest["conditions"]
    colours = {"negative": "#4472C4", "zero": "#A5A5A5", "positive": "#70AD47"}

    model_signs = [
        row for row in analysis["condition_sign_counts"] if row["aggregation_level"] == "model"
    ]
    labels = []
    values = {name: [] for name in SIGN_NAMES}
    for model in models:
        for condition in conditions:
            row = next(
                item
                for item in model_signs
                if item["model_key"] == model["model_key"] and item["condition_id"] == condition
            )
            labels.append(f"{model['display_name']}\n{condition.replace('_', ' ')}")
            for name in SIGN_NAMES:
                values[name].append(row[name])
    fig, ax = plt.subplots(figsize=(12, 5.5))
    bottoms = [0] * len(labels)
    for name in SIGN_NAMES:
        bars = ax.bar(range(len(labels)), values[name], bottom=bottoms, color=colours[name], label=name)
        for bar, count, bottom in zip(bars, values[name], bottoms):
            if count:
                ax.text(bar.get_x() + bar.get_width() / 2, bottom + count / 2, str(count), ha="center", va="center", color="white", fontweight="bold")
        bottoms = [bottom + count for bottom, count in zip(bottoms, values[name])]
    ax.set_ylabel("Mappings (n=12 per model/condition)")
    ax.set_title("Semantic-margin signs by model and condition")
    ax.set_xticks(range(len(labels)), labels, rotation=25, ha="right")
    ax.legend(ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(figures / "action_logprob_cross_model_condition_sign_counts_v0.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    case_short = {case["case_key"]: case["display_name"] for case in manifest["cases"]}
    mapping_short = {mapping["mapping_id"]: str(index + 1) for index, mapping in enumerate(manifest["mappings"])}
    fig, axes = plt.subplots(1, 3, figsize=(14, 6), sharey=True)
    y_labels = [
        f"{case_short[case['case_key']]} · M{index + 1}"
        for case in manifest["cases"]
        for index, _mapping in enumerate(manifest["mappings"])
    ]
    for ax, model in zip(axes, models):
        rows = [row for row in analysis["mapping_effects"] if row["model_key"] == model["model_key"]]
        for y, row in enumerate(rows):
            ratio = row["pressure_to_resolution_ratio"]
            if ratio is not None:
                ax.scatter(ratio, y, color="#ED7D31", s=30)
        ax.axvline(1.0, color="#666666", linestyle="--", linewidth=1)
        ax.set_title(model["display_name"])
        ax.set_xlabel("Directive / resolution effect")
        ax.grid(axis="x", alpha=0.25)
    axes[0].set_yticks(range(len(y_labels)), y_labels)
    axes[0].invert_yaxis()
    fig.suptitle("Mapping-specific pressure-to-resolution ratios (dimensionless)")
    fig.tight_layout()
    fig.savefig(figures / "action_logprob_cross_model_pressure_resolution_ratio_v0.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(12, 5), sharey=True)
    offsets = {"semantic_margin": -0.12, "directive_effect": 0.0, "resolution_effect": 0.12}
    markers = {"semantic_margin": "o", "directive_effect": "s", "resolution_effect": "^"}
    for ax, model in zip(axes, models):
        rows = [
            row
            for row in analysis["mapping_robustness"]
            if row["model_key"] == model["model_key"]
            and row["symmetric_relative_difference"] is not None
        ]
        for metric in offsets:
            metric_rows = [row for row in rows if row["metric"] == metric]
            xs = [
                (0 if row["dimension"] == "label_assignment" else 1) + offsets[metric]
                for row in metric_rows
            ]
            ys = [row["symmetric_relative_difference"] for row in metric_rows]
            ax.scatter(xs, ys, s=20, alpha=0.7, marker=markers[metric], label=metric.replace("_", " "))
        ax.set_title(model["display_name"])
        ax.set_xticks((0, 1), ("Label", "Position"))
        ax.set_ylim(-0.05, 2.05)
        ax.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Symmetric relative difference")
    axes[-1].legend(frameon=False, fontsize=8)
    fig.suptitle("Mapping nuisance-sensitivity diagnostics (dimensionless)")
    fig.tight_layout()
    fig.savefig(figures / "action_logprob_cross_model_mapping_robustness_v0.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_outputs(
    output: Path, analysis: dict[str, Any], manifest: Mapping[str, Any], source_validation: dict[str, Any]
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "source_validation.json", source_validation)
    _write_json(
        output / "regression_checks.json",
        {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "all_passed": all(item["passed"] for item in analysis["regression_checks"]),
            "checks": analysis["regression_checks"],
        },
    )
    summary_keys = (
        "schema_version",
        "analysis_version",
        "definitions",
        "source_files",
        "bundle_checks",
        "artifact_manifest_checks",
        "case_summaries",
        "model_summaries",
        "condition_sign_counts",
        "condition_margin_summaries",
        "boundary_crossings",
        "boundary_crossing_summaries",
        "generation_conformance",
    )
    _write_json(output / "analysis_summary.json", {key: analysis[key] for key in summary_keys})

    csv_specs = (
        (
            "condition_margins_long.csv",
            analysis["condition_margins"],
            ("model_key", "model", "case_key", "case", "case_id", "mapping_id", "condition_id", "bounded_label", "broader_label", "semantic_margin", "margin_sign", "ordinary_generation_text", "ordinary_generation_conforms", "prompt_sha256", "source_path"),
        ),
        (
            "condition_margins_wide.csv",
            _wide_margin_rows(analysis["condition_margins"], manifest),
            ("model_key", "model", "case_key", "case", "mapping_id", "unresolved_neutral_margin", "unresolved_neutral_sign", "unresolved_directive_margin", "unresolved_directive_sign", "resolved_neutral_margin", "resolved_neutral_sign"),
        ),
        (
            "mapping_effects.csv",
            analysis["mapping_effects"],
            ("model_key", "model", "case_key", "case", "case_id", "mapping_id", "directive_effect", "directive_sign", "resolution_effect", "resolution_sign", "pressure_to_resolution_ratio", "ratio_status"),
        ),
        (
            "case_summaries.csv",
            analysis["case_summaries"],
            tuple(analysis["case_summaries"][0]),
        ),
        (
            "model_summaries.csv",
            analysis["model_summaries"],
            tuple(analysis["model_summaries"][0]),
        ),
        (
            "condition_margin_summaries.csv",
            analysis["condition_margin_summaries"],
            ("aggregation_level", "model_key", "model", "case_key", "case", "condition_id", "mean", "median", "minimum", "maximum", "count"),
        ),
        (
            "condition_sign_counts.csv",
            analysis["condition_sign_counts"],
            ("aggregation_level", "model_key", "model", "case_key", "case", "condition_id", "negative", "zero", "positive", "total"),
        ),
        (
            "boundary_crossings.csv",
            analysis["boundary_crossings"],
            ("model_key", "model", "case_key", "case", "mapping_id", "effect_type", "source_condition", "target_condition", "source_margin", "target_margin", "source_sign", "target_sign", "transition", "crosses_zero", "touches_or_leaves_zero"),
        ),
        (
            "boundary_crossing_summaries.csv",
            analysis["boundary_crossing_summaries"],
            ("aggregation_level", "model_key", "model", "case_key", "case", "effect_type", "transition", "count", "total", "crosses_zero_count", "touches_or_leaves_zero_count"),
        ),
        (
            "mapping_robustness.csv",
            analysis["mapping_robustness"],
            ("model_key", "model", "case_key", "case", "dimension", "pair_id", "metric", "condition_id", "left_mapping", "right_mapping", "left_value", "right_value", "left_sign", "right_sign", "sign_agreement", "symmetric_relative_difference", "comparison_status"),
        ),
        (
            "generation_conformance.csv",
            analysis["generation_conformance"],
            ("aggregation_level", "model_key", "model", "case_key", "case", "conforming", "total"),
        ),
    )
    for name, rows, fields in csv_specs:
        _write_csv(output / name, rows, fields)
    _plot_outputs(output, analysis, manifest)


def run(manifest_path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> Path:
    manifest = load_manifest(manifest_path, root=root)
    output = _repo_path(manifest["output_root"], root=root)
    source_specs = [
        item
        for model in manifest["models"]
        for item in model["results"].values()
    ]
    pre_hashes = {item["path"]: sha256_path(_repo_path(item["path"], root=root)) for item in source_specs}
    analysis = analyse(manifest, root=root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cross_model_v0_", dir=output.parent) as temporary:
        stage = Path(temporary) / "output"
        provisional_validation = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "raw_sources_unchanged": None,
            "pre_analysis_sha256": pre_hashes,
            "post_analysis_sha256": {},
        }
        write_outputs(stage, analysis, manifest, provisional_validation)
        post_hashes = {
            item["path"]: sha256_path(_repo_path(item["path"], root=root))
            for item in source_specs
        }
        if pre_hashes != post_hashes:
            raise AnalysisError("One or more canonical raw result hashes changed during analysis")
        source_validation = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "raw_sources_unchanged": True,
            "pre_analysis_sha256": pre_hashes,
            "post_analysis_sha256": post_hashes,
            "canonical_result_count": len(source_specs),
        }
        _write_json(stage / "source_validation.json", source_validation)
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(stage, output)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        output = run(args.manifest.resolve())
    except AnalysisError as exc:
        print(f"Analysis failed: {exc}")
        return 1
    print(f"Validated and analysed nine canonical result files.")
    print(f"Saved deterministic tables and JSON plus presentation plots to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
