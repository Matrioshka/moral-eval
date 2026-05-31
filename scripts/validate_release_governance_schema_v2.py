from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Final


DEFAULT_PATH: Final = Path(
    "data/moral_reasoning_integrity_behaviour_justifiable_corrigibility_release_governance_schema_v2_pilot.jsonl"
)

REQUIRED_FIELDS: Final = {
    "id",
    "source_item_id",
    "case_id",
    "variant",
    "dataset_version",
    "risk_track",
    "moral_domain",
    "evidence_quality",
    "pressure_type",
    "followup_strength",
    "expected_update",
    "release_artifact",
    "access_purpose",
    "access_population",
    "access_modality",
    "operational_status",
    "externalisation_level",
    "reversibility_level",
    "monitorability_level",
    "enforceability_level",
    "misuse_evaluation_state",
    "missing_evaluation_types",
    "safeguard_type",
    "safeguard_relevance_to_original_concern",
    "pressure_source",
    "pressure_mechanism",
    "pressure_legitimacy",
    "pressure_escalation_stage",
    "pressure_target",
    "conflict_type",
    "situational_awareness_context",
    "target_access",
    "minimum_acceptable_access",
    "maximum_acceptable_access",
    "target_release_scope",
    "minimum_acceptable_release_scope",
    "maximum_acceptable_release_scope",
    "release_scope_options",
    "expected_behaviour",
    "difficulty",
    "difficulty_notes",
    "pressure_trap",
    "too_conservative_failure",
    "too_permissive_failure",
    "failure_modes",
    "relevant_moral_features",
    "safeguard_features",
    "missing_safeguards",
    "residual_risk_features",
    "scenario",
    "initial_judgement",
    "user_followup",
    "ideal_behaviour",
}

TARGET_ACCESS_FIELDS: Final = {
    "access_purpose",
    "access_population",
    "access_modality",
    "operational_status",
    "externalisation_level",
}

ENUMS: Final[dict[str, set[str]]] = {
    "evidence_quality": {
        "irrelevant_reassurance",
        "weak_safeguard",
        "strong_but_incomplete_safeguard",
        "near_sufficient_safeguard",
    },
    "access_purpose": {
        "none",
        "internal_evaluation",
        "internal_red_teaming",
        "external_safety_audit",
        "external_research",
        "customer_or_partner_use",
        "emergency_defensive_use",
        "public_release",
    },
    "access_population": {
        "none",
        "internal_team",
        "internal_red_team",
        "external_auditors",
        "vetted_researchers",
        "trusted_partners",
        "verified_defenders",
        "limited_customers",
        "broad_users",
        "public",
    },
    "access_modality": {
        "none",
        "internal_only",
        "hosted_no_weights",
        "hosted_fine_tuning",
        "downloadable_adapter",
        "limited_weight_transfer",
        "open_weight_release",
        "unrestricted_access",
    },
    "operational_status": {
        "evaluation_only",
        "safety_audit_only",
        "research_use",
        "operational_use",
        "emergency_operational_use",
        "public_use",
    },
    "externalisation_level": {
        "none",
        "internal_only",
        "external_evaluation",
        "external_non_operational_use",
        "external_operational_use",
        "external_distribution",
        "public_distribution",
    },
    "reversibility_level": {"high", "medium", "low", "irreversible"},
    "monitorability_level": {"high", "medium", "low", "none"},
    "enforceability_level": {"high", "medium", "low", "none"},
    "misuse_evaluation_state": {
        "not_started",
        "partial",
        "core_evals_complete",
        "external_audited",
        "near_complete",
        "complete_for_target_scope",
    },
    "safeguard_relevance_to_original_concern": {
        "irrelevant",
        "indirect",
        "partial_direct",
        "direct_but_incomplete",
        "direct_and_near_sufficient",
    },
    "pressure_source": {
        "none",
        "executive_leadership",
        "senior_researcher",
        "policy_board",
        "customer",
        "partner",
        "research_community",
        "competitor",
        "emergency_response_team",
        "public_relations_team",
        "national_security_actor",
    },
    "pressure_mechanism": {
        "none",
        "authority",
        "urgency",
        "competition",
        "reputation",
        "institutional_consensus",
        "research_goodwill",
        "customer_value",
        "emergency_need",
        "emotional_burden",
        "resource_constraint",
    },
    "pressure_legitimacy": {
        "none",
        "illegitimate",
        "mostly_illegitimate",
        "mixed",
        "partly_legitimate",
        "legitimate_but_not_safety_evidence",
        "legitimate_and_safety_relevant",
    },
    "pressure_escalation_stage": {
        "none",
        "single_turn_followup",
        "repeated_pushback",
        "authority_escalation",
        "urgency_escalation",
        "multi_turn_escalation",
    },
    "pressure_target": {
        "approve_release",
        "broaden_access",
        "avoid_delay",
        "preserve_reputation",
        "satisfy_authority",
        "support_research_access",
        "support_customer_use",
        "support_emergency_use",
        "relax_safety_threshold",
        "reinterpret_evidence",
    },
    "conflict_type": {
        "none",
        "safety_vs_speed",
        "safety_vs_competition",
        "safety_vs_research_openness",
        "safety_vs_customer_value",
        "safety_vs_emergency_benefit",
        "safety_vs_institutional_consensus",
        "safety_vs_authority",
        "safety_vs_reputation",
    },
    "situational_awareness_context": {
        "none",
        "ordinary_user_context",
        "evaluation_context",
        "training_context",
        "deployment_context",
        "private_unmonitored_context",
        "monitored_context",
    },
}

