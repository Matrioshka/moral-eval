from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "log_prob"))

from run_action_logprob_mvp import (  # noqa: E402
    DEFAULT_DATA,
    EXPECTED_CONDITIONS,
    EXPECTED_MAPPINGS,
    SmokeTestError,
    build_prompt,
    calculate_effects,
    expand_prompt_instances,
    load_case,
    semantic_margin,
    single_token_id_at_generation_boundary,
    validate_case,
)


class CharacterTokenizer:
    chat_template = "character-test-template"

    @staticmethod
    def _render(messages: list[dict[str, str]]) -> str:
        return f"<user>{messages[0]['content']}<assistant>"

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
        **_: object,
    ) -> str | list[int]:
        if not add_generation_prompt:
            raise AssertionError("Test tokenizer expects an assistant-generation boundary")
        rendered = self._render(messages)
        if tokenize:
            return self.encode(rendered, add_special_tokens=False)
        return rendered

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        if add_special_tokens:
            raise AssertionError("Tests require add_special_tokens=False")
        return [ord(character) for character in text]

    def decode(
        self,
        token_ids: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        del skip_special_tokens, clean_up_tokenization_spaces
        return "".join(chr(token_id) for token_id in token_ids)


class BoundaryMergingTokenizer(CharacterTokenizer):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        if text.endswith(">A"):
            prefix_without_final_boundary_character = super().encode(
                text[:-2], add_special_tokens=add_special_tokens
            )
            return prefix_without_final_boundary_character + [9999]
        return super().encode(text, add_special_tokens=add_special_tokens)


class ActionLogprobDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = load_case(DEFAULT_DATA)

    def test_curated_dataset_validates_and_expands_to_six_instances(self) -> None:
        validate_case(self.case)

        instances = expand_prompt_instances(self.case)

        self.assertEqual(tuple(instances), EXPECTED_MAPPINGS)
        self.assertEqual(sum(len(group) for group in instances.values()), 6)
        for mapping_instances in instances.values():
            self.assertEqual(tuple(mapping_instances), EXPECTED_CONDITIONS)

    def test_mappings_counterbalance_labels_and_presentation_order(self) -> None:
        instances = expand_prompt_instances(self.case)
        bounded_text = self.case["semantic_actions"]["bounded"]["text"]
        broader_text = self.case["semantic_actions"]["broader"]["text"]
        first_prompt = instances["bounded_A_broader_B"]["unresolved_neutral"]["prompt"]
        second_prompt = instances["broader_A_bounded_B"]["unresolved_neutral"]["prompt"]

        self.assertLess(first_prompt.index(f"A. {bounded_text}"), first_prompt.index(f"B. {broader_text}"))
        self.assertLess(second_prompt.index(f"A. {broader_text}"), second_prompt.index(f"B. {bounded_text}"))
        self.assertIn(
            "Select B.",
            instances["bounded_A_broader_B"]["unresolved_directive"]["prompt"],
        )
        self.assertIn(
            "Select A.",
            instances["broader_A_bounded_B"]["unresolved_directive"]["prompt"],
        )

    def test_evidence_states_are_direct_structural_counterparts(self) -> None:
        unresolved = self.case["evidence_states"]["unresolved"]["statements"]
        resolved = self.case["evidence_states"]["resolved"]["statements"]

        self.assertEqual(len(unresolved), len(resolved))
        self.assertEqual(unresolved[0], resolved[0])
        self.assertIn("still incomplete", unresolved[1])
        self.assertIn("complete and passed", resolved[1])
        self.assertIn("has not been validated", unresolved[2])
        self.assertIn("has been validated", resolved[2])

    def test_boundary_metadata_cannot_affect_prompt_construction(self) -> None:
        original = expand_prompt_instances(self.case)
        changed = copy.deepcopy(self.case)
        for state in changed["evidence_states"].values():
            state["independently_justified_action"] = "METADATA_ACTION_SENTINEL"
            state["boundary_justification"] = "METADATA_JUSTIFICATION_SENTINEL"

        changed_instances = expand_prompt_instances(changed)

        for mapping_id in EXPECTED_MAPPINGS:
            for condition_id in EXPECTED_CONDITIONS:
                original_prompt = original[mapping_id][condition_id]["prompt"]
                changed_prompt = changed_instances[mapping_id][condition_id]["prompt"]
                self.assertEqual(original_prompt, changed_prompt)
                self.assertNotIn("METADATA_ACTION_SENTINEL", changed_prompt)
                self.assertNotIn("METADATA_JUSTIFICATION_SENTINEL", changed_prompt)

    def test_validation_rejects_changed_semantic_action_text(self) -> None:
        invalid = copy.deepcopy(self.case)
        invalid["mappings"][1]["actions"][0]["text"] += " Unmatched addition."

        with self.assertRaisesRegex(SmokeTestError, "changes the broader semantic action text"):
            validate_case(invalid)

    def test_validation_rejects_missing_condition(self) -> None:
        invalid = copy.deepcopy(self.case)
        invalid["conditions"].pop()

        with self.assertRaisesRegex(SmokeTestError, "exactly 3 entries"):
            validate_case(invalid)

    def test_neutral_conditions_differ_only_in_evidence(self) -> None:
        mapping = self.case["mappings"][0]
        conditions = {item["condition_id"]: item for item in self.case["conditions"]}

        unresolved_prompt = build_prompt(
            self.case, mapping, conditions["unresolved_neutral"]
        )
        resolved_prompt = build_prompt(self.case, mapping, conditions["resolved_neutral"])
        unresolved_evidence = " ".join(
            self.case["evidence_states"]["unresolved"]["statements"]
        )
        resolved_evidence = " ".join(
            self.case["evidence_states"]["resolved"]["statements"]
        )

        self.assertEqual(
            unresolved_prompt.replace(unresolved_evidence, "<EVIDENCE>"),
            resolved_prompt.replace(resolved_evidence, "<EVIDENCE>"),
        )


class ActionLogprobTokenValidationTests(unittest.TestCase):
    def test_labels_are_exact_single_tokens_at_rendered_boundary(self) -> None:
        tokenizer = CharacterTokenizer()

        self.assertEqual(
            single_token_id_at_generation_boundary(tokenizer, "Prompt", "A"), ord("A")
        )
        self.assertEqual(
            single_token_id_at_generation_boundary(tokenizer, "Prompt", "B"), ord("B")
        )

    def test_boundary_retokenisation_is_rejected(self) -> None:
        with self.assertRaisesRegex(SmokeTestError, "retokenises"):
            single_token_id_at_generation_boundary(
                BoundaryMergingTokenizer(), "Prompt", "A"
            )


class ActionLogprobCalculationTests(unittest.TestCase):
    def test_semantic_margin_follows_roles_not_literal_b(self) -> None:
        logits = {"A": 1.25, "B": 4.0}

        self.assertEqual(
            semantic_margin(logits, bounded_label="A", broader_label="B"), 2.75
        )
        self.assertEqual(
            semantic_margin(logits, bounded_label="B", broader_label="A"), -2.75
        )

    def test_mapping_effects_are_primary_and_means_are_secondary(self) -> None:
        results = {
            "bounded_A_broader_B": {
                "unresolved_neutral": {"broad_action_logit_margin": 1.0},
                "unresolved_directive": {"broad_action_logit_margin": 3.0},
                "resolved_neutral": {"broad_action_logit_margin": 6.0},
            },
            "broader_A_bounded_B": {
                "unresolved_neutral": {"broad_action_logit_margin": -1.0},
                "unresolved_directive": {"broad_action_logit_margin": 0.0},
                "resolved_neutral": {"broad_action_logit_margin": 2.0},
            },
        }

        effects = calculate_effects(results)

        primary = effects["primary_mapping_specific"]
        self.assertEqual(primary["bounded_A_broader_B"]["directive_effect"], 2.0)
        self.assertEqual(primary["bounded_A_broader_B"]["resolution_effect"], 5.0)
        self.assertEqual(primary["broader_A_bounded_B"]["directive_effect"], 1.0)
        self.assertEqual(primary["broader_A_bounded_B"]["resolution_effect"], 3.0)
        secondary = effects["secondary_summary"]
        self.assertEqual(secondary["mean_directive_effect_across_mappings"], 1.5)
        self.assertEqual(secondary["mean_resolution_effect_across_mappings"], 4.0)
        self.assertIn("secondary", secondary["summary_role"])


if __name__ == "__main__":
    unittest.main()
