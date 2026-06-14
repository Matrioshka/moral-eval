"""Quality-control scoring and filtering for generated scenario candidates."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median, pstdev

from .llm_clients import StructuredLLM, generate_many_structured
from .prompts import QC_RUBRIC_VERSION, build_qc_messages
from .schemas import CandidateRecord, ScenarioQCResponse


def score_candidate_records(
    *,
    llm: StructuredLLM,
    model: str,
    records: list[CandidateRecord],
    temperature: float = 0.0,
    max_tokens: int = 1800,
    max_workers: int | None = 6,
) -> list[CandidateRecord]:
    """Run a separate LLM QC pass over candidate records."""
    messages_list = [build_qc_messages(record.candidate) for record in records]
    qc_results = generate_many_structured(
        llm=llm,
        model=model,
        messages_list=messages_list,
        response_model=ScenarioQCResponse,
        temperature=temperature,
        max_tokens=max_tokens,
        max_workers=max_workers,
    )

    scored: list[CandidateRecord] = []
    for record, qc in zip(records, qc_results):
        scored.append(
            CandidateRecord(
                candidate=record.candidate,
                qc=qc,
                generation_model=record.generation_model,
                judge_model=model,
                prompt_version=record.prompt_version,
                rubric_version=QC_RUBRIC_VERSION,
                generation_cell=record.generation_cell,
                created_at_utc=record.created_at_utc,
                source=record.source,
                notes=record.notes,
            )
        )
    return scored


def filter_candidate_records(
    records: list[CandidateRecord], *, min_mean_quality: float = 8.0, max_duplicate_risk: int = 4) -> list[CandidateRecord]:
    """Keep records passing explicit QC thresholds."""
    kept = []
    for record in records:
        if record.qc is None:
            continue
        if record.qc.decision == "keep" and record.qc.mean_quality_score >= min_mean_quality and record.qc.duplicate_risk <= max_duplicate_risk:
            kept.append(record)
    return kept


def summarise_records(records: list[CandidateRecord]) -> dict[str, object]:
    """Produce balance and QC summary statistics for manual review."""
    scores = [r.qc.mean_quality_score for r in records if r.qc is not None]
    decisions = [r.qc.decision for r in records if r.qc is not None]
    domains = [r.candidate.domain for r in records]
    evidence = [r.candidate.evidence_quality for r in records]
    pressure = [r.candidate.primary_pressure_type for r in records]
    updates = [r.candidate.judgement_envelope.target_update_direction for r in records]

    out: dict[str, object] = {
        "num_records": len(records),
        "domain_balance": dict(Counter(domains)),
        "evidence_quality_balance": dict(Counter(evidence)),
        "pressure_type_balance": dict(Counter(pressure)),
        "target_update_direction_balance": dict(Counter(updates)),
    }
    if scores:
        out.update(
            {
                "qc_mean": mean(scores),
                "qc_median": median(scores),
                "qc_min": min(scores),
                "qc_max": max(scores),
                "qc_pstdev": pstdev(scores) if len(scores) > 1 else 0.0,
                "qc_decision_balance": dict(Counter(decisions)),
            }
        )
    return out
