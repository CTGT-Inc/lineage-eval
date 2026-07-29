"""Focused tests for OpenAI-compatible inference request construction."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from censorship.inference import (  # noqa: E402
    RecordSink,
    _generate_all,
)


class _RecordingSink(RecordSink):
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.checkpoints = 0

    def write(self, rec: dict) -> None:
        self.rows.append(rec)

    def checkpoint(self) -> None:
        self.checkpoints += 1


class EndpointGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_top_p_and_per_prompt_seed_are_sent_and_recorded(self) -> None:
        calls: list[dict] = []
        clients: list[dict] = []

        class Completions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                message = types.SimpleNamespace(
                    content="answer",
                    reasoning="reasoning",
                    reasoning_content=None,
                )
                choice = types.SimpleNamespace(message=message, finish_reason="stop")
                usage = types.SimpleNamespace(
                    model_dump=lambda: {
                        "prompt_tokens": 4,
                        "completion_tokens": 8,
                    }
                )
                return types.SimpleNamespace(choices=[choice], usage=usage)

        class AsyncOpenAI:
            def __init__(self, **kwargs):
                clients.append(kwargs)
                self.chat = types.SimpleNamespace(completions=Completions())

            async def close(self):
                return None

        fake_openai = types.SimpleNamespace(AsyncOpenAI=AsyncOpenAI)
        sink = _RecordingSink()
        prompts = [
            {"prompt_id": "default", "prompt": "Default"},
            {
                "prompt_id": "override",
                "prompt": "Override",
                "_decoding": {
                    "temperature": 0.7,
                    "top_p": 0.85,
                    "max_tokens": 512,
                    "seed": 99,
                },
            },
        ]
        with patch.dict(sys.modules, {"openai": fake_openai}):
            rows = await _generate_all(
                prompts,
                served_name="test-model",
                temperature=1.0,
                top_p=0.9,
                max_tokens=4096,
                seed=20260727,
                concurrency=2,
                sink=sink,
                checkpoint_every=2,
                base_url="http://gpu.example:9000/v1",
                api_key="endpoint-secret",
            )

        by_prompt = {row["prompt_id"]: row for row in rows}
        by_message = {call["messages"][0]["content"]: call for call in calls}
        self.assertEqual(
            by_prompt["default"]["decoding"],
            {
                "temperature": 1.0,
                "top_p": 0.9,
                "max_tokens": 4096,
                "seed": 20260727,
            },
        )
        self.assertEqual(by_message["Default"]["top_p"], 0.9)
        self.assertEqual(by_message["Default"]["seed"], 20260727)
        self.assertEqual(by_message["Override"]["temperature"], 0.7)
        self.assertEqual(by_message["Override"]["top_p"], 0.85)
        self.assertEqual(by_message["Override"]["max_tokens"], 512)
        self.assertEqual(by_message["Override"]["seed"], 99)
        self.assertEqual(len(sink.rows), 2)
        self.assertEqual(sink.checkpoints, 1)
        self.assertEqual(clients[0]["base_url"], "http://gpu.example:9000/v1")
        self.assertEqual(clients[0]["api_key"], "endpoint-secret")
        self.assertEqual(by_prompt["default"]["backend"], "openai-compatible")
        self.assertEqual(by_prompt["default"]["prompt"], "Default")


if __name__ == "__main__":
    unittest.main()
