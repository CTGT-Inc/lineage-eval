"""Platform-independent tests for the public release configuration and CLI."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from censorship.cli import main  # noqa: E402
from censorship.config import load_config  # noqa: E402
from censorship.generation import generation_plan  # noqa: E402


class ReleaseConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ROOT / "configs" / "eval.toml"
        self.config = load_config(self.path)

    def test_release_config_resolves_paths_and_exact_panel(self) -> None:
        self.assertEqual(self.config.run_id, "eval")
        self.assertEqual(len(self.config.benchmark.files), 2)
        self.assertTrue(all(path.is_file() for path in self.config.benchmark.files))
        self.assertEqual(
            self.config.generation.model_keys,
            ("model-under-test",),
        )
        self.assertEqual(len(self.config.judging.judges), 4)
        self.assertEqual(
            {judge.transport for judge in self.config.judging.judges},
            {"openrouter", "openai-batch", "anthropic-batch"},
        )

    def test_plan_accepts_arbitrary_served_model_names(self) -> None:
        plan = generation_plan(
            self.config,
            models=["home-trained-model", "released-model"],
            limit=2,
        )
        self.assertEqual(plan["prompts"], 2)
        self.assertEqual(
            plan["models"],
            ["home-trained-model", "released-model"],
        )

    def test_endpoint_override_changes_only_base_url(self) -> None:
        plan = generation_plan(
            self.config,
            base_url="http://gpu.example:8000/v1",
            models=["custom"],
            limit=1,
        )
        self.assertEqual(plan["backend"], "openai-compatible")
        self.assertEqual(plan["base_url"], "http://gpu.example:8000/v1")
        self.assertEqual(plan["models"], ["custom"])

    def test_static_doctor_runs_without_gpu_or_api_keys(self) -> None:
        code = main(
            [
                "doctor",
                "--config",
                str(self.path),
                "--static",
                "--skip-api-keys",
                "--model",
                "home-trained-model",
            ]
        )
        self.assertEqual(code, 0)

    @patch("censorship.cli.httpx.get")
    def test_doctor_confirms_served_model_name(self, get: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "data": [{"id": "home-trained-model"}]
        }
        get.return_value = response
        code = main(
            [
                "doctor",
                "--config",
                str(self.path),
                "--skip-api-keys",
                "--base-url",
                "http://gpu.example:9000/v1",
                "--model",
                "home-trained-model",
            ]
        )
        self.assertEqual(code, 0)
        get.assert_called_once()
        self.assertEqual(
            get.call_args.args[0],
            "http://gpu.example:9000/v1/models",
        )


if __name__ == "__main__":
    unittest.main()
