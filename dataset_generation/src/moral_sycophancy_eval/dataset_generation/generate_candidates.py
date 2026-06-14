"""Candidate generation functions for Phase 3 scenarios."""

from __future__ import annotations

import random
from itertools import product
from typing import Iterable

from .llm_clients import StructuredLLM, generate_many_structured
from .prompts import PromptConfig
from .schemas import (
    CandidateRecord,
    CatastrophicRiskDomain,
    EvidenceQuality,
    MatrixCell,
    PressureType,
    ScenarioCandidate,
)

DEFAULT_DOMAINS: tuple[CatastrophicRiskDomain, ...] = (
    "frontier_ai_deployment",
    "dangerous_capability_evaluation",
    "autonomous_cyber_agent",
    "ai_biosecurity_dual_use",
    "agentic_scaffolding",
    "shutdown_control_evaluation",
    "automated_alignment_research_assistant",
    "model_release_governance",
    "critical_infrastructure_ai_assistant",
    "military_national_security_decision_support",
)

DEFAULT_EVIDENCE_QUALITIES: tuple[EvidenceQuality, ...] = (
    "irrelevant_reassurance",
    "weak_safeguard",
    "strong_but_incomplete_safeguard",
    "near_sufficient_safeguard",
)

DEFAULT_PRESSURE_TYPES: tuple[PressureType, ...] = (
    "none",
    "authority_seniority",
    "urgency_deployment",
    "emotional_reputational",
    "institutional_consensus",
)


def build_matrix_cells(
    domains: Iterable[CatastrophicRiskDomain] = DEFAULT_DOMAINS,
    evidence_qualities: Iterable[EvidenceQuality] = DEFAULT_EVIDENCE_QUALITIES,
    pressure_types: Iterable[PressureType] = DEFAULT_PRESSURE_TYPES,
) -> list[MatrixCell]:
    return [MatrixCell(domain=d, evidence_quality=e, pressure_type=p) for d, e, p in product(domains, evidence_qualities, pressure_types)]


def generate_candidates_for_cells(
    *,
    llm: StructuredLLM,
    model: str,
    cells: list[MatrixCell],
    n_per_cell: int = 1,
    prompt_config: PromptConfig | None = None,
    temperature: float = 0.8,
    max_tokens: int = 2500,
    max_workers: int | None = 6,
    seed: int = 1,
    case_id_stem: str = "jmcu_p3",
) -> list[CandidateRecord]:
    """Generate candidate records for specified matrix cells.

    This function makes model calls through the supplied StructuredLLM. It does
    not score or filter the candidates.
    """
    prompt_config = prompt_config or PromptConfig()
    rng = random.Random(seed)

    expanded_cells: list[MatrixCell] = []
    messages_list = []
    prefixes = []

    for cell in cells:
        for i in range(n_per_cell):
            expanded_cells.append(cell)
            prefix = f"{case_id_stem}_{cell.key()}_{i + 1:03d}"
            prefixes.append(prefix)
            messages_list.append(prompt_config.build_generation_messages(cell, prefix, rng=rng))

    candidates = generate_many_structured(
        llm=llm,
        model=model,
        messages_list=messages_list,
        response_model=ScenarioCandidate,
        temperature=temperature,
        max_tokens=max_tokens,
        max_workers=max_workers,
    )

    return [
        CandidateRecord(
            candidate=candidate,
            generation_model=model,
            prompt_version=prompt_config.prompt_version,
            generation_cell=cell,
        )
        for candidate, cell in zip(candidates, expanded_cells)
    ]
