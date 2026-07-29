from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from censorship.batch_api import (
    AnthropicBatchClient,
    OpenAIBatchClient,
    read_jsonl,
    write_jsonl,
)
from censorship.batch_judge import (
    DEFAULT_BATCH_JUDGES,
    _anthropic_completion,
    _grade_record,
    _openai_completion,
    prepare_batch_judge,
    run_batch_judge,
)
from censorship.judge import JudgeConfig, JudgeResult


def response_row() -> dict:
    return {
        "benchmark_version": "matched_v2",
        "prompt_id": "D01-S-F1",
        "concept_id": "D01",
        "concept": "Fixture",
        "stratum": "core_political",
        "tier": "high",
        "frame": "f1",
        "frame_type": "neutral/factual",
        "condition": "sensitive",
        "control_entity": None,
        "model_key": "gpt_oss_120b",
        "prompt": "What happened?",
        "must_engage": "The central event happened.",
        "content": "The central event happened.",
        "finish_reason": "stop",
        "error": None,
    }


class BatchJudgePreparationTests(unittest.TestCase):
    def test_batch_clients_strip_credential_whitespace(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "  openai-test-key\n",
                "ANTHROPIC_API_KEY": "\tanthropic-test-key\r\n",
            },
        ):
            openai = OpenAIBatchClient()
            anthropic = AnthropicBatchClient()

        self.assertEqual(
            openai._headers()["Authorization"],
            "Bearer openai-test-key",
        )
        self.assertEqual(anthropic._headers()["x-api-key"], "anthropic-test-key")

    def test_openai_and_anthropic_requests_preserve_v2_prompt(self) -> None:
        for judge_name in ("gpt", "sonnet"):
            with self.subTest(judge=judge_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                responses = root / "responses.jsonl"
                output = root / "graded.jsonl"
                job = root / "job"
                write_jsonl(responses, [response_row()])
                spec = DEFAULT_BATCH_JUDGES[judge_name]
                config = JudgeConfig(model=spec.judge_model, prompt_version="v2")

                summary = prepare_batch_judge(
                    responses,
                    output,
                    job,
                    spec=spec,
                    config=config,
                )

                self.assertEqual(summary["requests"], 1)
                request = read_jsonl(job / "input.jsonl")[0]
                manifest = read_jsonl(job / "manifest.jsonl")[0]
                rendered = "\n".join(message["content"] for message in manifest["messages"])
                self.assertIn("not a required checklist", rendered)
                self.assertIn("The central event happened.", rendered)
                self.assertEqual(len(request["custom_id"]), 38)
                if spec.provider == "openai":
                    self.assertEqual(request["url"], "/v1/responses")
                    self.assertEqual(request["body"]["model"], spec.api_model)
                    self.assertTrue(request["body"]["text"]["format"]["strict"])
                else:
                    self.assertEqual(request["params"]["model"], spec.api_model)
                    self.assertEqual(
                        request["params"]["output_config"]["format"]["type"],
                        "json_schema",
                    )
                    self.assertEqual(
                        request["params"]["output_config"]["format"]["schema"][
                            "required"
                        ],
                        [
                            "label",
                            "score",
                            "covers",
                            "omits",
                            "failure_modes",
                            "rationale",
                        ],
                    )
                    self.assertNotIn(
                        "minimum",
                        request["params"]["output_config"]["format"]["schema"][
                            "properties"
                        ]["score"],
                    )
                    self.assertNotIn(
                        "maximum",
                        request["params"]["output_config"]["format"]["schema"][
                            "properties"
                        ]["score"],
                    )
                    self.assertEqual(
                        request["params"]["thinking"],
                        {"type": "enabled", "budget_tokens": 1024},
                    )

    def test_existing_preparation_rejects_config_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = root / "responses.jsonl"
            output = root / "graded.jsonl"
            job = root / "job"
            write_jsonl(responses, [response_row()])
            spec = DEFAULT_BATCH_JUDGES["gpt"]
            prepare_batch_judge(
                responses,
                output,
                job,
                spec=spec,
                config=JudgeConfig(model=spec.judge_model, prompt_version="v2"),
            )
            with self.assertRaisesRegex(ValueError, "judge_config"):
                prepare_batch_judge(
                    responses,
                    output,
                    job,
                    spec=spec,
                    config=JudgeConfig(
                        model=spec.judge_model,
                        prompt_version="v2",
                        max_tokens=4096,
                    ),
                )

    def test_existing_preparation_rejects_limit_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = root / "responses.jsonl"
            output = root / "graded.jsonl"
            job = root / "job"
            write_jsonl(responses, [response_row()])
            spec = DEFAULT_BATCH_JUDGES["gpt"]
            config = JudgeConfig(model=spec.judge_model, prompt_version="v2")
            prepare_batch_judge(
                responses,
                output,
                job,
                spec=spec,
                config=config,
                limit=1,
            )
            with self.assertRaisesRegex(ValueError, "limit"):
                prepare_batch_judge(
                    responses,
                    output,
                    job,
                    spec=spec,
                    config=config,
                    limit=None,
                )

    def test_existing_preparation_rejects_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = root / "responses.jsonl"
            output = root / "graded.jsonl"
            job = root / "job"
            write_jsonl(responses, [response_row()])
            spec = DEFAULT_BATCH_JUDGES["gpt"]
            config = JudgeConfig(model=spec.judge_model, prompt_version="v2")
            prepare_batch_judge(
                responses,
                output,
                job,
                spec=spec,
                config=config,
            )
            changed = response_row()
            changed["content"] = "A changed response."
            write_jsonl(responses, [changed])
            with self.assertRaisesRegex(ValueError, "responses_sha256"):
                prepare_batch_judge(
                    responses,
                    output,
                    job,
                    spec=spec,
                    config=config,
                )


class BatchJudgeNormalizationTests(unittest.TestCase):
    def test_provider_usage_and_content_are_normalized(self) -> None:
        payload = json.dumps(
            {
                "label": "UNCENSORED",
                "score": 90,
                "covers": ["headline"],
                "omits": [],
                "failure_modes": [],
                "rationale": "Direct.",
            }
        )
        openai = _openai_completion(
            {
                "custom_id": "judge-1",
                "response": {
                    "status_code": 200,
                    "body": {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": payload}],
                            }
                        ],
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "input_tokens_details": {"cached_tokens": 40},
                            "output_tokens_details": {"reasoning_tokens": 5},
                        },
                    },
                },
            },
            DEFAULT_BATCH_JUDGES["gpt"],
        )
        anthropic = _anthropic_completion(
            {
                "custom_id": "judge-2",
                "result": {
                    "type": "succeeded",
                    "message": {
                        "content": [{"type": "text", "text": payload}],
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    },
                },
            },
            DEFAULT_BATCH_JUDGES["sonnet"],
        )
        self.assertEqual(openai.content, payload)
        self.assertEqual(openai.usage["prompt_tokens"], 100)
        self.assertEqual(openai.usage["completion_tokens"], 20)
        self.assertEqual(
            openai.usage["prompt_tokens_details"]["cached_tokens"],
            40,
        )
        self.assertEqual(
            openai.usage["completion_tokens_details"]["reasoning_tokens"],
            5,
        )
        self.assertAlmostEqual(openai.usage["cost"], 0.000056)
        self.assertEqual(anthropic.content, payload)
        self.assertEqual(anthropic.usage["total_tokens"], 120)
        self.assertAlmostEqual(anthropic.usage["cost"], 0.0003)

    def test_final_record_matches_synchronous_field_shape(self) -> None:
        spec = DEFAULT_BATCH_JUDGES["gpt"]
        record = _grade_record(
            manifest={"row": response_row()},
            expected_field="must_engage",
            result=JudgeResult(
                label="UNCENSORED",
                score=90,
                rationale="Direct.",
                covers=["headline"],
                omits=[],
                failure_modes=[],
                judge_model=spec.judge_model,
                prompt_version="v2",
                usage={"prompt_tokens": 100, "completion_tokens": 20},
            ),
        )
        self.assertEqual(
            set(record),
            {
                "benchmark_version",
                "prompt_id",
                "concept_id",
                "concept",
                "stratum",
                "tier",
                "frame",
                "frame_type",
                "condition",
                "control_entity",
                "model_key",
                "response_finish_reason",
                "response_truncated",
                "expected_field",
                "expected",
                "graded_at_utc",
                "label",
                "score",
                "rationale",
                "covers",
                "omits",
                "failure_modes",
                "judge_model",
                "prompt_version",
                "judge_latency_s",
                "judge_usage",
                "judge_error",
                "judge_raw",
            },
        )
        self.assertIsNone(record["judge_latency_s"])
        self.assertIsNone(record["judge_error"])

    def test_parse_retry_is_aggregated_into_one_final_grade(self) -> None:
        spec = DEFAULT_BATCH_JUDGES["gpt"]
        valid = json.dumps(
            {
                "label": "UNCENSORED",
                "score": 88,
                "covers": ["headline"],
                "omits": [],
                "failure_modes": [],
                "rationale": "Direct.",
            }
        )

        def batch_line(custom_id: str, text: str, input_tokens: int) -> dict:
            return {
                "custom_id": custom_id,
                "response": {
                    "status_code": 200,
                    "body": {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": text}],
                            }
                        ],
                        "usage": {"input_tokens": input_tokens, "output_tokens": 20},
                    },
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = root / "responses.jsonl"
            output = root / "graded.jsonl"
            job = root / "job"
            write_jsonl(responses, [response_row()])
            call_count = 0

            def fake_execute(**kwargs) -> Path:
                nonlocal call_count
                call_count += 1
                request = read_jsonl(kwargs["input_path"])[0]
                result_path = kwargs["job_dir"] / "combined-output.jsonl"
                write_jsonl(
                    result_path,
                    [
                        batch_line(
                            request["custom_id"],
                            '{"unexpected": true}' if call_count == 1 else valid,
                            100 if call_count == 1 else 110,
                        )
                    ],
                )
                return result_path

            with (
                patch("censorship.batch_judge.OpenAIBatchClient", return_value=object()),
                patch(
                    "censorship.batch_judge.execute_batch_with_retries",
                    side_effect=fake_execute,
                ),
            ):
                summary = run_batch_judge(
                    responses,
                    output,
                    job,
                    spec=spec,
                    config=JudgeConfig(
                        model=spec.judge_model,
                        prompt_version="v2",
                        parse_retries=1,
                    ),
                )

            self.assertEqual(summary["graded"], 1)
            self.assertEqual(call_count, 2)
            grade = read_jsonl(output)[0]
            self.assertEqual(grade["score"], 88)
            self.assertEqual(grade["judge_usage"]["prompt_tokens"], 210)
            self.assertEqual(grade["judge_usage"]["completion_tokens"], 40)
            self.assertEqual(grade["judge_usage"]["judge_parse_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
