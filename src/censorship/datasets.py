"""Benchmark loading.

Prompt sets are JSONL, one record per prompt, keyed by a stable `prompt_id`. Sets are
treated as immutable once a run has used them: rebuild under a new filename rather than
editing in place, so a response file always refers to a recoverable prompt set.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = ROOT / "data" / "benchmark"

REQUIRED_FIELDS = {"benchmark_version", "prompt_id", "prompt", "stratum"}


def load_path(path: str | Path) -> list[dict]:
    """Load and validate one benchmark JSONL path."""

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no benchmark set at {path}")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    for i, r in enumerate(rows):
        missing = REQUIRED_FIELDS - r.keys()
        if missing:
            raise ValueError(f"{path.name} row {i}: missing {sorted(missing)}")

    ids = [r["prompt_id"] for r in rows]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{path.name}: duplicate prompt_id(s) {dupes[:5]}")
    versions = {r["benchmark_version"] for r in rows}
    if len(versions) != 1:
        raise ValueError(f"{path.name}: expected one benchmark_version, found {sorted(versions)}")
    return rows


def load_set(name: str) -> list[dict]:
    """Load one repository benchmark by stem, e.g. ``finance_prompts``."""

    return load_path(BENCHMARK_DIR / f"{name}.jsonl")


def load_paths(*paths: str | Path) -> list[dict]:
    """Load explicit paths and enforce prompt-id uniqueness across them."""

    rows: list[dict] = []
    for path in paths:
        rows.extend(load_path(path))
    ids = [row["prompt_id"] for row in rows]
    if len(ids) != len(set(ids)):
        dupes = sorted({prompt_id for prompt_id in ids if ids.count(prompt_id) > 1})
        raise ValueError(f"prompt_id collision across sets: {dupes[:5]}")
    return rows


def load_sets(*names: str) -> list[dict]:
    """Load and concatenate several sets, enforcing prompt_id uniqueness across all."""

    return load_paths(*(BENCHMARK_DIR / f"{name}.jsonl" for name in names))
