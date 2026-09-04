from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/results/ox-alpha-v1/matched-v2-core-political.json"
RELEASE = ROOT / "release/huggingface/ox-alpha-v1"
EXPORTER = ROOT / "tools/export_ox_alpha_release.mjs"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class OxAlphaReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = json.loads(SOURCE.read_text(encoding="utf-8"))
        cls.responses = read_jsonl(RELEASE / "data/responses/evaluation.jsonl")
        cls.judgments = read_jsonl(RELEASE / "data/judgments/evaluation.jsonl")
        cls.model = json.loads((RELEASE / "metadata/model.json").read_text())

    def test_counts_and_statistics(self) -> None:
        self.assertEqual(len(self.source["prompts"]), 152)
        self.assertEqual(len(self.responses), 152)
        self.assertEqual(len(self.judgments), 604)
        self.assertEqual(
            Counter(row["response_quality_label"] for row in self.responses),
            {"VALID": 151, "INVALID_DEGENERATE": 1},
        )
        self.assertEqual(self.source["statistics"]["cards_v1"]["estimate"], 7.42)
        self.assertEqual(self.source["statistics"]["cards_v1"]["n"], 75)
        self.assertEqual(self.source["statistics"]["cards_v2"]["estimate"], 6.05)
        self.assertEqual(self.source["statistics"]["cards_v2"]["n"], 75)

    def test_provenance_and_lineage_are_explicit(self) -> None:
        self.assertEqual(self.model["display_name"], "Ox Alpha")
        self.assertEqual(self.model["served_model"], "stealth/ox-alpha")
        self.assertEqual(self.model["provenance_status"], "undisclosed")
        self.assertFalse(self.model["lineage_claim"])
        self.assertIsNone(self.model["identity_claim"])
        serialized = json.dumps(self.source)
        self.assertNotIn("GLM-5.3-Flash", serialized)
        self.assertNotIn('"origin": "Zhipu"', serialized)

    def test_judgment_coverage_matches_quality_policy(self) -> None:
        by_response = Counter(row["response_id"] for row in self.judgments)
        for response in self.responses:
            expected = 4 if response["response_quality_label"] == "VALID" else 0
            self.assertEqual(by_response[response["response_id"]], expected)

    def test_manifest_and_export_are_reproducible(self) -> None:
        manifest = json.loads((RELEASE / "MANIFEST.json").read_text())
        self.assertEqual(
            manifest["row_counts"],
            {"benchmark": 152, "responses": 152, "judgments": 604, "models": 1, "judges": 4},
        )
        for entry in manifest["files"]:
            path = RELEASE / entry["path"]
            content = path.read_bytes()
            self.assertEqual(len(content), entry["bytes"])
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"])

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "release"
            subprocess.run(
                ["node", str(EXPORTER), str(SOURCE), str(destination)],
                check=True,
                capture_output=True,
                text=True,
            )
            expected = {
                path.relative_to(RELEASE) for path in RELEASE.rglob("*") if path.is_file()
            }
            actual = {
                path.relative_to(destination)
                for path in destination.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)
            for relative in expected:
                self.assertEqual(
                    (destination / relative).read_bytes(),
                    (RELEASE / relative).read_bytes(),
                    relative,
                )


if __name__ == "__main__":
    unittest.main()
