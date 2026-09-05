from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = REPOSITORY_ROOT / "release" / "huggingface" / "blog-v1"
SOURCE_PATH = (
    REPOSITORY_ROOT
    / "data"
    / "results"
    / "blog-v1"
    / "matched-v2-full-data.json"
)
ANNOTATIONS_PATH = (
    REPOSITORY_ROOT / "data" / "annotations" / "blog-v1" / "human-labels.json"
)
OX_ALPHA_RESPONSES_PATH = (
    REPOSITORY_ROOT / "data" / "results" / "ox-alpha-v1" / "responses.jsonl"
)
EXPORTER_PATH = REPOSITORY_ROOT / "tools" / "export_huggingface_release.mjs"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class HuggingFaceReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
        cls.benchmark = read_jsonl(
            RELEASE_ROOT / "data" / "benchmark" / "evaluation.jsonl"
        )
        cls.responses = [
            row
            for path in sorted((RELEASE_ROOT / "data" / "responses").glob("*.jsonl"))
            for row in read_jsonl(path)
        ]
        cls.judgments = read_jsonl(
            RELEASE_ROOT / "data" / "judgments" / "evaluation.jsonl"
        )
        cls.human_annotations = read_jsonl(
            RELEASE_ROOT / "data" / "human_annotations" / "evaluation.jsonl"
        )
        cls.viewer = json.loads(
            (RELEASE_ROOT / "viewer" / "viewer_payload.json").read_text(
                encoding="utf-8"
            )
        )
        cls.run_metadata = json.loads(
            (RELEASE_ROOT / "metadata" / "run.json").read_text(encoding="utf-8")
        )

    def test_counts_and_unique_keys(self) -> None:
        self.assertEqual(len(self.benchmark), 304)
        self.assertEqual(len(self.responses), 1_976)
        self.assertEqual(len(self.judgments), 7_296)
        self.assertEqual(len(self.human_annotations), 96)

        self.assertEqual(len({row["prompt_id"] for row in self.benchmark}), 304)
        self.assertEqual(
            len({row["response_id"] for row in self.responses}), 1_976
        )
        self.assertEqual(
            len({row["judgment_id"] for row in self.judgments}), 7_296
        )
        self.assertEqual(
            len({row["annotation_id"] for row in self.human_annotations}), 96
        )

    def test_normalized_joins_are_complete(self) -> None:
        prompt_ids = {row["prompt_id"] for row in self.benchmark}
        response_ids = {row["response_id"] for row in self.responses}

        self.assertTrue(
            all(row["prompt_id"] in prompt_ids for row in self.responses)
        )
        self.assertTrue(
            all(row["prompt_id"] in prompt_ids for row in self.judgments)
        )
        self.assertTrue(
            all(row["response_id"] in response_ids for row in self.judgments)
        )
        self.assertEqual(
            Counter(Counter(row["prompt_id"] for row in self.responses).values()),
            {6: 152, 7: 152},
        )
        self.assertEqual(
            set(Counter(row["response_id"] for row in self.judgments).values()),
            {4},
        )

    def test_quality_exclusions_are_explicit_and_consistent(self) -> None:
        valid = [
            row for row in self.responses if row["response_quality_label"] == "VALID"
        ]
        invalid = [
            row
            for row in self.responses
            if row["response_quality_label"] == "INVALID_DEGENERATE"
        ]
        included_judgments = [
            row for row in self.judgments if row["included_in_statistics"]
        ]
        excluded_judgments = [
            row for row in self.judgments if not row["included_in_statistics"]
        ]

        self.assertEqual(len(valid), 1_789)
        self.assertEqual(len(invalid), 187)
        self.assertEqual(len(included_judgments), 6_552)
        self.assertEqual(len(excluded_judgments), 744)
        self.assertTrue(all(row["included_in_statistics"] for row in valid))
        self.assertTrue(
            all(row["mean_fidelity_score"] is not None for row in valid)
        )
        self.assertTrue(
            all(not row["included_in_statistics"] for row in invalid)
        )
        self.assertTrue(
            all(row["mean_fidelity_score"] is None for row in invalid)
        )
        self.assertTrue(
            all(row["censorship_score"] is None for row in excluded_judgments)
        )

    def test_reference_and_annotation_limitations_are_machine_readable(self) -> None:
        self.assertEqual(
            {row["reference_card_status"] for row in self.benchmark},
            {"model_drafted_unverified"},
        )
        self.assertEqual(
            Counter(row["provenance_status"] for row in self.benchmark),
            {
                "authored_for_matched_v2": 152,
                "upstream_inventory_influence_not_fully_mapped": 152,
            },
        )
        self.assertEqual(
            {row["annotation_scope"] for row in self.human_annotations},
            {"partial_pilot"},
        )
        self.assertEqual(
            len({row["prompt_id"] for row in self.human_annotations}), 24
        )
        self.assertEqual(
            {row["model_key"] for row in self.human_annotations},
            {
                "gpt_oss_120b",
                "self_distilled",
                "v4_flash_distilled",
                "v4_flash",
            },
        )

    def test_viewer_payload_preserves_results_and_sanitizes_runs(self) -> None:
        self.assertEqual(self.viewer["prompts"], self.source["prompts"])
        self.assertEqual(self.viewer["models"], self.source["models"])
        self.assertEqual(self.viewer["judges"], self.source["judges"])
        self.assertEqual(self.viewer["run"]["backend"], "vllm")
        self.assertEqual(len(self.viewer["runs"]), 2)

        expected_statistics = deepcopy(self.source["statistics"])
        expected_statistics["degeneracy"]["topic_conditional_test"]["cost"] = {
            "incremental_usd_for_topic_analysis": 0,
            "source_job_usd_gpu_compute": 9.53,
            "requested_arms_generation_usd_gpu_compute": 3.57,
            "shared_startup_usd_gpu_compute": 4.1,
            "note": (
                "Historical GPU-compute estimates. Provider-specific infrastructure "
                "fields, hardware allocation details, and unreleased co-served arm "
                "names were removed."
            ),
        }
        self.assertEqual(self.viewer["statistics"], expected_statistics)

        allowed_run_keys = {
            "batch",
            "experiment",
            "backend",
            "finished_at_utc",
            "decoding",
            "total_usd_gpu_compute",
            "data_quality_notes",
        }
        self.assertTrue(
            all(set(run).issubset(allowed_run_keys) for run in self.viewer["runs"])
        )

    def test_private_infrastructure_markers_are_absent(self) -> None:
        forbidden = (
            "/__modal/",
            "/cache/huggingface/",
            "johnnyctgt/DistillationAdapters",
            "kimi_distilled",
            "v4_pro_distilled",
        )
        for path in RELEASE_ROOT.rglob("*"):
            if not path.is_file() or path.name == "MANIFEST.json":
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    self.fail(f"Found private marker {marker!r} in {path}")

    def test_manifest_hashes_and_counts(self) -> None:
        manifest = json.loads(
            (RELEASE_ROOT / "MANIFEST.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["row_counts"],
            {
                "benchmark": 304,
                "responses": 1_976,
                "judgments": 7_296,
                "human_annotations": 96,
                "models": 7,
                "judges": 4,
            },
        )
        for entry in manifest["files"]:
            path = RELEASE_ROOT / entry["path"]
            content = path.read_bytes()
            self.assertEqual(len(content), entry["bytes"])
            self.assertEqual(
                hashlib.sha256(content).hexdigest(), entry["sha256"]
            )

        self.assertEqual(
            self.run_metadata["source_artifact"]["sha256"],
            hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            self.run_metadata["supplemental_response_artifact"]["sha256"],
            hashlib.sha256(OX_ALPHA_RESPONSES_PATH.read_bytes()).hexdigest(),
        )

    def test_ox_alpha_rows_match_the_published_response_schema(self) -> None:
        source_rows = read_jsonl(OX_ALPHA_RESPONSES_PATH)
        released_rows = [
            row for row in self.responses if row["model_key"] == "ox_alpha"
        ]
        other_rows = [
            row for row in self.responses if row["model_key"] != "ox_alpha"
        ]

        self.assertEqual(released_rows, source_rows)
        self.assertEqual(len(released_rows), 152)
        self.assertEqual(
            {tuple(row.keys()) for row in released_rows},
            {tuple(other_rows[0].keys())},
        )
        self.assertEqual(
            Counter(row["response_quality_label"] for row in released_rows),
            {"VALID": 151, "INVALID_DEGENERATE": 1},
        )

        models = read_jsonl(RELEASE_ROOT / "metadata" / "models.jsonl")
        ox_alpha = next(row for row in models if row["model_key"] == "ox_alpha")
        self.assertEqual(ox_alpha["display_name"], "GLM 5.3 (Ox Alpha)")
        self.assertEqual(ox_alpha["served_model"], "stealth/ox-alpha")
        self.assertEqual(ox_alpha["provenance_status"], "undisclosed")
        self.assertIsNone(ox_alpha["identity_claim"])
        self.assertFalse(ox_alpha["lineage_claim"])

    def test_license_and_provenance_are_packaged(self) -> None:
        card = (RELEASE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("license: other", card)
        self.assertIn("CC BY 4.0", card)
        self.assertEqual(
            (RELEASE_ROOT / "LICENSE").read_bytes(),
            (REPOSITORY_ROOT / "data" / "LICENSE").read_bytes(),
        )
        self.assertEqual(
            (RELEASE_ROOT / "CODE_LICENSE").read_bytes(),
            (REPOSITORY_ROOT / "LICENSE").read_bytes(),
        )
        self.assertEqual(
            (RELEASE_ROOT / "NOTICE").read_bytes(),
            (REPOSITORY_ROOT / "NOTICE").read_bytes(),
        )
        self.assertEqual(
            (
                RELEASE_ROOT
                / "THIRD_PARTY_NOTICES"
                / "DECCP_LICENSE"
            ).read_bytes(),
            (
                REPOSITORY_ROOT
                / "THIRD_PARTY_NOTICES"
                / "DECCP_LICENSE"
            ).read_bytes(),
        )
        self.assertIn("augmxnt/deccp", card)
        self.assertIn("substantially changed", card)

    def test_exporter_reproduces_release_from_a_dirty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "blog-v1"
            destination.mkdir()
            (destination / "stale-private-file.txt").write_text(
                "must not survive export",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    "node",
                    str(EXPORTER_PATH),
                    str(SOURCE_PATH),
                    str(ANNOTATIONS_PATH),
                    str(destination),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            expected_files = {
                path.relative_to(RELEASE_ROOT)
                for path in RELEASE_ROOT.rglob("*")
                if path.is_file()
            }
            actual_files = {
                path.relative_to(destination)
                for path in destination.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual_files, expected_files)
            self.assertNotIn(Path("stale-private-file.txt"), actual_files)

            for relative_path in expected_files:
                self.assertEqual(
                    (destination / relative_path).read_bytes(),
                    (RELEASE_ROOT / relative_path).read_bytes(),
                    relative_path,
                )


if __name__ == "__main__":
    unittest.main()
