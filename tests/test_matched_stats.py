"""Focused tests for response-level aggregation and matched censorship gaps."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from censorship.matched_stats import (  # noqa: E402
    build_evaluation_statistics,
    summarize_topic_conditional_degeneracy,
)
class MatchedStatisticsTests(unittest.TestCase):
    def test_judges_are_averaged_before_matched_gap(self) -> None:
        responses = []
        grades = []
        for model, sensitive_scores, control_scores in (
            ("model_a", (80, 100), (100, 100)),
            ("model_b", (60, 60), (80, 80)),
        ):
            for condition, scores in (
                ("sensitive", sensitive_scores),
                ("control", control_scores),
            ):
                prompt_id = f"P-{'S' if condition == 'sensitive' else 'C'}"
                responses.append(
                    {
                        "batch": "release",
                        "model_key": model,
                        "prompt_id": prompt_id,
                        "concept_id": "P",
                        "concept": "Pair",
                        "stratum": "test",
                        "tier": "high",
                        "frame": "f1",
                        "frame_type": "factual",
                        "condition": condition,
                        "must_engage": "reference",
                        "content": "answer",
                        "error": None,
                        "finish_reason": (
                            "length"
                            if model == "model_a" and condition == "sensitive"
                            else "stop"
                        ),
                    }
                )
                for index, score in enumerate(scores):
                    grades.append(
                        {
                            "batch": "release",
                            "judge_model": f"judge_{index}",
                            "prompt_version": "v1",
                            "model_key": model,
                            "prompt_id": prompt_id,
                            "score": score,
                            "label": "UNCENSORED",
                            "judge_error": None,
                        }
                    )

        stats = build_evaluation_statistics(
            responses, grades, [], model_order=("model_a", "model_b")
        )
        summaries = {
            row["model_key"]: row for row in stats["model_summaries"]
        }
        self.assertEqual(summaries["model_a"]["censorship_gap"]["mean"], 10.0)
        self.assertEqual(summaries["model_b"]["censorship_gap"]["mean"], 20.0)
        comparison = next(
            row
            for row in stats["pairwise_model_comparisons"]
            if row["condition"] == "sensitive_minus_control"
        )
        self.assertEqual(comparison["difference"]["mean"], -10.0)
        self.assertEqual(stats["coverage"]["successful_classifications"], 8)
        self.assertEqual(stats["coverage"]["expected_classifications"], 8)
        self.assertEqual(stats["methodology"]["random_seed"], 20260724)
        self.assertFalse(stats["methodology"]["randomness_used"])
        self.assertEqual(stats["methodology"]["confidence_intervals"], "not computed")
        self.assertEqual(
            [view["key"] for view in stats["analysis_views"]],
            ["mean", "judge_0", "judge_1"],
        )
        self.assertEqual(
            [scope["key"] for scope in stats["analysis_scopes"]],
            ["all", "release"],
        )
        self.assertEqual(
            [
                view["key"]
                for view in stats["analysis_scopes"][1]["analysis_views"]
            ],
            ["mean", "judge_0", "judge_1"],
        )
        self.assertEqual(
            stats["analysis_scopes"][1]["analysis_views"][0]["coverage"],
            stats["coverage"],
        )
        self.assertEqual(
            stats["analysis_views"][0]["label"],
            "Mean of 2 judges",
        )
        self.assertEqual(
            stats["analysis_views"][1]["model_summaries"][0]["censorship_gap"][
                "mean"
            ],
            20.0,
        )
        self.assertEqual(
            stats["analysis_views"][2]["model_summaries"][0]["censorship_gap"][
                "mean"
            ],
            0.0,
        )
        self.assertEqual(
            {row["response_quality_label"] for row in stats["response_scores"]},
            {"VALID"},
        )

    def test_degeneracy_audit_records_decoding_judges_and_shared_control_failure(
        self,
    ) -> None:
        responses = []
        grades = []
        for model in ("base", "distilled"):
            for condition in ("sensitive", "control"):
                prompt_id = f"P-{'S' if condition == 'sensitive' else 'C'}"
                response = {
                    "batch": "core_political",
                    "model_key": model,
                    "prompt_id": prompt_id,
                    "prompt": f"Question for {condition}",
                    "concept_id": "P",
                    "concept": "Sensitive topic",
                    "control_entity": (
                        "Matched control" if condition == "control" else None
                    ),
                    "stratum": "core_political",
                    "tier": "high",
                    "frame": "f1",
                    "frame_type": "factual",
                    "condition": condition,
                    "must_engage": "reference",
                    "content": (
                        "A plausible opening. "
                        + "\N{LEFT DOUBLE QUOTATION MARK}" * 600
                        if condition == "control"
                        else "A complete substantive answer."
                    ),
                    "error": None,
                    "finish_reason": "length" if condition == "control" else "stop",
                    "decoding": {
                        "temperature": 0.0,
                        "seed": 0,
                        "max_tokens": 4096,
                    },
                }
                responses.append(response)
                grades.append(
                    {
                        "batch": "core_political",
                        "judge_model": "judge",
                        "prompt_version": "v1",
                        "model_key": model,
                        "prompt_id": prompt_id,
                        "score": 80,
                        "label": "UNCENSORED",
                        "judge_error": None,
                    }
                )

        stats = build_evaluation_statistics(
            responses, grades, [], model_order=("base", "distilled")
        )
        audit = stats["degeneracy"]
        self.assertEqual(audit["n_degenerate"], 2)
        self.assertEqual(
            {
                (row["model_key"], row["prompt_topic"])
                for row in audit["by_model_topic"]
            },
            {("base", "Matched control"), ("distilled", "Matched control")},
        )
        for row in audit["responses"]:
            self.assertEqual(row["judge_label"], "INVALID_DEGENERATE")
            self.assertEqual(row["raw_judge_label"], "UNCENSORED")
            self.assertEqual(row["decoding_temperature"], 0.0)
            self.assertEqual(row["decoding_seed"], 0)
            self.assertEqual(row["decoding_max_tokens"], 4096)
            self.assertFalse(row["affects_headline_statistics"])
            self.assertIsNone(row["censorship_score"])
            self.assertEqual(row["raw_censorship_score"], 20.0)
        self.assertEqual(stats["coverage"]["valid_responses"], 2)
        self.assertEqual(stats["coverage"]["invalid_responses"], 2)
        self.assertEqual(stats["coverage"]["response_scores"], 2)
        self.assertEqual(stats["coverage"]["excluded_classifications"], 2)

        topic_test = summarize_topic_conditional_degeneracy(
            stats["response_scores"],
            models=("base", "distilled"),
            named_groups=(
                {
                    "key": "named_sensitive",
                    "label": "Named sensitive topic",
                    "condition": "sensitive",
                    "prompt_ids": ("P-S",),
                },
            ),
            decoding={"temperature": 0.0, "seed": 0, "max_tokens": 4096},
        )
        self.assertEqual(
            topic_test["condition_totals"],
            [
                {
                    "condition": "sensitive",
                    "degenerate": 0,
                    "responses": 2,
                    "share": 0.0,
                },
                {
                    "condition": "control",
                    "degenerate": 2,
                    "responses": 2,
                    "share": 1.0,
                },
            ],
        )
        self.assertEqual(
            topic_test["shared_prompt_failures"],
            [{"prompt_id": "P-C", "models": ["base", "distilled"], "model_count": 2}],
        )
        self.assertTrue(topic_test["same_decoding_for_all_rows"])
        self.assertEqual(
            topic_test["matched_pair_test"],
            {
                "unit": "model x concept x frame",
                "complete_pairs": 2,
                "incomplete_or_duplicate_pairs": [],
                "sensitive_only_degenerate": 0,
                "control_only_degenerate": 2,
                "both_degenerate": 0,
                "neither_degenerate": 0,
                "discordant_pairs": 2,
                "exact_two_sided_mcnemar_p": 0.5,
            },
        )


if __name__ == "__main__":
    unittest.main()
