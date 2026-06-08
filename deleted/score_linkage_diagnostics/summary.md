# Score Linkage Diagnostics

This diagnostic is read-only with respect to PostgreSQL. It does not create score events, run evals, or mutate dataset files.

## Summary

- Unlinked manual_score rows inspected: 233
- Uniquely linkable manual_score rows proposed: 0
- Ambiguous or unmatched manual_score rows: 233
- Non-summary model_response candidates loaded: 602
- Deterministic score rows from summary CSVs ignored for proposal purposes: 12
- Manual-score files with unlinked rows: 19

## Matching Rules Applied

- The script does not propose a link by case_id/sample_id alone when more than one response candidate remains.
- Exact model, dataset_version, prompt_style, and source-file pairing are required whenever those fields or pairings are available.
- Row-order links are proposed only when the manual-score source file and expected output source file both exist in source_file and have identical record counts.
- Every proposed link is labelled with link_method and confidence_reason.
- Deterministic_score rows, including summary CSV rows, are not used for proposed manual-score links.

## Reports

- `tmp/score_linkage_diagnostics/unlinked_manual_scores_by_file.csv`
- `tmp/score_linkage_diagnostics/candidate_response_counts.csv`
- `tmp/score_linkage_diagnostics/uniquely_linkable_manual_scores.csv`
- `tmp/score_linkage_diagnostics/ambiguous_manual_scores.csv`
- `tmp/score_linkage_diagnostics/missing_output_file_pairs.csv`
