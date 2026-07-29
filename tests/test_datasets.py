"""Release benchmark identity and pairing regression tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from censorship.datasets import load_paths, load_set  # noqa: E402


class BenchmarkVersionTests(unittest.TestCase):
    def test_declared_versions_and_sizes(self) -> None:
        expected = {
            "finance_prompts": ("matched_v2", 152),
            "core_political_matched_prompts": ("matched_v2", 152),
        }
        for name, (version, size) in expected.items():
            with self.subTest(name=name):
                rows = load_set(name)
                self.assertEqual(len(rows), size)
                self.assertEqual({row["benchmark_version"] for row in rows}, {version})

    def test_complete_release_has_unique_ids_and_balanced_conditions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rows = load_paths(
            root / "data/benchmark/finance_prompts.jsonl",
            root / "data/benchmark/core_political_matched_prompts.jsonl",
        )
        self.assertEqual(len(rows), 304)
        self.assertEqual(len({row["prompt_id"] for row in rows}), 304)
        self.assertEqual(
            {
                condition: sum(row["condition"] == condition for row in rows)
                for condition in ("sensitive", "control")
            },
            {"sensitive": 152, "control": 152},
        )


if __name__ == "__main__":
    unittest.main()
