# AI Prefill Review Policy

AI prefill outputs are review assistance only. They are not final labels, benchmark scores, or analysis-ready results unless they have been reviewed and saved by a human scorer.

The prefill pipeline may generate structured candidate scores for multi-stage pressure dialogues, but these outputs should be treated as draft annotations. They can help speed up human review by proposing failure classes, severity scores, per-turn judgements, and rationales, but they do not replace human scoring.

Only human-reviewed records should be treated as final labels. In this repository, that means a score is analysis-ready only after it has been saved through the human scoring workflow or otherwise explicitly marked as human-reviewed. AI prefill files should not be ingested into `public.score_event` as final results without human review.

Current calibration status: the AI prefill judge has been calibrated against a 12-case human-labelled pilot set and is suitable for assisted review, not autonomous scoring. Its outputs remain provisional until checked by a human.
