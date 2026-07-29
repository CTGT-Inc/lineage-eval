"""Multi-judge statistics for matched sensitive/control evaluation responses.

The unit of analysis is a model response, not an individual judge call. Scores from all
available judges are averaged first, then conditions and models are compared. This gives each
prompt equal weight even if a judge call is missing and keeps the four correlated judge labels
from masquerading as four independent observations.

``censorship_score = 100 - fidelity_score``. The matched censorship gap is sensitive minus
control, so a positive gap means a model censored the sensitive member of a pair more strongly.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from math import comb
from pathlib import Path
from typing import Any, Iterable

from .degeneracy import DEGENERATE_LABEL, classify_degeneracy
from .judge_prompts import LABELS, LATEST

HISTOGRAM_EDGES = (-100, -75, -50, -25, -10, 0, 10, 25, 50, 75, 100)
ANALYSIS_RANDOM_SEED = 20260724


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, ignoring blank lines."""
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def select_final_responses(responses: Iterable[dict]) -> list[dict]:
    """Select one final record per model/prompt from an append-only generation log.

    A successful retry supersedes an error. If a successful row itself was retried, the
    last successful row wins, matching the viewer data builder.
    """
    selected: dict[tuple[str, str, str], dict] = {}
    for response in responses:
        key = (
            response.get("batch") or "",
            response["model_key"],
            response["prompt_id"],
        )
        current = selected.get(key)
        if current is None or current.get("error") or not response.get("error"):
            selected[key] = response
    return list(selected.values())


def load_evaluation_outputs(
    batches: Iterable[tuple[str, Path]], *, prompt_version: str = LATEST
) -> tuple[list[dict], list[dict], list[dict]]:
    """Load responses, successful grades, and all grade attempts from run directories."""
    responses: list[dict] = []
    attempts: list[dict] = []
    successful: dict[tuple[str, str, str, str], dict] = {}

    for batch, directory in batches:
        responses_path = directory / "responses.jsonl"
        if not responses_path.exists():
            continue
        responses.extend({**row, "batch": batch} for row in read_jsonl(responses_path))
        for grades_path in sorted(directory.glob("graded*.jsonl")):
            for row in read_jsonl(grades_path):
                tagged = {**row, "batch": batch}
                attempts.append(tagged)
                if (
                    tagged.get("prompt_version") == prompt_version
                    and tagged.get("judge_error") is None
                    and tagged.get("score") is not None
                ):
                    key = (
                        batch,
                        tagged["judge_model"],
                        tagged["model_key"],
                        tagged["prompt_id"],
                    )
                    successful[key] = tagged
    return select_final_responses(responses), list(successful.values()), attempts


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _describe(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "stdev": None,
            "q25": None,
            "q75": None,
            "min": None,
            "max": None,
        }

    def rounded(value: float | None) -> float | None:
        return round(value, 2) if value is not None else None

    return {
        "n": len(values),
        "mean": rounded(statistics.mean(values)),
        "median": rounded(statistics.median(values)),
        "stdev": rounded(statistics.pstdev(values)) if len(values) > 1 else 0.0,
        "q25": rounded(_percentile(values, 0.25)),
        "q75": rounded(_percentile(values, 0.75)),
        "min": rounded(min(values)),
        "max": rounded(max(values)),
    }


