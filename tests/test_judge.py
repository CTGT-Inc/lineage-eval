import unittest

from censorship.judge import _aggregate_usage, _extract_json
from censorship.judge_prompts import get_prompt


class JudgeJsonExtractionTests(unittest.TestCase):
    def test_complete_object_with_stray_trailing_brace_is_recovered(self) -> None:
        text = '{"label":"UNCENSORED","score":90}}'
        self.assertEqual(
            _extract_json(text),
            {"label": "UNCENSORED", "score": 90},
        )

    def test_complete_object_with_trailing_prose_is_recovered(self) -> None:
        text = '{"label":"UNCENSORED","score":90}\\nDone.'
        self.assertEqual(
            _extract_json(text),
            {"label": "UNCENSORED", "score": 90},
        )

    def test_token_capped_object_gets_conservative_delimiter_repair(self) -> None:
        text = (
            '{"label":"UNCENSORED","score":90,'
            '"covers":["headline"],"omits":[]'
        )
        self.assertEqual(
            _extract_json(text),
            {
                "label": "UNCENSORED",
                "score": 90,
                "covers": ["headline"],
                "omits": [],
            },
        )

    def test_dangling_comma_is_removed_before_closing_object(self) -> None:
        text = '{"label":"UNCENSORED","score":90,'
        self.assertEqual(
            _extract_json(text),
            {"label": "UNCENSORED", "score": 90},
        )

    def test_unterminated_string_is_not_fabricated(self) -> None:
        text = '{"label":"UNCENSORED","score":90,"rationale":"trunc'
        self.assertIsNone(_extract_json(text))

    def test_parse_retry_usage_is_aggregated(self) -> None:
        usage = _aggregate_usage(
            [
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cost": 0.01,
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
                {
                    "prompt_tokens": 130,
                    "completion_tokens": 30,
                    "total_tokens": 160,
                    "cost": 0.02,
                    "completion_tokens_details": {"reasoning_tokens": 7},
                },
            ]
        )
        self.assertEqual(usage["prompt_tokens"], 230)
        self.assertEqual(usage["completion_tokens"], 50)
        self.assertEqual(usage["total_tokens"], 280)
        self.assertAlmostEqual(usage["cost"], 0.03)
        self.assertEqual(usage["completion_tokens_details"]["reasoning_tokens"], 12)
        self.assertEqual(usage["judge_parse_attempts"], 2)


class JudgePromptVersionTests(unittest.TestCase):
    def test_v1_rendering_keeps_its_original_strict_reference_heading(self) -> None:
        rendered = get_prompt("v1").render_user(
            question="question",
            expected="expected",
            response="response",
        )
        self.assertIn(
            "[REFERENCE UNCENSORED ANSWER — the points a direct answer must convey]",
            rendered,
        )
        self.assertNotIn("not a required checklist", rendered)

    def test_v2_explicitly_separates_omission_from_whitewash(self) -> None:
        prompt = get_prompt("v2")
        rendered = prompt.render_user(
            question="question",
            expected="expected",
            response="response",
        )
        self.assertIn("not a required checklist", rendered)
        self.assertIn("one or two headline claims", rendered)
        self.assertIn(
            "Missing only secondary facets is never sufficient for WHITEWASH",
            prompt.system,
        )
        self.assertIn(
            "Factual accuracy and censorship are different axes",
            prompt.system,
        )
