# AIRisk → JMCUP adjudicator-run audit protocol v1

## Purpose

This is a deterministic operational audit of one persisted adjudicator run. It
checks selection, configuration, schema, hash, journal-history and usage
integrity. It does not reinterpret completed H1–H7 judgements or infer semantic
quality from provider behaviour.

## Terminal provider-block rule

A selected group is `terminal_provider_block` only when it has no completed
adjudication, has at least two independently persisted journal records, and the
latest two records and their final attempts are explicitly `blocked` or
`refused` with structured provider/content-filter signals. Older records remain
in provenance but do not control terminality.

This status means that the frozen adjudicator judgement could not be obtained.
It is not a rejection or evidence that the source is semantically unsuitable.
Source resolution must route it to `pending_human_review` with an unresolved
disposition and no fabricated adjudication evidence.

## Usage accounting

Attempt-level normalised usage is authoritative within a journal record. The
record's top-level provider usage is a final-attempt compatibility projection
and is never added separately. A record-level aggregate may be used once only
when a legacy record lacks usable attempt details, and that fallback must be
reported explicitly.

## Fail-closed conditions

The audit stops on any selected-set, immutable-setting, schema, input-hash or
provenance mismatch; an unknown group; more than one completed record for a
group; or a completed response that fails canonical structure or local
invariants. Artefacts are selected only through explicit manifest paths or exact
hash/schema/group-set/provenance agreement, never names, timestamps or recency.
