from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts" / "log_prob"
sys.path.insert(0, str(SCRIPT_DIR))

import build_airiskdilemmas_jmcup_whole_corpus_v3 as pipeline  # noqa: E402


def full_row(
    dilemma: str,
    action_number: int,
    action_body: str,
    *,
    values: list[str] | None = None,
    risks: list[str] | None = None,
    context: str = "Technology",
) -> dict[str, object]:
    return {
        "dilemma": dilemma,
        "action": f"Action {action_number}: {action_body}",
        "values": list(values or [f"value-{action_number}"]),
        "risky_behaviors": list(risks or []),
        "context": context,
    }


def target_row(source: dict[str, object], targets: list[str]) -> dict[str, object]:
    return {
        "dilemma": source["dilemma"],
        "action": source["action"],
        "values": source["values"],
        "targets": targets,
    }


def make_occurrence(
    pair_index: int,
    theme: str,
    *,
    risk: str = "",
    context: str = "Technology",
) -> dict[str, object]:
    dilemma = f"{theme} shared source theme contextualisation {pair_index}"
    risks = [risk] if risk else []
    actions = {
        "action_1": {
            "source_row_index": pair_index * 2,
            "action": f"Action 1: Bound {theme}",
            "action_body": f"Bound {theme}",
            "values": ["care"],
            "risky_behaviors": risks,
            "context": context,
            "targets": None,
            "targets_available": False,
            "targets_source_configuration": None,
            "targets_source_row_index": None,
            "targets_join_method": None,
            "targets_source_dilemma_sha256": None,
        },
        "action_2": {
            "source_row_index": pair_index * 2 + 1,
            "action": f"Action 2: Expand {theme}",
            "action_body": f"Expand {theme}",
            "values": ["learning"],
            "risky_behaviors": risks,
            "context": context,
            "targets": None,
            "targets_available": False,
            "targets_source_configuration": None,
            "targets_source_row_index": None,
            "targets_join_method": None,
            "targets_source_dilemma_sha256": None,
        },
    }
    payload = {"dilemma": dilemma, "actions": actions}
    return {
        "occurrence_id": f"airisk_pair_{pair_index:05d}",
        "pair_index": pair_index,
        "original_action_row_indices": [pair_index * 2, pair_index * 2 + 1],
        "dilemma": dilemma,
        "dilemma_sha256": pipeline.sha256_text(dilemma),
        "actions": actions,
        "risky_behaviors": risks,
        "contexts": [context],
        "full_pair_sha256": pipeline.sha256_text(pipeline.canonical_json(payload)),
    }


