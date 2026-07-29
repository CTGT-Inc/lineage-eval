"""LLM judge: score a response's fidelity to a reference uncensored answer.

The judge is a **scalable proxy, not ground truth.** EXPERIMENT_PLAN.md is explicit that headline
labels come from humans; this judge exists to triage at scale and to be *validated against* those
human labels later. Its output is therefore shaped to line up with the human schema — the same
three classes plus a 0-100 score — so agreement can be measured by a plain join, and so a run
records exactly which judge model and which rubric version produced each score.

Two design choices follow from "not ground truth":
  • the judge model is injected and recorded, never assumed. It must not be one of the models
    under test (it cannot grade itself), and should not be a plausibly-censored model, which
    would import the very censorship we measure. `guard_judge_model` enforces the first.
  • the rubric lives in `judge_prompts.py`, versioned. Editing prompts never touches this file.

Everything here is async and reuses `OpenRouterClient.chat`, so the judge shares the client's
retry, timeout and concurrency policy rather than reimplementing it.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .client import OpenRouterClient
from .judge_prompts import FAILURE_MODES, LABELS, LATEST, get_prompt

# xAI Grok reasoning model via OpenRouter. Neutral w.r.t. the four models under test. Override
# per run with --judge-model or the JUDGE_MODEL env var; nothing downstream hard-codes it.
DEFAULT_JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "x-ai/grok-4.20")

# Model keys under test. The judge must not be any of these: a model cannot neutrally grade its
# own outputs, and the point of the judge is an independent read.
_SUBJECT_SLUGS = {
    "openai/gpt-oss-120b", "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro",
    "moonshotai/kimi-k2.5", "moonshotai/kimi-k2.6",
}


@dataclass(frozen=True)
class JudgeConfig:
    """Everything that determines a score, recorded with every graded record so a result is
    reproducible and a rubric/model change is never silent."""

    model: str = DEFAULT_JUDGE_MODEL
    provider: str | None = None          # pin an OpenRouter provider, or None to let it route
    prompt_version: str = LATEST
    temperature: float = 0.0
    max_tokens: int = 2048
    seed: int | None = 0
    reasoning: bool = True
    reasoning_effort: str | None = None
    parse_retries: int = 1               # re-ask once on unparseable output before giving up


@dataclass(frozen=True)
class JudgeResult:
    """A single grading outcome. `error` is set iff grading failed (API or parse); on failure the
    score/label are None and `raw` holds whatever the judge returned, for debugging."""

    label: str | None
    score: int | None
    rationale: str | None = None
    covers: list[str] = field(default_factory=list)
    omits: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    judge_model: str | None = None
    prompt_version: str | None = None
    latency_s: float | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None
    raw: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "score": self.score,
            "rationale": self.rationale,
            "covers": self.covers,
            "omits": self.omits,
            "failure_modes": self.failure_modes,
            "judge_model": self.judge_model,
            "prompt_version": self.prompt_version,
            "judge_latency_s": self.latency_s,
            "judge_usage": self.usage,
            "judge_error": self.error,
            "judge_raw": self.raw,
        }


def guard_judge_model(slug: str) -> None:
    """Refuse a judge that is one of the models under test. Raises rather than warns: a self-graded
    run is invalid, not merely suspect, and should fail loudly before spending on it."""
    if slug in _SUBJECT_SLUGS:
        raise ValueError(
            f"judge model {slug!r} is one of the models under test; a model cannot grade itself. "
            f"Pick a neutral judge (--judge-model / JUDGE_MODEL)."
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from judge output.

    Reasoning models and non-strict endpoints sometimes wrap the object in prose or a ```json
    fence, so we try the whole string, then a fenced block, then the outermost brace span.
    """
    if not text:
        return None
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        candidates.append(text[i : j + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj

    # A model can emit a complete valid object followed by prose or a stray closing brace. The
    # outermost-brace candidate above deliberately remains strict, but raw_decode lets us recover
    # the first complete object without guessing how to repair its contents.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start() :])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj

    # A token-capped structured output can contain every semantic field but stop
    # just before its final delimiters. Repair only unambiguous container syntax:
    # trim whitespace, drop one dangling comma, and close open arrays/objects.
    # Do not close an unterminated string because that would silently accept a
    # truncated semantic value.
    for match in re.finditer(r"\{", text):
        candidate = text[match.start() :].rstrip()
        stack: list[str] = []
        in_string = False
        escaped = False
        mismatched = False
        for char in candidate:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                expected = "[" if char == "]" else "{"
                if not stack or stack[-1] != expected:
                    mismatched = True
                    break
                stack.pop()
        if mismatched or in_string or not stack:
            continue
        if candidate.endswith(","):
            candidate = candidate[:-1].rstrip()
        candidate += "".join(
            "}" if opener == "{" else "]" for opener in reversed(stack)
        )
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _coerce(obj: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
    """Validate and normalise a parsed judge object into (label, score, extras).

    Tolerant on the extras, strict on the two fields the analysis depends on: an out-of-range or
    non-numeric score, or a label outside the schema, means the grade is unusable and we surface
    it as a parse failure rather than silently coercing a wrong number into the dataset.
    """
    label = obj.get("label")
    if isinstance(label, str):
        label = label.strip().upper()
    if label not in LABELS:
        return None

    raw_score = obj.get("score")
    try:
        score = int(round(float(raw_score)))
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        # A value just outside the range is a rounding/format slip and is safe to clamp; anything
        # wildly out is a misunderstanding and should fail. 0-100 is the whole scale, so clamp.
        score = max(0, min(100, score))

    def _strlist(key: str) -> list[str]:
        v = obj.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    modes = [m for m in _strlist("failure_modes") if m in FAILURE_MODES]
    rationale = obj.get("rationale")
    extras = {
        "rationale": str(rationale) if rationale is not None else None,
        "covers": _strlist("covers"),
        "omits": _strlist("omits"),
        "failure_modes": modes,
    }
    return label, score, extras


def _aggregate_usage(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate billed usage across judge parse retries without hiding the attempt records."""
    attempts = [usage for usage in attempts if usage]
    if not attempts:
        return None
    if len(attempts) == 1:
        return attempts[0]

    summed: dict[str, Any] = {
        "prompt_tokens": sum(int(usage.get("prompt_tokens") or 0) for usage in attempts),
        "completion_tokens": sum(int(usage.get("completion_tokens") or 0) for usage in attempts),
        "total_tokens": sum(int(usage.get("total_tokens") or 0) for usage in attempts),
        "cost": sum(float(usage.get("cost") or 0) for usage in attempts),
        "judge_parse_attempts": len(attempts),
        "attempt_usage": attempts,
    }
    reasoning_tokens = sum(
        int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        for usage in attempts
    )
    if reasoning_tokens:
        summed["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return summed


class Judge:
    """Grades one (reference, response) pair at a time. Batch grading lives in `grade_file`."""

    def __init__(self, client: OpenRouterClient, config: JudgeConfig | None = None):
        self.client = client
        self.config = config or JudgeConfig()
        guard_judge_model(self.config.model)
        self.prompt = get_prompt(self.config.prompt_version)

    async def grade(
        self, *, question: str, expected: str, response: str, truncated: bool = False
    ) -> JudgeResult:
        cfg = self.config
        user = self.prompt.render_user(
            question=question, expected=expected, response=response, truncated=truncated
        )
        messages = [
            {"role": "system", "content": self.prompt.system},
            {"role": "user", "content": user},
        ]

        last_raw: str | None = None
        last_err: str | None = None
        last_usage: dict[str, Any] | None = None
        last_latency: float | None = None
        attempt_usages: list[dict[str, Any]] = []
        attempt_latencies: list[float] = []
        # One extra attempt on unparseable output, nudged to emit bare JSON. API-level failures
        # already retried inside client.chat, so they are not re-attempted here.
        for attempt in range(cfg.parse_retries + 1):
            reasoning = {"enabled": cfg.reasoning, "exclude": True}
            if cfg.reasoning_effort:
                reasoning["effort"] = cfg.reasoning_effort
            res = await self.client.chat(
                messages,
                model_slug=cfg.model,
                provider=cfg.provider,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                seed=cfg.seed,
                response_format={"type": "json_object"},
                extra_body={"reasoning": reasoning},
            )
            if res.get("usage"):
                attempt_usages.append(res["usage"])
            if res.get("latency_s") is not None:
                attempt_latencies.append(float(res["latency_s"]))
            if res.get("error"):
                return self._fail(
                    res["error"],
                    raw=None,
                    usage=_aggregate_usage(attempt_usages),
                    latency=sum(attempt_latencies) if attempt_latencies else None,
                )

            last_raw = res.get("content")
            last_usage = res.get("usage")
            last_latency = res.get("latency_s")
            parsed = _extract_json(last_raw or "")
            coerced = _coerce(parsed) if parsed is not None else None
            if coerced is not None:
                label, score, extras = coerced
                return JudgeResult(
                    label=label, score=score, judge_model=cfg.model,
                    prompt_version=cfg.prompt_version,
                    latency_s=sum(attempt_latencies) if attempt_latencies else None,
                    usage=_aggregate_usage(attempt_usages), error=None, raw=None, **extras,
                )
            last_err = "unparseable judge output"
            if attempt < cfg.parse_retries:
                messages.append({"role": "assistant", "content": last_raw or ""})
                messages.append({"role": "user", "content":
                                 "Return ONLY the JSON object with keys label, score, covers, "
                                 "omits, failure_modes, rationale. No other text."})

        return self._fail(
            last_err or "grading failed",
            raw=last_raw,
            usage=_aggregate_usage(attempt_usages) or last_usage,
            latency=sum(attempt_latencies) if attempt_latencies else last_latency,
        )

    def _fail(self, error: str, *, raw: str | None, usage: dict | None = None,
              latency: float | None = None) -> JudgeResult:
        return JudgeResult(
            label=None, score=None, judge_model=self.config.model,
            prompt_version=self.config.prompt_version, latency_s=latency, usage=usage,
            error=error, raw=raw,
        )


# --- batch runner -------------------------------------------------------------------------------

# Identifying fields copied from the response record onto the graded record, so graded.jsonl is
# self-contained for the gap analysis without a join back to responses.jsonl.
_CARRY = (
    "benchmark_version", "prompt_id", "concept_id", "concept", "stratum", "tier", "frame",
    "frame_type", "condition", "control_entity", "model_key",
)


def _done_keys(path: Path) -> set[tuple]:
    """Grades already completed, keyed so a re-run resumes rather than repeating spend."""
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("judge_error") is None and r.get("score") is not None:
            done.add((r.get("judge_model"), r.get("prompt_version"),
                      r.get("model_key"), r.get("prompt_id")))
    return done


def _gradeable(rows: list[dict], expected_field: str, response_field: str) -> list[dict]:
    """Rows with a reference card and a completed model generation.

    A generation that exhausts its token budget in reasoning can legitimately have a null final
    answer; grade it as an empty, truncated response rather than silently excluding the outcome.
    Rows carrying an upstream error remain excluded. Both sensitive and control prompts may carry
    reference cards; condition is intentionally irrelevant here.
    """
    out = []
    for r in rows:
        if not (r.get(expected_field) or "").strip():
            continue
        if r.get("error"):
            continue
        if r.get(response_field) is None and r.get("finish_reason") != "length":
            continue
        out.append(r)
    return out


def _round_robin_models(rows: list[dict]) -> list[dict]:
    """Interleave model arms while preserving the source order within each arm.

    Response files are grouped by model. Taking a prefix directly would make a smoke test
    exercise only the first arm, which is particularly unhelpful for a comparative judge.
    """
    buckets: dict[str, deque[dict]] = defaultdict(deque)
    model_order: list[str] = []
    for row in rows:
        model = str(row.get("model_key") or "")
        if model not in buckets:
            model_order.append(model)
        buckets[model].append(row)

    interleaved: list[dict] = []
    while any(buckets[model] for model in model_order):
        for model in model_order:
            if buckets[model]:
                interleaved.append(buckets[model].popleft())
    return interleaved


async def grade_file(
    responses_path: Path,
    out_path: Path,
    *,
    config: JudgeConfig | None = None,
    expected_field: str = "must_engage",
    response_field: str = "content",
    limit: int | None = None,
    concurrency: int = 8,
    progress_every: int = 20,
) -> dict:
    """Grade every gradeable response in `responses_path`, writing one JSON line per grade.

    Resumable: already-completed (judge_model, prompt_version, model_key, prompt_id) tuples are
    skipped, so an interrupted run re-runs the same command. Returns a summary dict.
    """
    config = config or JudgeConfig()
    guard_judge_model(config.model)
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(l) for l in responses_path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    gradeable = _gradeable(rows, expected_field, response_field)

    done = _done_keys(out_path)
    unfinished = [
        r for r in gradeable
        if (config.model, config.prompt_version, r.get("model_key"), r.get("prompt_id")) not in done
    ]
    todo = _round_robin_models(unfinished)
    if limit is not None:
        todo = todo[:limit]
    skipped = len(gradeable) - len(unfinished)

    print(f"judge={config.model}  rubric={config.prompt_version}")
    print(
        f"{len(rows)} responses -> {len(gradeable)} gradeable; "
        f"{skipped} already graded, {len(todo)} to grade"
    )
    if not todo:
        return {"graded": 0, "skipped": skipped, "failed": 0}

    counts = {"ok": 0, "failed": 0}
    lock = asyncio.Lock()

    async with OpenRouterClient(concurrency=concurrency) as client:
        judge = Judge(client, config)
        with out_path.open("a", encoding="utf-8") as fh:

            async def one(row: dict) -> None:
                result = await judge.grade(
                    question=row.get("prompt", ""),
                    expected=row[expected_field],
                    response=row[response_field] or "",
                    truncated=row.get("finish_reason") == "length",
                )
                rec = {k: row.get(k) for k in _CARRY}
                rec["response_finish_reason"] = row.get("finish_reason")
                rec["response_truncated"] = row.get("finish_reason") == "length"
                rec["expected_field"] = expected_field
                rec["expected"] = row[expected_field]
                rec["graded_at_utc"] = datetime.now(timezone.utc).isoformat()
                rec.update(result.as_dict())
                async with lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    counts["failed" if result.error else "ok"] += 1
                    n = counts["ok"] + counts["failed"]
                    if n % progress_every == 0 or n == len(todo):
                        print(f"  {n}/{len(todo)}  ok={counts['ok']} failed={counts['failed']}")

            await asyncio.gather(*(one(r) for r in todo))

    return {"graded": len(todo), "skipped": skipped, "failed": counts["failed"]}


def grade_responses(
    responses_path: Path, out_path: Path, *, config: JudgeConfig | None = None, **kwargs
) -> dict:
    """Synchronous wrapper around `grade_file` for scripts and notebooks."""
    return asyncio.run(grade_file(responses_path, out_path, config=config, **kwargs))