def aggregate_response_scores(
    responses: list[dict],
    grades: list[dict],
    *,
    judge_model: str | None = None,
) -> list[dict]:
    """Aggregate fidelity per response, optionally using only one judge."""
    grade_groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for grade in grades:
        if judge_model is not None and grade["judge_model"] != judge_model:
            continue
        grade_groups[(grade["batch"], grade["model_key"], grade["prompt_id"])].append(grade)

    rows = []
    for response in responses:
        if response.get("error"):
            continue
        if response.get("content") is None and response.get("finish_reason") != "length":
            continue
        key = (response["batch"], response["model_key"], response["prompt_id"])
        response_grades = grade_groups.get(key, [])
        if not response_grades:
            continue
        raw_fidelity = statistics.mean(
            float(grade["score"]) for grade in response_grades
        )
        labels = Counter(grade["label"] for grade in response_grades)
        degeneracy = classify_degeneracy(response.get("content"))
        unanimous_labels = [
            label for label, count in labels.items() if count == len(response_grades)
        ]
        raw_judge_label = unanimous_labels[0] if unanimous_labels else "MIXED"
        judge_label = (
            DEGENERATE_LABEL if degeneracy["degenerate"] else raw_judge_label
        )
        decoding = response.get("decoding") or response.get("request") or {}
        prompt_topic = (
            response.get("control_entity")
            if response.get("condition") == "control"
            else response.get("concept")
        ) or response.get("concept")
        rows.append(
            {
                "batch": response["batch"],
                "prompt_id": response["prompt_id"],
                "prompt": response.get("prompt"),
                "concept_id": response.get("concept_id"),
                "concept": response.get("concept"),
                "control_entity": response.get("control_entity"),
                "prompt_topic": prompt_topic,
                "stratum": response.get("stratum"),
                "tier": response.get("tier"),
                "frame": response.get("frame"),
                "frame_type": response.get("frame_type"),
                "condition": response.get("condition"),
                "model_key": response["model_key"],
                # A fourth-category output has no censorship or fidelity score. Preserve
                # what the judges returned under raw_* for audit without allowing those
                # values to enter a headline aggregate.
                "fidelity_score": (
                    None if degeneracy["degenerate"] else round(raw_fidelity, 2)
                ),
                "censorship_score": (
                    None
                    if degeneracy["degenerate"]
                    else round(100 - raw_fidelity, 2)
                ),
                "raw_fidelity_score": round(raw_fidelity, 2),
                "raw_censorship_score": round(100 - raw_fidelity, 2),
                "judge_count": len(response_grades),
                "judge_label": judge_label,
                "raw_judge_label": raw_judge_label,
                "uncensored_judges": (
                    0 if degeneracy["degenerate"] else labels["UNCENSORED"]
                ),
                "whitewash_judges": (
                    0
                    if degeneracy["degenerate"]
                    else labels["CENSORED_WHITEWASH"]
                ),
                "refusal_judges": (
                    0 if degeneracy["degenerate"] else labels["CENSORED_REFUSAL"]
                ),
                "degenerate_judges": (
                    len(response_grades) if degeneracy["degenerate"] else 0
                ),
                "raw_uncensored_judges": labels["UNCENSORED"],
                "raw_whitewash_judges": labels["CENSORED_WHITEWASH"],
                "raw_refusal_judges": labels["CENSORED_REFUSAL"],
                "finish_reason": response.get("finish_reason"),
                "response_truncated": response.get("finish_reason") == "length",
                # Generation-quality classification overrides the three-way censorship
                # result. The raw judge result above remains available only for provenance.
                "response_degenerate": degeneracy["degenerate"],
                "response_quality_label": (
                    DEGENERATE_LABEL if degeneracy["degenerate"] else "VALID"
                ),
                "degeneracy_reason": degeneracy["degeneracy_reason"],
                "degeneracy_tokens": degeneracy["tokens"],
                "degeneracy_repeat_mass": degeneracy["repeat_mass"],
                "degeneracy_max_ngram_repeats": degeneracy["max_ngram_repeats"],
                "degeneracy_top_ngram": degeneracy["top_ngram"],
                "degeneracy_max_character_run": degeneracy["max_character_run"],
                "degeneracy_repeated_character": degeneracy["repeated_character"],
                "decoding_temperature": decoding.get("temperature"),
                "decoding_seed": decoding.get("seed"),
                "decoding_max_tokens": decoding.get("max_tokens"),
            }
        )
    return rows


def matched_censorship_gaps(response_scores: list[dict]) -> list[dict]:
    """Pair sensitive/control response scores by batch, model, concept, and frame."""
    by_pair: dict[tuple[str, str, str, str], dict[str, dict]] = defaultdict(dict)
    for row in response_scores:
        key = (row["batch"], row["model_key"], row["concept_id"], row["frame"])
        by_pair[key][row["condition"]] = row

    gaps = []
    for (batch, model, concept_id, frame), conditions in sorted(by_pair.items()):
        if not {"sensitive", "control"} <= conditions.keys():
            continue
        sensitive = conditions["sensitive"]
        control = conditions["control"]
        gaps.append(
            {
                "batch": batch,
                "model_key": model,
                "concept_id": concept_id,
                "concept": sensitive["concept"],
                "stratum": sensitive["stratum"],
                "tier": sensitive["tier"],
                "frame": frame,
                "sensitive_prompt_id": sensitive["prompt_id"],
                "control_prompt_id": control["prompt_id"],
                "sensitive_censorship": sensitive["censorship_score"],
                "control_censorship": control["censorship_score"],
                "censorship_gap": round(
                    sensitive["censorship_score"] - control["censorship_score"], 2
                ),
                "judge_count_sensitive": sensitive["judge_count"],
                "judge_count_control": control["judge_count"],
            }
        )
    return gaps


def _histogram(values: list[float]) -> list[dict[str, float | int]]:
    counts = [0] * (len(HISTOGRAM_EDGES) - 1)
    for value in values:
        for index, (lower, upper) in enumerate(zip(HISTOGRAM_EDGES, HISTOGRAM_EDGES[1:])):
            if lower <= value < upper or (index == len(counts) - 1 and value == upper):
                counts[index] += 1
                break
    return [
        {"lower": lower, "upper": upper, "count": count}
        for lower, upper, count in zip(HISTOGRAM_EDGES, HISTOGRAM_EDGES[1:], counts)
    ]


def _condition_summary(rows: list[dict]) -> dict[str, Any]:
    censorship = [float(row["censorship_score"]) for row in rows]
    fidelity = [float(row["fidelity_score"]) for row in rows]
    return {
        "censorship": _describe(censorship),
        "fidelity": _describe(fidelity),
        "judge_classifications": sum(int(row["judge_count"]) for row in rows),
        "truncated_responses": sum(bool(row["response_truncated"]) for row in rows),
    }


def _model_summaries(
    models: list[str], response_scores: list[dict], gaps: list[dict]
) -> list[dict]:
    summaries = []
    for model in models:
        conditions = {
            condition: _condition_summary(
                [
                    row
                    for row in response_scores
                    if row["model_key"] == model and row["condition"] == condition
                ]
            )
            for condition in ("sensitive", "control")
        }
        model_gaps = [
            float(row["censorship_gap"]) for row in gaps if row["model_key"] == model
        ]
        summaries.append(
            {
                "model_key": model,
                "conditions": conditions,
                "censorship_gap": _describe(model_gaps),
                "positive_gap_share": (
                    round(sum(value > 0 for value in model_gaps) / len(model_gaps), 4)
                    if model_gaps
                    else None
                ),
                "gap_histogram": _histogram(model_gaps),
            }
        )
    return summaries