def unique_from_occurrences(occurrences: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for occurrence in occurrences:
        result.append(
            {
                "dilemma_id": f"airisk_dilemma_{occurrence['dilemma_sha256'][:16]}",
                "canonical_pair_index": occurrence["pair_index"],
                "dilemma": occurrence["dilemma"],
                "dilemma_sha256": occurrence["dilemma_sha256"],
                "occurrence_count": 1,
                "source_occurrences": [occurrence],
            }
        )
    return result


def synthetic_groups() -> tuple[list[dict[str, object]], list[list[dict[str, object]]]]:
    themes = [
        ("alpha", "Deception", "Healthcare", 3),
        ("bravo", "Power-Seeking", "Business", 3),
        ("charlie", "Others", "Technology", 2),
        ("delta", "Proxy Gaming", "Education", 3),
    ]
    occurrences: list[dict[str, object]] = []
    groups: list[list[dict[str, object]]] = []
    for theme, risk, context, size in themes:
        group = []
        for _ in range(size):
            occurrence = make_occurrence(len(occurrences), theme, risk=risk, context=context)
            occurrences.append(occurrence)
            group.append(occurrence)
        groups.append(group)
    return occurrences, groups


def source_questions() -> list[dict[str, object]]:
    rows = []
    for index, question in enumerate(
        [
            "Would you expand alpha access?\n\nChoices:\n (A) Yes\n (B) No",
            "Would you override bravo authority?\n\nChoices:\n (A) Yes\n (B) No",
            "Would you change charlie controls?\n\nChoices:\n (A) Yes\n (B) No",
            "Would you optimise delta rewards?\n\nChoices:\n (A) Yes\n (B) No",
        ]
    ):
        rows.append(
            {
                "source_record_id": f"source-{index}",
                "source_path": "advanced-ai-risk/human_generated_evals/test.jsonl",
                "source_row_index": index,
                "source_dataset_layer": "human_generated_evals",
                "question": question,
                "question_sha256": pipeline.sha256_text(question),
                "answer_matching_behavior": " (A)",
                "answer_not_matching_behavior": " (B)",
            }
        )
    return rows


def build_synthetic_products():
    occurrences, generation_groups = synthetic_groups()
    unique_records = unique_from_occurrences(occurrences)
    triage = []
    for ordinal, group in enumerate(generation_groups):
        group_id = f"airisk_generation_group_{ordinal:04d}"
        for occurrence in group:
            record = next(
                item
                for item in unique_records
                if item["dilemma_sha256"] == occurrence["dilemma_sha256"]
            )
            triage.append(pipeline.triage_record(record, group_id))
    triage.sort(key=lambda item: item["canonical_pair_index"])
    triage_by_hash = {item["dilemma_sha256"]: item for item in triage}
    segmentation = {
        "generation_grouping_confidence": "highly_likely",
        "primary_hypothesis_clearly_preferred": True,
    }
    audit = pipeline.select_pre_outcome_audit_groups(
        [f"airisk_generation_group_{index:04d}" for index in range(4)],
        sample_count=1,
        seed="test-seed",
    )
    groups, review, membership = pipeline.build_group_records(
        generation_groups,
        triage_by_hash,
        pipeline.SourceQuestionIndex(source_questions()),
        {"status": "unavailable", "generation_group_ordinal": None},
        segmentation,
        audit,
    )
    return occurrences, unique_records, triage, groups, review, membership, audit


def test_pair_reconstruction_preserves_exact_rows_and_action_identity() -> None:
    dilemma = "Exact dilemma text with trailing space "
    action_2 = full_row(dilemma, 2, "Stay bounded", values=["safety"])
    action_1 = full_row(
        dilemma,
        1,
        "Expand authority",
        values=["capability"],
        risks=["Power-Seeking"],
    )
    occurrences, unique, summary = pipeline.reconstruct_pairs([action_2, action_1])

    assert summary["pair_count"] == 1
    assert unique[0]["dilemma"] == dilemma
    assert occurrences[0]["original_action_row_indices"] == [0, 1]
    assert occurrences[0]["actions"]["action_1"]["source_row_index"] == 1
    assert occurrences[0]["actions"]["action_1"]["action"] == "Action 1: Expand authority"
    assert occurrences[0]["actions"]["action_2"]["source_row_index"] == 0


def test_repeated_dilemma_text_keeps_both_different_metadata_occurrences() -> None:
    dilemma = "Repeated refusal dilemma"
    rows = [
        full_row(dilemma, 1, "Decline", values=["beneficence"]),
        full_row(dilemma, 2, "Comply", values=["compliance"]),
        full_row(dilemma, 1, "Decline", values=["integrity"]),
        full_row(dilemma, 2, "Comply", values=["satisfaction"]),
    ]
    occurrences, unique, summary = pipeline.reconstruct_pairs(rows)

    assert len(occurrences) == 2
    assert len(unique) == 1
    assert unique[0]["occurrence_count"] == 2
    assert unique[0]["source_occurrences"][0]["actions"]["action_1"]["values"] == [
        "beneficence"
    ]
    assert unique[0]["source_occurrences"][1]["actions"]["action_1"]["values"] == [
        "integrity"
    ]
    assert summary["repeated_dilemma_text_occurrence_count"] == 1
    assert summary["full_pair_duplicate_occurrence_count"] == 0


def test_target_join_allows_only_audited_line_ending_normalisation() -> None:
    full = [
        full_row("First line\n\nSecond line", 1, "Bound"),
        full_row("First line\n\nSecond line", 2, "Expand"),
    ]
    targets = [
        target_row({**full[0], "dilemma": "First line\r\n\r\nSecond line"}, ["Human"]),
        target_row({**full[1], "dilemma": "First line\r\n\r\nSecond line"}, ["AI"]),
    ]
    occurrences, unique, summary = pipeline.reconstruct_pairs(full, targets)

    assert summary["target_join_counts"] == {"normalised_line_endings": 2}
    assert occurrences[0]["dilemma"] == "First line\n\nSecond line"
    assert occurrences[0]["actions"]["action_1"]["targets"] == ["Human"]
    assert unique[0]["source_occurrences"][0]["actions"]["action_2"][
        "targets_join_method"
    ] == "normalised_line_endings"


@pytest.mark.parametrize(
    "rows, message",
    [
        ([full_row("x", 1, "a")], "even action-row"),
        (
            [full_row("x", 1, "a"), full_row("y", 2, "b")],
            "identical non-empty dilemma",
        ),
        (
            [full_row("x", 1, "a"), full_row("x", 1, "b")],
            "repeats Action 1",
        ),
    ],
)
def test_malformed_pairs_are_detected(rows, message: str) -> None:
    with pytest.raises(pipeline.PipelineError, match=message):
        pipeline.reconstruct_pairs(rows)


def test_primary_segmentation_is_tested_against_all_and_local_alternatives() -> None:
    occurrences, _ = synthetic_groups()
    evidence = pipeline.evaluate_segmentation_candidates(
        occurrences,
        expected_group_count=4,
        full_group_size=3,
        primary_short_group_ordinal=2,
    )

    assert evidence["all_candidate_count"] == 4
    assert evidence["primary_short_group_ordinal"] == 2
    assert evidence["local_alternative_ordinals"] == [0, 1, 2, 3]
    assert evidence["lexical_best_short_group_ordinal"] == 2
    assert evidence["metadata_best_short_group_ordinal"] == 2
    groups = pipeline.segment_occurrences(
        occurrences,
        expected_group_count=4,
        full_group_size=3,
        short_group_ordinal=2,
    )
    assert [len(group) for group in groups] == [3, 3, 2, 3]
    assert [item["pair_index"] for item in groups[3]] == [8, 9, 10]


def test_evidence_channels_remain_separate_and_lexical_has_one_vote() -> None:
    segmentation = {
        "primary_short_group_ordinal": 7,
        "metadata_best_short_group_ordinal": 7,
        "metadata_primary_rank": 1,
        "lexical_best_short_group_ordinal": 6,
        "lexical_primary_rank": 2,
        "source_match_best_short_group_ordinal": 7,
        "source_match_primary_rank": 1,
    }
    result = pipeline.finalise_segmentation_evidence(
        segmentation,
        {"status": "exact_publication_anchor", "generation_group_ordinal": 0},
    )

    assert result["primary_votes_among_discriminating_channels"] == 2
    assert result["primary_hypothesis_clearly_preferred"] is True
    assert set(result["evidence_channels"]) == {
        "ordering",
        "metadata",
        "lexical",
        "source_match",
        "publication_anchor",
    }
    assert result["evidence_channels"]["lexical"]["role"].endswith("not_dominant")


def test_target_availability_cannot_change_triage_score_or_band() -> None:
    full = [full_row("Hospital safety validation dilemma", 1, "Bound access"), full_row("Hospital safety validation dilemma", 2, "Expand access")]
    targets = [target_row(full[0], ["Human"]), target_row(full[1], ["AI"])]
    _, without_target, _ = pipeline.reconstruct_pairs(full)
    _, with_target, _ = pipeline.reconstruct_pairs(full, targets)
    left = pipeline.triage_record(without_target[0], "group")
    right = pipeline.triage_record(with_target[0], "group")

    score_fields = [
        "heuristic_high_stakes_relevance_score",
        "heuristic_transformability_score",
        "heuristic_jmcup_source_priority_score",
        "heuristic_queue_priority_band",
    ]
    assert {field: left[field] for field in score_fields} == {
        field: right[field] for field in score_fields
    }
    assert right["target_metadata_summary"]["role"] == "supplemental_provenance_only_not_used_in_any_heuristic"


def test_every_group_and_every_contextualisation_enters_review_queue() -> None:
    occurrences, unique, _, groups, review, membership, _ = build_synthetic_products()

    assert len(groups) == len(review) == 4
    assert len(membership) == len(occurrences) == 11
    assert sum(len(record["all_contextualisations"]) for record in review) == 11
    assert {record["generation_group_id"] for record in review} == {
        record["generation_group_id"] for record in groups
    }
    pipeline.validate_membership(occurrences, unique, groups, membership)
    assert all(len(record["representative_candidates"]) <= 3 for record in groups)


def test_review_fields_include_later_review_additions_and_remain_empty() -> None:
    _, _, _, _, review, _, _ = build_synthetic_products()
    fields = review[0]["reviewer_fields"]

    assert all(fields[name] is None for name in pipeline.H_REVIEW_FIELDS)
    assert fields["proposed_transformation_family"] is None
    assert fields["semantic_independence_concern"] is None
    assert fields["likely_transformation_duplicate_of"] is None
    assert fields["source_fidelity"] is None
    assert fields["group_useful_without_clean_representative"] is None
    assert pipeline.review_schema()["source_fidelity_allowed_values"] == [
        "high",
        "moderate",
        "low",
        None,
    ]


def test_generation_group_and_source_seed_match_are_distinct() -> None:
    _, _, _, groups, _, membership, _ = build_synthetic_products()

    assert all(record["generation_group_id"] for record in groups)
    assert all(record["source_seed_match"]["status"] == "candidate_matches_only" for record in groups)
    assert all(row["source_seed_match_status"] != "exact" for row in membership)
    assert all(record["source_seed_match"]["source_seed_record"] is None for record in groups)


def test_publication_anchor_fails_closed_when_not_unique() -> None:
    occurrences, groups = synthetic_groups()
    result = pipeline.validate_publication_anchor(source_questions(), occurrences, groups)
    assert result["status"] == "unavailable"
    assert result["confidence"] == "unavailable"


def test_pre_outcome_audit_sample_is_hash_deterministic_and_order_independent() -> None:
    group_ids = [f"group-{index}" for index in range(20)]
    first = pipeline.select_pre_outcome_audit_groups(group_ids, sample_count=5, seed="fixed")
    second = pipeline.select_pre_outcome_audit_groups(
        list(reversed(group_ids)), sample_count=5, seed="fixed"
    )
    assert first == second
    assert sorted(first.values()) == [1, 2, 3, 4, 5]


def test_local_source_inventory_discovers_observed_schema(tmp_path: Path) -> None:
    source_dir = tmp_path / "advanced-ai-risk" / "human_generated_evals"
    source_dir.mkdir(parents=True)
    row = {
        "question": "Question?\n\nChoices:\n (A) Yes\n (B) No",
        "answer_matching_behavior": " (A)",
        "answer_not_matching_behavior": " (B)",
    }
    (source_dir / "source.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    questions, provenance = pipeline.load_advanced_ai_risk_inventory(
        "f" * 40, repository_dir=tmp_path
    )
    assert len(questions) == 1
    assert provenance["candidate_question_count"] == 1
    assert provenance["candidate_question_counts_by_layer"] == {
        "human_generated_evals": 1
    }
    assert provenance["files"][0]["observed_key_sets"][0]["count"] == 1


def test_output_order_and_hashes_are_byte_reproducible(tmp_path: Path) -> None:
    _, _, triage, groups, review, membership, _ = build_synthetic_products()

    def write(destination: Path):
        return pipeline.write_outputs(
            destination,
            triage=triage,
            groups=groups,
            review_records=review,
            membership_rows=membership,
            lineage_markdown="# fixed lineage\n",
            triage_markdown="# fixed triage\n",
            manifest_builder=lambda hashes: {
                "schema_version": "test",
                "created_at_utc": "2026-01-01T00:00:00+00:00",
                "output_file_sha256": dict(sorted(hashes.items())),
            },
            overwrite=False,
        )

    first = tmp_path / "first"
    second = tmp_path / "second"
    write(first)
    write(second)
    for name in pipeline.OUTPUT_FILENAMES:
        assert (first / name).read_bytes() == (second / name).read_bytes()

    manifest = json.loads((first / "manifest_v3.json").read_text(encoding="utf-8"))
    for name, expected_hash in manifest["output_file_sha256"].items():
        assert hashlib.sha256((first / name).read_bytes()).hexdigest() == expected_hash


def test_membership_csv_is_one_row_per_source_occurrence(tmp_path: Path) -> None:
    occurrences, _, triage, groups, review, membership, _ = build_synthetic_products()
    output = tmp_path / "products"
    pipeline.write_outputs(
        output,
        triage=triage,
        groups=groups,
        review_records=review,
        membership_rows=membership,
        lineage_markdown="lineage\n",
        triage_markdown="triage\n",
        manifest_builder=lambda hashes: {"output_file_sha256": hashes},
        overwrite=False,
    )
    lines = (output / "airisk_jmcup_source_group_membership.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == len(occurrences) + 1
    assert "generation_group_id" in lines[0]
    assert "source_seed_match_status" in lines[0]


def test_pipeline_has_no_model_inference_import_dependency() -> None:
    script = SCRIPT_DIR / "build_airiskdilemmas_jmcup_whole_corpus_v3.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(
        {
            "torch",
            "transformers",
            "anthropic",
            "openai",
            "google",
            "mistral_common",
            "sentence_transformers",
        }
    )
    assert pipeline.ANTI_CONTAMINATION_STATEMENT.startswith("No evaluated JMCUP")


@pytest.mark.skipif(
    os.environ.get("RUN_AIRISK_INTEGRATION") != "1",
    reason="Set RUN_AIRISK_INTEGRATION=1 to validate the real pinned dataset.",
)
def test_real_pinned_dataset_integration_counts() -> None:
    full_rows, target_rows, provenance = pipeline.load_airisk_datasets(
        pipeline.DEFAULT_AIRISK_REVISION
    )
    occurrences, unique, summary = pipeline.reconstruct_pairs(full_rows, target_rows)
    pipeline.validate_expected_real_counts(summary)
    assert provenance["full_fingerprint"] == "93a241b45b9e097e"
    assert len(occurrences) == 10_399
    assert len(unique) == 10_398
    assert summary["target_join_counts"] == {
        "exact": 5_996,
        "normalised_line_endings": 4,
    }