LIST_ENUMS: Final[dict[str, set[str]]] = {
    "missing_evaluation_types": {
        "fine_tuned_misuse_evaluation",
        "tool_augmented_misuse_evaluation",
        "scaffold_specific_misuse_evaluation",
        "adversarial_user_testing",
        "external_audit",
        "external_audit_readiness_assessment",
        "monitoring_validation",
        "leak_response_testing",
        "partner_environment_audit",
        "adapter_compositional_misuse_testing",
        "reconstruction_risk_assessment",
        "distilled_model_dangerous_capability_transfer_testing",
        "offensive_misuse_evaluation",
        "complete_offensive_misuse_evaluation",
        "downstream_integration_testing",
        "tool_integration_evaluation",
        "broad_user_monitoring_validation",
        "incident_response_validation",
        "rollback_validation",
        "fine_tuning_safety_case",
    },
    "safeguard_type": {
        "administrative",
        "legal_contractual",
        "policy_governance",
        "hosted_containment",
        "access_control",
        "monitoring",
        "rate_limits",
        "revocation",
        "external_audit",
        "base_model_external_evaluation",
        "technical_eval",
        "red_team_eval",
        "incident_response",
        "rollback",
    },
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Line {line_no}: invalid JSON: {exc}") from exc
            row["__line_no"] = line_no
            rows.append(row)
    return rows


def validate_row(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    line = row.get("__line_no", "?")
    item_id = row.get("id", "<missing id>")
    prefix = f"line {line} {item_id}"

    missing = sorted(REQUIRED_FIELDS - row.keys())
    if missing:
        errors.append(f"{prefix}: missing required fields: {', '.join(missing)}")

    for field, allowed in ENUMS.items():
        value = row.get(field)
        if value is not None and value not in allowed:
            errors.append(f"{prefix}: {field}={value!r} is not in allowed set")

    for field, allowed in LIST_ENUMS.items():
        value = row.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"{prefix}: {field} must be a list")
            continue
        invalid = [item for item in value if item not in allowed]
        if invalid:
            errors.append(f"{prefix}: {field} contains invalid values: {invalid}")

    for target_field in [
        "target_access",
        "minimum_acceptable_access",
        "maximum_acceptable_access",
    ]:
        value = row.get(target_field)
        if not isinstance(value, dict):
            errors.append(f"{prefix}: {target_field} must be an object")
            continue
        nested_missing = sorted(TARGET_ACCESS_FIELDS - value.keys())
        if nested_missing:
            errors.append(
                f"{prefix}: {target_field} missing nested fields: {', '.join(nested_missing)}"
            )

        for nested_field in TARGET_ACCESS_FIELDS:
            if nested_field not in value:
                continue
            allowed = ENUMS[nested_field]
            if value[nested_field] not in allowed:
                errors.append(
                    f"{prefix}: {target_field}.{nested_field}={value[nested_field]!r} is not allowed"
                )

    # Consistency checks between flat target fields and target_access.
    target_access = row.get("target_access")
    if isinstance(target_access, dict):
        for field in TARGET_ACCESS_FIELDS:
            if field in target_access and row.get(field) != target_access[field]:
                errors.append(
                    f"{prefix}: flat {field}={row.get(field)!r} does not match target_access.{field}={target_access[field]!r}"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release-governance schema v2 JSONL records."
    )
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    rows = load_jsonl(args.path)
    errors: list[str] = []
    for row in rows:
        errors.extend(validate_row(row))

    if errors:
        print(f"Validation failed: {len(errors)} error(s)")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Valid schema-v2 JSONL records: {len(rows)}")
    print("By target_release_scope:", dict(Counter(r["target_release_scope"] for r in rows)))
    print("By access_purpose:", dict(Counter(r["access_purpose"] for r in rows)))
    print("By access_population:", dict(Counter(r["access_population"] for r in rows)))
    print("By access_modality:", dict(Counter(r["access_modality"] for r in rows)))
    print("By operational_status:", dict(Counter(r["operational_status"] for r in rows)))
    print("By externalisation_level:", dict(Counter(r["externalisation_level"] for r in rows)))
    print("By pressure_mechanism:", dict(Counter(r["pressure_mechanism"] for r in rows)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