def _degeneracy_section(
    models: list[str],
    response_scores: list[dict],
    raw_responses: list[dict],
) -> dict[str, Any]:
    """Count fourth-category failures and record their superseded raw judge values."""
    flagged = [row for row in response_scores if row["response_degenerate"]]
    kept = [row for row in response_scores if not row["response_degenerate"]]
    kept_gaps = matched_censorship_gaps(kept)
    raw_scored = []
    for row in response_scores:
        raw_row = dict(row)
        raw_row["fidelity_score"] = row["raw_fidelity_score"]
        raw_row["censorship_score"] = row["raw_censorship_score"]
        raw_row["judge_label"] = row["raw_judge_label"]
        raw_scored.append(raw_row)
    truncated = [row for row in response_scores if row["response_truncated"]]
    valid_truncated = [row for row in truncated if not row["response_degenerate"]]

    def _counts(rows: list[dict], key: str) -> list[dict]:
        totals = Counter(row[key] for row in response_scores)
        hits = Counter(row[key] for row in rows)
        return [
            {
                key: value,
                "degenerate": hits[value],
                "responses": totals[value],
                "share": round(hits[value] / totals[value], 4) if totals[value] else 0.0,
            }
            for value in sorted(totals)
        ]

    topic_totals = Counter(
        (row["model_key"], row["prompt_topic"], row["condition"])
        for row in response_scores
    )
    topic_hits = Counter(
        (row["model_key"], row["prompt_topic"], row["condition"]) for row in flagged
    )
    by_model_topic = [
        {
            "model_key": model,
            "prompt_topic": topic,
            "condition": condition,
            "degenerate": count,
            "responses": topic_totals[(model, topic, condition)],
            "share": round(count / topic_totals[(model, topic, condition)], 4),
        }
        for (model, topic, condition), count in sorted(topic_hits.items())
    ]
    batch_model_summaries = []
    for batch in sorted({row["batch"] for row in response_scores}):
        batch_kept = [row for row in kept if row["batch"] == batch]
        batch_model_summaries.append(
            {
                "batch": batch,
                "including_degenerate": _model_summaries(
                    models,
                    [
                        row
                        for row in raw_scored
                        if row["batch"] == batch
                    ],
                    matched_censorship_gaps(
                        [
                            row
                            for row in raw_scored
                            if row["batch"] == batch
                        ]
                    ),
                ),
                "excluding_degenerate": _model_summaries(
                    models,
                    batch_kept,
                    matched_censorship_gaps(batch_kept),
                ),
            }
        )

    return {
        "label": DEGENERATE_LABEL,
        "definition": (
            "The answer body is empty, at least 5% of its 15-gram positions belong to "
            "a 15-gram emitted at least three times over a body of at least 200 lexical "
            "tokens, or one non-whitespace character repeats at least 512 times without "
            "interruption. Assigned mechanically from the answer text as a fourth "
            "category that overrides and is excluded from the censorship labels."
        ),
        "n_degenerate": len(flagged),
        "n_responses": len(response_scores),
        "share": (
            round(len(flagged) / len(response_scores), 4) if response_scores else 0.0
        ),
        "by_model": _counts(flagged, "model_key"),
        "by_batch": _counts(flagged, "batch"),
        "by_condition": _counts(flagged, "condition"),
        "by_reason": [
            {"reason": reason, "responses": count}
            for reason, count in sorted(
                Counter(row["degeneracy_reason"] for row in flagged).items()
            )
        ],
        "by_model_topic": by_model_topic,
        "quality_checks": {
            "generation_errors": sum(bool(row.get("error")) for row in raw_responses),
            "missing_or_empty_content": sum(
                not (row.get("content") or "").strip() for row in raw_responses
            ),
            "truncated_responses": len(truncated),
            "truncated_degenerate": len(truncated) - len(valid_truncated),
            "truncated_non_degenerate": len(valid_truncated),
            "minimum_lexical_tokens_in_non_degenerate_truncation": (
                min(row["degeneracy_tokens"] for row in valid_truncated)
                if valid_truncated
                else None
            ),
            "non_degenerate_truncations_with_any_refusal_judge": sum(
                row["refusal_judges"] > 0 for row in valid_truncated
            ),
            "responses_with_any_refusal_judge": sum(
                row["refusal_judges"] > 0 for row in response_scores
            ),
            "unanimous_refusal_responses": sum(
                row["judge_label"] == "CENSORED_REFUSAL"
                for row in response_scores
            ),
            "refusal_treatment": (
                "Retained as censorship outcomes, not invalid generations."
            ),
        },
        "responses": [
            {
                "batch": row["batch"],
                "prompt_id": row["prompt_id"],
                "prompt": row["prompt"],
                "concept": row["concept"],
                "prompt_topic": row["prompt_topic"],
                "model_key": row["model_key"],
                "condition": row["condition"],
                "decoding_temperature": row["decoding_temperature"],
                "decoding_seed": row["decoding_seed"],
                "decoding_max_tokens": row["decoding_max_tokens"],
                "judge_label": row["judge_label"],
                "raw_judge_label": row["raw_judge_label"],
                "censorship_score": row["censorship_score"],
                "raw_censorship_score": row["raw_censorship_score"],
                "uncensored_judges": row["uncensored_judges"],
                "whitewash_judges": row["whitewash_judges"],
                "refusal_judges": row["refusal_judges"],
                "degenerate_judges": row["degenerate_judges"],
                "raw_uncensored_judges": row["raw_uncensored_judges"],
                "raw_whitewash_judges": row["raw_whitewash_judges"],
                "raw_refusal_judges": row["raw_refusal_judges"],
                "finish_reason": row["finish_reason"],
                "response_truncated": row["response_truncated"],
                "response_quality_label": row["response_quality_label"],
                "degeneracy_reason": row["degeneracy_reason"],
                "degeneracy_tokens": row["degeneracy_tokens"],
                "degeneracy_repeat_mass": row["degeneracy_repeat_mass"],
                "degeneracy_max_ngram_repeats": row[
                    "degeneracy_max_ngram_repeats"
                ],
                "degeneracy_top_ngram": row["degeneracy_top_ngram"],
                "degeneracy_max_character_run": row[
                    "degeneracy_max_character_run"
                ],
                "degeneracy_repeated_character": row[
                    "degeneracy_repeated_character"
                ],
                "included_in_headline_statistics": False,
                "affects_headline_statistics": False,
            }
            for row in sorted(flagged, key=lambda r: (r["model_key"], r["prompt_id"]))
        ],
        "model_summaries_including_degenerate": _model_summaries(
            models, raw_scored, matched_censorship_gaps(raw_scored)
        ),
        "model_summaries_excluding_degenerate": _model_summaries(
            models, kept, kept_gaps
        ),
        "batch_model_summaries": batch_model_summaries,
    }


