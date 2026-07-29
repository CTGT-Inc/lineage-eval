"""Native batch-API judging with synchronous-compatible graded records."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .batch_api import (
    AnthropicBatchClient,
    BatchAPIError,
    OpenAIBatchClient,
    execute_batch_with_retries,
    read_jsonl,
    write_json,
    write_jsonl,
)
from .judge import (
    _CARRY,
    _aggregate_usage,
    _coerce,
    _done_keys,
    _extract_json,
    _gradeable,
    _round_robin_models,
    JudgeConfig,
    JudgeResult,
    guard_judge_model,
)
from .judge_prompts import FAILURE_MODES, LABELS, get_prompt


@dataclass(frozen=True)
class BatchJudgeSpec:
    """Provider request identity and the stable identity written to graded JSONL."""

    provider: str
    api_model: str
    judge_model: str
    batch_input_usd_per_million: float | None = None
    batch_cached_input_usd_per_million: float | None = None
    batch_output_usd_per_million: float | None = None


DEFAULT_BATCH_JUDGES: dict[str, BatchJudgeSpec] = {
    "gpt": BatchJudgeSpec(
        provider="openai",
        api_model="gpt-5-mini",
        judge_model="openai/gpt-5-mini",
        batch_input_usd_per_million=0.25,
        batch_cached_input_usd_per_million=0.025,
        batch_output_usd_per_million=2.0,
    ),
    "sonnet": BatchJudgeSpec(
        provider="anthropic",
        api_model="claude-sonnet-4-6",
        judge_model="anthropic/claude-sonnet-4.6",
        batch_input_usd_per_million=1.5,
        batch_output_usd_per_million=7.5,
    ),
}


@dataclass(frozen=True)
class BatchCompletion:
    content: str | None
    usage: dict[str, Any] | None
    error: str | None
    raw: str | None


def _judge_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": list(LABELS)},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "covers": {"type": "array", "items": {"type": "string"}},
            "omits": {"type": "array", "items": {"type": "string"}},
            "failure_modes": {
                "type": "array",
                "items": {"type": "string", "enum": list(FAILURE_MODES)},
            },
            "rationale": {"type": "string"},
        },
        "required": [
            "label",
            "score",
            "covers",
            "omits",
            "failure_modes",
            "rationale",
        ],
        "additionalProperties": False,
    }


def _anthropic_judge_schema() -> dict[str, Any]:
    """Return the schema subset supported by Anthropic structured outputs."""

    schema = _judge_schema()
    score = schema["properties"]["score"]
    score.pop("minimum", None)
    score.pop("maximum", None)
    return schema


def _custom_id(spec: BatchJudgeSpec, config: JudgeConfig, row: dict[str, Any]) -> str:
    identity = "\x1f".join(
        (
            spec.judge_model,
            config.prompt_version,
            str(row.get("model_key") or ""),
            str(row.get("prompt_id") or ""),
        )
    )
    return "judge-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _messages(
    row: dict[str, Any],
    *,
    config: JudgeConfig,
    expected_field: str,
    response_field: str,
) -> list[dict[str, str]]:
    prompt = get_prompt(config.prompt_version)
    return [
        {"role": "system", "content": prompt.system},
        {
            "role": "user",
            "content": prompt.render_user(
                question=str(row.get("prompt") or ""),
                expected=str(row[expected_field]),
                response=str(row.get(response_field) or ""),
                truncated=row.get("finish_reason") == "length",
            ),
        },
    ]


def _openai_request(
    *,
    custom_id: str,
    messages: list[dict[str, str]],
    spec: BatchJudgeSpec,
    config: JudgeConfig,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": spec.api_model,
        "input": messages,
        "max_output_tokens": config.max_tokens,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "censorship_grade",
                "strict": True,
                "schema": _judge_schema(),
            }
        },
    }
    if config.reasoning:
        body["reasoning"] = {"effort": config.reasoning_effort or "medium"}
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": body,
    }


def _anthropic_request(
    *,
    custom_id: str,
    messages: list[dict[str, str]],
    spec: BatchJudgeSpec,
    config: JudgeConfig,
) -> dict[str, Any]:
    system = next(message["content"] for message in messages if message["role"] == "system")
    conversation = [
        message for message in messages if message["role"] in {"user", "assistant"}
    ]
    params: dict[str, Any] = {
        "model": spec.api_model,
        "system": system,
        "messages": conversation,
        "max_tokens": config.max_tokens,
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": _anthropic_judge_schema(),
            }
        },
    }
    if config.reasoning:
        # Native extended thinking requires at least 1,024 tokens and a larger max_tokens.
        if config.max_tokens <= 1024:
            raise ValueError("Anthropic reasoning requires max_tokens greater than 1024")
        params["thinking"] = {"type": "enabled", "budget_tokens": 1024}
    else:
        params["temperature"] = config.temperature
    return {"custom_id": custom_id, "params": params}


def _request(
    *,
    custom_id: str,
    messages: list[dict[str, str]],
    spec: BatchJudgeSpec,
    config: JudgeConfig,
) -> dict[str, Any]:
    if spec.provider == "openai":
        return _openai_request(
            custom_id=custom_id,
            messages=messages,
            spec=spec,
            config=config,
        )
    if spec.provider == "anthropic":
        return _anthropic_request(
            custom_id=custom_id,
            messages=messages,
            spec=spec,
            config=config,
        )
    raise ValueError(f"unsupported batch provider: {spec.provider}")


def prepare_batch_judge(
    responses_path: Path,
    out_path: Path,
    job_dir: Path,
    *,
    spec: BatchJudgeSpec,
    config: JudgeConfig,
    expected_field: str = "must_engage",
    response_field: str = "content",
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a stable request file and row manifest without making API calls."""

    guard_judge_model(spec.judge_model)
    if config.model != spec.judge_model:
        raise ValueError("JudgeConfig.model must equal BatchJudgeSpec.judge_model")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / "input.jsonl"
    manifest_path = job_dir / "manifest.jsonl"
    run_path = job_dir / "run.json"
    responses_sha256 = _file_sha256(responses_path)

    if input_path.exists() or manifest_path.exists() or run_path.exists():
        if not (input_path.exists() and manifest_path.exists() and run_path.exists()):
            raise ValueError(f"{job_dir}: incomplete prepared batch state")
        run = json.loads(run_path.read_text(encoding="utf-8"))
        expected = {
            "provider": spec.provider,
            "api_model": spec.api_model,
            "judge_model": spec.judge_model,
            "prompt_version": config.prompt_version,
            "judge_config": asdict(config),
            "batch_spec": asdict(spec),
            "responses_path": str(responses_path.resolve()),
            "responses_sha256": responses_sha256,
            "out_path": str(out_path.resolve()),
            "expected_field": expected_field,
            "response_field": response_field,
            "limit": limit,
        }
        for key, value in expected.items():
            if run.get(key) != value:
                raise ValueError(
                    f"{job_dir}: prepared {key}={run.get(key)!r}, requested {value!r}"
                )
        return run

    rows = [
        json.loads(line)
        for line in responses_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gradeable = _gradeable(rows, expected_field, response_field)
    done = _done_keys(out_path)
    unfinished = [
        row
        for row in gradeable
        if (
            spec.judge_model,
            config.prompt_version,
            row.get("model_key"),
            row.get("prompt_id"),
        )
        not in done
    ]
    todo = _round_robin_models(unfinished)
    if limit is not None:
        todo = todo[:limit]

    manifest: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in todo:
        custom_id = _custom_id(spec, config, row)
        if custom_id in seen:
            raise ValueError(f"duplicate batch identity for {row.get('prompt_id')}")
        seen.add(custom_id)
        messages = _messages(
            row,
            config=config,
            expected_field=expected_field,
            response_field=response_field,
        )
        manifest.append({"custom_id": custom_id, "row": row, "messages": messages})
        requests.append(
            _request(
                custom_id=custom_id,
                messages=messages,
                spec=spec,
                config=config,
            )
        )

    run = {
        "provider": spec.provider,
        "api_model": spec.api_model,
        "judge_model": spec.judge_model,
        "prompt_version": config.prompt_version,
        "judge_config": asdict(config),
        "batch_spec": asdict(spec),
        "responses_path": str(responses_path.resolve()),
        "responses_sha256": responses_sha256,
        "out_path": str(out_path.resolve()),
        "expected_field": expected_field,
        "response_field": response_field,
        "responses": len(rows),
        "gradeable": len(gradeable),
        "already_graded": len(gradeable) - len(unfinished),
        "requests": len(requests),
        "limit": limit,
    }
    write_jsonl(input_path, requests)
    write_jsonl(manifest_path, manifest)
    write_json(run_path, run)
    return run


def _priced_usage(
    usage: dict[str, Any] | None,
    *,
    spec: BatchJudgeSpec,
) -> dict[str, Any] | None:
    if not usage:
        return None
    normalized = dict(usage)
    prompt_tokens = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    completion_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    cached_tokens = int(
        (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        or (usage.get("input_tokens_details") or {}).get("cached_tokens")
        or usage.get("cache_read_input_tokens")
        or 0
    )
    cache_write_tokens = int(usage.get("cache_creation_input_tokens") or 0)
    if spec.provider == "anthropic":
        # Anthropic reports non-cached, cache-read, and cache-write inputs separately.
        prompt_tokens += cached_tokens + cache_write_tokens
    normalized.update(
        {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
    )
    if cached_tokens or cache_write_tokens:
        normalized["prompt_tokens_details"] = {
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
        }
    output_details = usage.get("output_tokens_details") or {}
    reasoning_tokens = int(
        (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        or output_details.get("reasoning_tokens")
        or output_details.get("thinking_tokens")
        or 0
    )
    if reasoning_tokens:
        normalized["completion_tokens_details"] = {
            "reasoning_tokens": reasoning_tokens
        }
    if (
        spec.batch_input_usd_per_million is not None
        and spec.batch_output_usd_per_million is not None
    ):
        uncached = max(0, prompt_tokens - cached_tokens)
        input_cost = uncached * spec.batch_input_usd_per_million / 1_000_000
        if cached_tokens:
            cached_rate = (
                spec.batch_cached_input_usd_per_million
                if spec.batch_cached_input_usd_per_million is not None
                else spec.batch_input_usd_per_million
            )
            input_cost += cached_tokens * cached_rate / 1_000_000
        output_cost = completion_tokens * spec.batch_output_usd_per_million / 1_000_000
        normalized["cost"] = input_cost + output_cost
        normalized["cost_estimated_from_batch_list_price"] = True
    return normalized


def _openai_completion(value: dict[str, Any], spec: BatchJudgeSpec) -> BatchCompletion:
    if value.get("error"):
        return BatchCompletion(None, None, str(value["error"]), None)
    wrapper = value.get("response")
    if not isinstance(wrapper, dict):
        return BatchCompletion(None, None, "missing OpenAI response wrapper", None)
    if int(wrapper.get("status_code", 0)) != 200:
        return BatchCompletion(
            None,
            None,
            f"OpenAI response status {wrapper.get('status_code')}",
            None,
        )
    body = wrapper.get("body")
    if not isinstance(body, dict):
        return BatchCompletion(None, None, "missing OpenAI response body", None)
    texts: list[str] = []
    refusal: str | None = None
    for output in body.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text":
                texts.append(str(content.get("text", "")))
            elif content.get("type") == "refusal":
                refusal = str(content.get("refusal", ""))
    raw = "".join(texts) or None
    usage = _priced_usage(dict(body.get("usage") or {}), spec=spec)
    if refusal:
        return BatchCompletion(raw, usage, f"model refusal: {refusal}", raw)
    if not raw:
        incomplete = body.get("incomplete_details")
        detail = f": {incomplete}" if incomplete else ""
        return BatchCompletion(None, usage, f"response contained no output_text{detail}", None)
    return BatchCompletion(raw, usage, None, raw)


def _anthropic_completion(value: dict[str, Any], spec: BatchJudgeSpec) -> BatchCompletion:
    result = value.get("result")
    if not isinstance(result, dict):
        return BatchCompletion(None, None, "missing Anthropic result", None)
    if result.get("type") != "succeeded":
        return BatchCompletion(
            None,
            None,
            f"Anthropic result {result.get('type')}: {result.get('error')}",
            None,
        )
    message = result.get("message")
    if not isinstance(message, dict):
        return BatchCompletion(None, None, "missing Anthropic message", None)
    texts = [
        str(block.get("text", ""))
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    raw = "".join(texts) or None
    usage = _priced_usage(dict(message.get("usage") or {}), spec=spec)
    if not raw:
        return BatchCompletion(None, usage, "response contained no text block", None)
    return BatchCompletion(raw, usage, None, raw)


def _completion(
    value: dict[str, Any],
    *,
    spec: BatchJudgeSpec,
) -> BatchCompletion:
    if spec.provider == "openai":
        return _openai_completion(value, spec)
    if spec.provider == "anthropic":
        return _anthropic_completion(value, spec)
    raise ValueError(f"unsupported batch provider: {spec.provider}")


def _retry_messages(
    manifest: dict[str, Any],
    completion: BatchCompletion,
) -> list[dict[str, str]]:
    messages = [dict(message) for message in manifest["messages"]]
    if completion.raw:
        messages.append({"role": "assistant", "content": completion.raw})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Return ONLY the JSON object with keys label, score, covers, omits, "
                    "failure_modes, rationale. No other text."
                ),
            }
        )
    return messages


def _grade_record(
    *,
    manifest: dict[str, Any],
    result: JudgeResult,
    expected_field: str,
) -> dict[str, Any]:
    row = manifest["row"]
    record = {key: row.get(key) for key in _CARRY}
    record["response_finish_reason"] = row.get("finish_reason")
    record["response_truncated"] = row.get("finish_reason") == "length"
    record["expected_field"] = expected_field
    record["expected"] = row[expected_field]
    record["graded_at_utc"] = datetime.now(timezone.utc).isoformat()
    record.update(result.as_dict())
    return record


def _existing_attempt_keys(path: Path) -> set[tuple[Any, ...]]:
    if not path.exists():
        return set()
    keys = set()
    for row in read_jsonl(path):
        keys.add(
            (
                row.get("judge_model"),
                row.get("prompt_version"),
                row.get("model_key"),
                row.get("prompt_id"),
                row.get("judge_error"),
            )
        )
    return keys


def run_batch_judge(
    responses_path: Path,
    out_path: Path,
    job_dir: Path,
    *,
    spec: BatchJudgeSpec,
    config: JudgeConfig,
    expected_field: str = "must_engage",
    response_field: str = "content",
    limit: int | None = None,
    max_request_retries: int = 2,
    poll_interval_seconds: float = 60,
    parse_retry_max_tokens: int | None = None,
) -> dict[str, Any]:
    """Prepare, execute, parse-retry, and append synchronous-shaped grades."""

    prepared = prepare_batch_judge(
        responses_path,
        out_path,
        job_dir,
        spec=spec,
        config=config,
        expected_field=expected_field,
        response_field=response_field,
        limit=limit,
    )
    if not prepared["requests"]:
        return {"graded": 0, "skipped": prepared["already_graded"], "failed": 0}

    client: OpenAIBatchClient | AnthropicBatchClient
    if spec.provider == "openai":
        client = OpenAIBatchClient()
    elif spec.provider == "anthropic":
        client = AnthropicBatchClient()
    else:
        raise ValueError(f"unsupported batch provider: {spec.provider}")

    def status(batch: dict[str, Any]) -> None:
        state = batch.get("status") or batch.get("processing_status")
        counts = batch.get("request_counts") or {}
        print(f"batch={state} counts={json.dumps(counts, sort_keys=True)}", flush=True)

    metadata = {
        "experiment": responses_path.parent.parent.name,
        "stage": "censorship-v2-judging",
        "judge": spec.judge_model,
        "rubric": config.prompt_version,
    }
    output_path = execute_batch_with_retries(
        provider=spec.provider,
        client=client,
        input_path=job_dir / "input.jsonl",
        job_dir=job_dir / "batch",
        metadata=metadata,
        max_request_retries=max_request_retries,
        poll_interval_seconds=poll_interval_seconds,
        status_callback=status,
    )

    manifests = read_jsonl(job_dir / "manifest.jsonl")
    manifest_by_id = {str(item["custom_id"]): item for item in manifests}
    attempts: dict[str, list[BatchCompletion]] = {
        custom_id: [] for custom_id in manifest_by_id
    }
    current_output = output_path
    final_results: dict[str, JudgeResult] = {}

    for parse_attempt in range(config.parse_retries + 1):
        result_by_id = {
            str(value.get("custom_id", "")): value
            for value in read_jsonl(current_output)
        }
        retry_ids: list[str] = []
        retry_completions: dict[str, BatchCompletion] = {}
        for custom_id in manifest_by_id:
            if custom_id in final_results:
                continue
            value = result_by_id.get(custom_id)
            completion = (
                _completion(value, spec=spec)
                if value is not None
                else BatchCompletion(None, None, "missing batch result", None)
            )
            attempts[custom_id].append(completion)
            parsed = _extract_json(completion.content or "") if not completion.error else None
            coerced = _coerce(parsed) if parsed is not None else None
            usages = [attempt.usage for attempt in attempts[custom_id] if attempt.usage]
            usage = _aggregate_usage(usages)
            if coerced is not None:
                label, score, extras = coerced
                final_results[custom_id] = JudgeResult(
                    label=label,
                    score=score,
                    judge_model=spec.judge_model,
                    prompt_version=config.prompt_version,
                    latency_s=None,
                    usage=usage,
                    error=None,
                    raw=None,
                    **extras,
                )
            elif parse_attempt < config.parse_retries:
                retry_ids.append(custom_id)
                retry_completions[custom_id] = completion
            else:
                final_results[custom_id] = JudgeResult(
                    label=None,
                    score=None,
                    judge_model=spec.judge_model,
                    prompt_version=config.prompt_version,
                    latency_s=None,
                    usage=usage,
                    error=completion.error or "unparseable judge output",
                    raw=completion.raw,
                )

        if not retry_ids:
            break
        retry_config = (
            replace(
                config,
                max_tokens=max(config.max_tokens, parse_retry_max_tokens),
            )
            if parse_retry_max_tokens is not None
            else config
        )
        retry_suffix = (
            f"-max-{retry_config.max_tokens}"
            if retry_config.max_tokens != config.max_tokens
            else ""
        )
        retry_dir = (
            job_dir
            / f"parse-retry-{parse_attempt + 1:02d}{retry_suffix}"
        )
        retry_input = retry_dir / "input.jsonl"
        retry_requests = [
            _request(
                custom_id=custom_id,
                messages=_retry_messages(
                    manifest_by_id[custom_id],
                    retry_completions[custom_id],
                ),
                spec=spec,
                config=retry_config,
            )
            for custom_id in retry_ids
        ]
        if retry_input.exists():
            if read_jsonl(retry_input) != retry_requests:
                raise ValueError(f"{retry_input}: retry request content changed")
        else:
            write_jsonl(retry_input, retry_requests)
        current_output = execute_batch_with_retries(
            provider=spec.provider,
            client=client,
            input_path=retry_input,
            job_dir=retry_dir / "batch",
            metadata=metadata | {"parse_retry": str(parse_attempt + 1)},
            max_request_retries=max_request_retries,
            poll_interval_seconds=poll_interval_seconds,
            status_callback=status,
        )

    if set(final_results) != set(manifest_by_id):
        missing = sorted(set(manifest_by_id) - set(final_results))
        raise BatchAPIError(f"missing {len(missing)} finalized judge results")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    successful_done = _done_keys(out_path)
    existing_attempts = _existing_attempt_keys(out_path)
    written = 0
    failed = 0
    with out_path.open("a", encoding="utf-8") as handle:
        for manifest in manifests:
            custom_id = str(manifest["custom_id"])
            result = final_results[custom_id]
            row = manifest["row"]
            success_key = (
                spec.judge_model,
                config.prompt_version,
                row.get("model_key"),
                row.get("prompt_id"),
            )
            attempt_key = success_key + (result.error,)
            if success_key in successful_done or attempt_key in existing_attempts:
                continue
            record = _grade_record(
                manifest=manifest,
                result=result,
                expected_field=expected_field,
            )
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            written += 1
            failed += int(result.error is not None)
    return {
        "graded": written,
        "skipped": prepared["already_graded"] + len(manifests) - written,
        "failed": failed,
        "out_path": str(out_path),
        "job_dir": str(job_dir),
    }