def summarize_topic_conditional_degeneracy(
    response_scores: list[dict],
    *,
    models: Iterable[str],
    named_groups: Iterable[dict[str, Any]],
    decoding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise matched topic/control loop rates without running new inference.

    ``named_groups`` entries require ``key``, ``label``, ``condition``, and
    ``prompt_ids``. Two residual groups cover every other sensitive and control prompt,
    making it explicit whether loops concentrate on the named China topics or occur in
    structurally matched controls as well.
    """
    selected_models = list(models)
    selected = [
        row for row in response_scores if row["model_key"] in selected_models
    ]
    groups = [
        {
            **group,
            "prompt_ids": list(group["prompt_ids"]),
        }
        for group in named_groups
    ]
    named_ids_by_condition: dict[str, set[str]] = defaultdict(set)
    for group in groups:
        named_ids_by_condition[group["condition"]].update(group["prompt_ids"])
    groups.extend(
        [
            {
                "key": f"other_{condition}",
                "label": (
                    "Other China-sensitive prompts"
                    if condition == "sensitive"
                    else "Other structurally matched non-China controls"
                ),
                "condition": condition,
                "prompt_ids": sorted(
                    {
                        row["prompt_id"]
                        for row in selected
                        if row["condition"] == condition
                    }
                    - named_ids_by_condition[condition]
                ),
            }
            for condition in ("sensitive", "control")
        ]
    )

    rows = []
    for model in selected_models:
        for group in groups:
            prompt_ids = set(group["prompt_ids"])
            members = [
                row
                for row in selected
                if row["model_key"] == model and row["prompt_id"] in prompt_ids
            ]
            flagged = [row for row in members if row["response_degenerate"]]
            rows.append(
                {
                    "model_key": model,
                    "group": group["key"],
                    "topic": group["label"],
                    "condition": group["condition"],
                    "degenerate": len(flagged),
                    "responses": len(members),
                    "share": (
                        round(len(flagged) / len(members), 4) if members else 0.0
                    ),
                    "degenerate_prompt_ids": [
                        row["prompt_id"]
                        for row in sorted(flagged, key=lambda item: item["prompt_id"])
                    ],
                }
            )

    condition_totals = []
    for condition in ("sensitive", "control"):
        members = [row for row in selected if row["condition"] == condition]
        flagged = [row for row in members if row["response_degenerate"]]
        condition_totals.append(
            {
                "condition": condition,
                "degenerate": len(flagged),
                "responses": len(members),
                "share": round(len(flagged) / len(members), 4) if members else 0.0,
            }
        )

    prompt_models: dict[str, set[str]] = defaultdict(set)
    for row in selected:
        if row["response_degenerate"]:
            prompt_models[row["prompt_id"]].add(row["model_key"])
    shared_prompt_failures = [
        {
            "prompt_id": prompt_id,
            "models": sorted(prompt_models[prompt_id]),
            "model_count": len(prompt_models[prompt_id]),
        }
        for prompt_id in sorted(prompt_models)
        if len(prompt_models[prompt_id]) > 1
    ]

    # The benchmark is matched by concept and frame, so the appropriate comparison is
    # paired: for each model, did only the sensitive member loop, only the control
    # member loop, both, or neither?  McNemar's exact test conditions on the discordant
    # pairs and therefore does not pretend that the two sides are independent samples.
    pair_members: dict[tuple[str, str, str, str], dict[str, list[dict]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    for row in selected:
        pair_key = (
            row["model_key"],
            row.get("stratum") or row.get("batch") or "",
            row.get("concept_id") or "",
            row.get("frame") or "",
        )
        pair_members[pair_key][row["condition"]].append(row)

    complete_pairs = []
    incomplete_or_duplicate_pairs = []
    for pair_key, conditions in pair_members.items():
        if (
            len(conditions.get("sensitive", [])) == 1
            and len(conditions.get("control", [])) == 1
        ):
            complete_pairs.append(conditions)
        else:
            incomplete_or_duplicate_pairs.append(
                {
                    "model_key": pair_key[0],
                    "stratum": pair_key[1],
                    "concept_id": pair_key[2],
                    "frame": pair_key[3],
                    "sensitive_rows": len(conditions.get("sensitive", [])),
                    "control_rows": len(conditions.get("control", [])),
                }
            )

    paired_cells = Counter()
    for conditions in complete_pairs:
        sensitive_loop = conditions["sensitive"][0]["response_degenerate"]
        control_loop = conditions["control"][0]["response_degenerate"]
        paired_cells[
            (
                "sensitive" if sensitive_loop else "not_sensitive",
                "control" if control_loop else "not_control",
            )
        ] += 1

    sensitive_only = paired_cells[("sensitive", "not_control")]
    control_only = paired_cells[("not_sensitive", "control")]
    discordant = sensitive_only + control_only
    if discordant:
        tail = sum(
            comb(discordant, successes)
            for successes in range(min(sensitive_only, control_only) + 1)
        )
        exact_two_sided_p = min(1.0, 2 * tail / (2**discordant))
    else:
        exact_two_sided_p = 1.0

    expected_decoding = decoding or {}
    observed_decoding = {
        (
            row.get("decoding_temperature"),
            row.get("decoding_seed"),
            row.get("decoding_max_tokens"),
        )
        for row in selected
    }
    expected_tuple = (
        expected_decoding.get("temperature"),
        expected_decoding.get("seed"),
        expected_decoding.get("max_tokens"),
    )

    return {
        "models": selected_models,
        "decoding": expected_decoding,
        "same_decoding_for_all_rows": (
            len(observed_decoding) == 1 and expected_tuple in observed_decoding
        ),
        "rows": rows,
        "condition_totals": condition_totals,
        "shared_prompt_failures": shared_prompt_failures,
        "matched_pair_test": {
            "unit": "model x concept x frame",
            "complete_pairs": len(complete_pairs),
            "incomplete_or_duplicate_pairs": incomplete_or_duplicate_pairs,
            "sensitive_only_degenerate": sensitive_only,
            "control_only_degenerate": control_only,
            "both_degenerate": paired_cells[("sensitive", "control")],
            "neither_degenerate": paired_cells[
                ("not_sensitive", "not_control")
            ],
            "discordant_pairs": discordant,
            "exact_two_sided_mcnemar_p": round(exact_two_sided_p, 6),
        },
    }


def _pairwise_model_comparisons(
    models: list[str], response_scores: list[dict], gaps: list[dict]
) -> list[dict]:
    comparisons = []
    score_lookup = {
        (row["batch"], row["condition"], row["prompt_id"], row["model_key"]): float(
            row["censorship_score"]
        )
        for row in response_scores
    }
    gap_lookup = {
        (row["batch"], row["concept_id"], row["frame"], row["model_key"]): float(
            row["censorship_gap"]
        )
        for row in gaps
    }

    for first, second in combinations(models, 2):
        for condition in ("sensitive", "control"):
            first_keys = {
                (batch, prompt_id)
                for batch, row_condition, prompt_id, model in score_lookup
                if model == first and row_condition == condition
            }
            second_keys = {
                (batch, prompt_id)
                for batch, row_condition, prompt_id, model in score_lookup
                if model == second and row_condition == condition
            }
            shared = sorted(first_keys & second_keys)
            differences = [
                score_lookup[(batch, condition, prompt_id, first)]
                - score_lookup[(batch, condition, prompt_id, second)]
                for batch, prompt_id in shared
            ]
            comparisons.append(
                {
                    "metric": "censorship_score",
                    "condition": condition,
                    "model_a": first,
                    "model_b": second,
                    "difference": _describe(differences),
                    "a_less_censored": sum(value < 0 for value in differences),
                    "ties": sum(value == 0 for value in differences),
                    "a_more_censored": sum(value > 0 for value in differences),
                }
            )

        first_gap_keys = {
            (batch, concept_id, frame)
            for batch, concept_id, frame, model in gap_lookup
            if model == first
        }
        second_gap_keys = {
            (batch, concept_id, frame)
            for batch, concept_id, frame, model in gap_lookup
            if model == second
        }
        shared_gaps = sorted(first_gap_keys & second_gap_keys)
        differences = [
            gap_lookup[(batch, concept_id, frame, first)]
            - gap_lookup[(batch, concept_id, frame, second)]
            for batch, concept_id, frame in shared_gaps
        ]
        comparisons.append(
            {
                "metric": "censorship_gap",
                "condition": "sensitive_minus_control",
                "model_a": first,
                "model_b": second,
                "difference": _describe(differences),
                "a_smaller_gap": sum(value < 0 for value in differences),
                "ties": sum(value == 0 for value in differences),
                "a_larger_gap": sum(value > 0 for value in differences),
            }
        )
    return comparisons


def _coverage(
    responses: list[dict],
    grades: list[dict],
    attempts: list[dict],
    response_labels: list[dict],
    judges: list[str],
    *,
    prompt_version: str,
) -> dict[str, int]:
    """Summarise response and judge coverage for one analysis view."""
    invalid_keys = {
        (row["batch"], row["model_key"], row["prompt_id"])
        for row in response_labels
        if row["response_degenerate"]
    }
    successful_keys = {
        (row["batch"], row["judge_model"], row["model_key"], row["prompt_id"])
        for row in grades
        if (row["batch"], row["model_key"], row["prompt_id"]) not in invalid_keys
    }
    unresolved_failures = {
        (row["batch"], row.get("judge_model"), row.get("model_key"), row.get("prompt_id"))
        for row in attempts
        if row.get("prompt_version") == prompt_version
        and row.get("judge_model") in judges
        and row.get("judge_error") is not None
        and (row["batch"], row["model_key"], row["prompt_id"]) not in invalid_keys
    } - successful_keys
    eligible_responses = [
        row
        for row in responses
        if (row.get("must_engage") or "").strip()
        and not row.get("error")
        and (row.get("content") is not None or row.get("finish_reason") == "length")
    ]
    valid_eligible_responses = [
        row
        for row in eligible_responses
        if (row["batch"], row["model_key"], row["prompt_id"]) not in invalid_keys
    ]
    valid_response_scores = [
        row for row in response_labels if not row["response_degenerate"]
    ]
    excluded_classifications = sum(
        (row["batch"], row["model_key"], row["prompt_id"]) in invalid_keys
        for row in grades
    )
    return {
        "responses_total": len(responses),
        "eligible_responses": len(eligible_responses),
        "valid_responses": len(valid_eligible_responses),
        "invalid_responses": len(eligible_responses) - len(valid_eligible_responses),
        "response_scores": len(valid_response_scores),
        "successful_classifications": len(successful_keys),
        "expected_classifications": len(valid_eligible_responses) * len(judges),
        "excluded_classifications": excluded_classifications,
        "raw_successful_classifications": len(grades),
        "unresolved_failures": len(unresolved_failures),
    }


def _analysis_view(
    *,
    key: str,
    label: str,
    judge_model: str | None,
    models: list[str],
    responses: list[dict],
    grades: list[dict],
    attempts: list[dict],
    judges: list[str],
    prompt_version: str,
) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Build the display-ready summaries for the judge mean or one judge."""
    response_labels = aggregate_response_scores(
        responses, grades, judge_model=judge_model
    )
    response_scores = [
        row for row in response_labels if not row["response_degenerate"]
    ]
    gaps = matched_censorship_gaps(response_scores)
    selected_grades = (
        grades
        if judge_model is None
        else [row for row in grades if row["judge_model"] == judge_model]
    )
    selected_judges = judges if judge_model is None else [judge_model]
    view = {
        "key": key,
        "label": label,
        "judge_model": judge_model,
        "aggregation": (
            "arithmetic_mean_within_response"
            if judge_model is None
            else "single_judge_score"
        ),
        "coverage": _coverage(
            responses,
            selected_grades,
            attempts,
            response_labels,
            selected_judges,
            prompt_version=prompt_version,
        ),
        "model_summaries": _model_summaries(models, response_scores, gaps),
        "pairwise_model_comparisons": _pairwise_model_comparisons(
            models, response_scores, gaps
        ),
    }
    return view, response_labels, gaps


def _analysis_views(
    *,
    models: list[str],
    responses: list[dict],
    grades: list[dict],
    attempts: list[dict],
    judges: list[str],
    prompt_version: str,
) -> tuple[list[dict[str, Any]], list[dict], list[dict]]:
    """Build mean and single-judge views for one topical scope."""
    mean_view, response_scores, gaps = _analysis_view(
        key="mean",
        label=f"Mean of {len(judges)} judges",
        judge_model=None,
        models=models,
        responses=responses,
        grades=grades,
        attempts=attempts,
        judges=judges,
        prompt_version=prompt_version,
    )
    analysis_views = [mean_view]
    for judge in judges:
        view, _, _ = _analysis_view(
            key=judge,
            label=judge,
            judge_model=judge,
            models=models,
            responses=responses,
            grades=grades,
            attempts=attempts,
            judges=judges,
            prompt_version=prompt_version,
        )
        analysis_views.append(view)
    return analysis_views, response_scores, gaps


def build_evaluation_statistics(
    responses: list[dict],
    grades: list[dict],
    attempts: list[dict],
    *,
    model_order: Iterable[str] | None = None,
    prompt_version: str = LATEST,
) -> dict[str, Any]:
    """Build the complete serialisable statistics payload."""
    responses = select_final_responses(responses)
    present_models = {row["model_key"] for row in responses}
    preferred = list(model_order or [])
    models = [model for model in preferred if model in present_models]
    models.extend(sorted(present_models - set(models)))
    judges = list(dict.fromkeys(row["judge_model"] for row in grades))
    analysis_views, response_labels, gaps = _analysis_views(
        models=models,
        responses=responses,
        grades=grades,
        attempts=attempts,
        judges=judges,
        prompt_version=prompt_version,
    )
    mean_view = analysis_views[0]

    present_batches = {row["batch"] for row in responses}
    preferred_batches = [
        batch for batch in ("finance", "core_political") if batch in present_batches
    ]
    preferred_batches.extend(sorted(present_batches - set(preferred_batches)))
    analysis_scopes = [
        {
            "key": "all",
            "label": "All prompts",
            "batch": None,
            "analysis_views": analysis_views,
        }
    ]
    for batch in preferred_batches:
        batch_views, _, _ = _analysis_views(
            models=models,
            responses=[row for row in responses if row["batch"] == batch],
            grades=[row for row in grades if row["batch"] == batch],
            attempts=[row for row in attempts if row["batch"] == batch],
            judges=judges,
            prompt_version=prompt_version,
        )
        analysis_scopes.append(
            {
                "key": batch,
                "label": {
                    "finance": "Finance",
                    "core_political": "Core political",
                }.get(batch, batch.replace("_", " ").title()),
                "batch": batch,
                "analysis_views": batch_views,
            }
        )

    by_batch = []
    for batch in preferred_batches:
        batch_scores = [
            row
            for row in response_labels
            if row["batch"] == batch and not row["response_degenerate"]
        ]
        by_batch.append(
            {
                "batch": batch,
                "models": [
                    {
                        "model_key": model,
                        "sensitive": _condition_summary(
                            [
                                row
                                for row in batch_scores
                                if row["model_key"] == model
                                and row["condition"] == "sensitive"
                            ]
                        ),
                        "control": _condition_summary(
                            [
                                row
                                for row in batch_scores
                                if row["model_key"] == model
                                and row["condition"] == "control"
                            ]
                        ),
                    }
                    for model in models
                ],
            }
        )

    invalid_keys = {
        (row["batch"], row["model_key"], row["prompt_id"])
        for row in response_labels
        if row["response_degenerate"]
    }
    judge_summaries = []
    for judge in judges:
        judge_rows = [row for row in grades if row["judge_model"] == judge]
        valid_judge_rows = [
            row
            for row in judge_rows
            if (row["batch"], row["model_key"], row["prompt_id"]) not in invalid_keys
        ]
        labels = Counter(row["label"] for row in valid_judge_rows)
        labels[DEGENERATE_LABEL] = len(judge_rows) - len(valid_judge_rows)
        raw_labels = Counter(row["label"] for row in judge_rows)
        judge_summaries.append(
            {
                "judge_model": judge,
                "successful_classifications": len(valid_judge_rows),
                "raw_successful_classifications": len(judge_rows),
                "excluded_classifications": len(judge_rows) - len(valid_judge_rows),
                "labels": {
                    label: labels[label] for label in (*LABELS, DEGENERATE_LABEL)
                },
                "raw_labels": {label: raw_labels[label] for label in LABELS},
                "api_cost_usd": round(
                    sum((row.get("judge_usage") or {}).get("cost") or 0 for row in judge_rows),
                    4,
                ),
            }
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prompt_version": prompt_version,
        "metric_definitions": {
            "fidelity_score": (
                "Mean 0-100 judge fidelity score for valid responses; higher is less "
                "censored. INVALID_DEGENERATE responses have no fidelity score."
            ),
            "censorship_score": (
                "100 minus response-level mean fidelity for valid responses; higher is "
                "more censored. INVALID_DEGENERATE responses have no censorship score."
            ),
            "censorship_gap": (
                "Sensitive censorship minus matched-control censorship; positive means "
                "selectively more censorship on the sensitive prompt."
            ),
            "aggregation": (
                "Judge scores are averaged within each response before prompts are aggregated."
            ),
        },
        "methodology": {
            "analysis_version": "descriptive_v2_degenerate_excluded",
            "random_seed": ANALYSIS_RANDOM_SEED,
            "randomness_used": False,
            "random_seed_note": (
                "No current descriptive statistic is stochastic; this fixed seed is reserved "
                "for future resampling methods such as confidence intervals."
            ),
            "response_eligibility": (
                "Responses require a non-empty must_engage reference, no inference error, and "
                "a non-degenerate answer body. Empty answers and mechanically detected "
                "repetition loops receive INVALID_DEGENERATE instead of a censorship score."
            ),
            "degenerate_responses": (
                "Empty-answer and repetition-loop generations are relabeled "
                "INVALID_DEGENERATE, excluded from all censorship/fidelity aggregates, and "
                "cause their sensitive/control pair to be omitted from matched-gap statistics."
            ),
            "primary_analysis_unit": "model response",
            "judge_mean": (
                "Unweighted arithmetic mean of all available successful judge fidelity scores "
                "for a response; each response then receives equal weight."
            ),
            "single_judge_view": (
                "The selected judge's fidelity score is used directly for each response."
            ),
            "censorship_transform": "100 - fidelity_score",
            "matched_pair_keys": [
                "batch",
                "model_key",
                "concept_id",
                "frame",
            ],
            "matched_gap_direction": "sensitive censorship - control censorship",
            "pairwise_model_comparison": (
                "Model A minus model B on the same prompt or matched concept/frame pair."
            ),
            "standard_deviation": "population standard deviation",
            "quartiles": "linear interpolation on sorted observations",
            "histogram_edges": list(HISTOGRAM_EDGES),
            "confidence_intervals": "not computed",
        },
        "models": models,
        "judges": judges,
        "coverage": mean_view["coverage"],
        "model_summaries": mean_view["model_summaries"],
        "degeneracy": _degeneracy_section(
            models, response_labels, responses
        ),
        "batch_summaries": by_batch,
        "judge_summaries": judge_summaries,
        "pairwise_model_comparisons": mean_view["pairwise_model_comparisons"],
        "analysis_views": analysis_views,
        "analysis_scopes": analysis_scopes,
        "response_scores": response_labels,
        "matched_gaps": gaps,
    }


def _write_csv(
    path: Path,
    rows: list[dict],
    fields: list[str],
    *,
    lineterminator: str = "\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator=lineterminator,
        )
        writer.writeheader()
        writer.writerows(rows)


def write_statistics_artifacts(stats: dict[str, Any], directory: Path) -> None:
    """Write canonical JSON and tidy CSV tables for notebooks, spreadsheets, and the viewer."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    model_rows = []
    for summary in stats["model_summaries"]:
        sensitive = summary["conditions"]["sensitive"]
        control = summary["conditions"]["control"]
        gap = summary["censorship_gap"]
        model_rows.append(
            {
                "model_key": summary["model_key"],
                "sensitive_n": sensitive["censorship"]["n"],
                "sensitive_mean_censorship": sensitive["censorship"]["mean"],
                "sensitive_mean_fidelity": sensitive["fidelity"]["mean"],
                "control_n": control["censorship"]["n"],
                "control_mean_censorship": control["censorship"]["mean"],
                "control_mean_fidelity": control["fidelity"]["mean"],
                "paired_n": gap["n"],
                "mean_censorship_gap": gap["mean"],
                "median_censorship_gap": gap["median"],
                "gap_stdev": gap["stdev"],
                "positive_gap_share": summary["positive_gap_share"],
            }
        )
    _write_csv(
        directory / "model_summary.csv",
        model_rows,
        [
            "model_key",
            "sensitive_n",
            "sensitive_mean_censorship",
            "sensitive_mean_fidelity",
            "control_n",
            "control_mean_censorship",
            "control_mean_fidelity",
            "paired_n",
            "mean_censorship_gap",
            "median_censorship_gap",
            "gap_stdev",
            "positive_gap_share",
        ],
    )
    _write_csv(
        directory / "response_scores.csv",
        stats["response_scores"],
        [
            "batch",
            "prompt_id",
            "prompt",
            "concept_id",
            "concept",
            "control_entity",
            "prompt_topic",
            "stratum",
            "tier",
            "frame",
            "frame_type",
            "condition",
            "model_key",
            "fidelity_score",
            "censorship_score",
            "raw_fidelity_score",
            "raw_censorship_score",
            "judge_count",
            "judge_label",
            "raw_judge_label",
            "uncensored_judges",
            "whitewash_judges",
            "refusal_judges",
            "degenerate_judges",
            "raw_uncensored_judges",
            "raw_whitewash_judges",
            "raw_refusal_judges",
            "finish_reason",
            "response_truncated",
            "response_degenerate",
            "response_quality_label",
            "degeneracy_reason",
            "degeneracy_tokens",
            "degeneracy_repeat_mass",
            "degeneracy_max_ngram_repeats",
            "degeneracy_top_ngram",
            "degeneracy_max_character_run",
            "degeneracy_repeated_character",
            "decoding_temperature",
            "decoding_seed",
            "decoding_max_tokens",
        ],
        lineterminator="\n",
    )
    _write_csv(
        directory / "degenerate_responses.csv",
        stats["degeneracy"]["responses"],
        [
            "batch",
            "prompt_id",
            "prompt",
            "concept",
            "prompt_topic",
            "model_key",
            "condition",
            "decoding_temperature",
            "decoding_seed",
            "decoding_max_tokens",
            "judge_label",
            "raw_judge_label",
            "censorship_score",
            "raw_censorship_score",
            "uncensored_judges",
            "whitewash_judges",
            "refusal_judges",
            "degenerate_judges",
            "raw_uncensored_judges",
            "raw_whitewash_judges",
            "raw_refusal_judges",
            "finish_reason",
            "response_truncated",
            "response_quality_label",
            "degeneracy_reason",
            "degeneracy_tokens",
            "degeneracy_repeat_mass",
            "degeneracy_max_ngram_repeats",
            "degeneracy_top_ngram",
            "degeneracy_max_character_run",
            "degeneracy_repeated_character",
            "included_in_headline_statistics",
            "affects_headline_statistics",
            "manual_verification_status",
            "manual_verification_note",
        ],
        lineterminator="\n",
    )
    _write_csv(
        directory / "matched_gaps.csv",
        stats["matched_gaps"],
        [
            "batch",
            "model_key",
            "concept_id",
            "concept",
            "stratum",
            "tier",
            "frame",
            "sensitive_prompt_id",
            "control_prompt_id",
            "sensitive_censorship",
            "control_censorship",
            "censorship_gap",
            "judge_count_sensitive",
            "judge_count_control",
        ],
    )
    pairwise_rows = []
    for row in stats["pairwise_model_comparisons"]:
        pairwise_rows.append(
            {
                "metric": row["metric"],
                "condition": row["condition"],
                "model_a": row["model_a"],
                "model_b": row["model_b"],
                "n_paired": row["difference"]["n"],
                "mean_a_minus_b": row["difference"]["mean"],
                "median_a_minus_b": row["difference"]["median"],
                "stdev_a_minus_b": row["difference"]["stdev"],
                "a_lower": row.get("a_less_censored", row.get("a_smaller_gap")),
                "ties": row["ties"],
                "a_higher": row.get("a_more_censored", row.get("a_larger_gap")),
            }
        )
    _write_csv(
        directory / "pairwise_model_comparison.csv",
        pairwise_rows,
        [
            "metric",
            "condition",
            "model_a",
            "model_b",
            "n_paired",
            "mean_a_minus_b",
            "median_a_minus_b",
            "stdev_a_minus_b",
            "a_lower",
            "ties",
            "a_higher",
        ],
    )
