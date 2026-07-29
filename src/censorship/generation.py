"""Generation through a user-managed OpenAI-compatible inference endpoint."""
from __future__ import annotations

import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import ReleaseConfig
from .datasets import load_paths
from .inference import RecordSink, generate_from_endpoint
from .usage import summarise_tokens


def _batch_for(prompt: dict[str, Any]) -> str:
    if prompt.get("batch"):
        return str(prompt["batch"])
    return (
        "finance"
        if prompt.get("stratum") == "finance_adjacent"
        else "core_political"
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _served_models(
    config: ReleaseConfig,
    models: Iterable[str] | None,
) -> tuple[str, ...]:
    values = tuple(
        value.strip() for value in (models or config.generation.model_keys)
    )
    if not values or any(not value for value in values):
        raise ValueError("at least one non-empty served model name is required")
    if len(values) != len(set(values)):
        raise ValueError("served model names must be unique")
    return values


class JSONLRecordSink(RecordSink):
    """Append-only response store with fsync checkpoints and resume support."""

    def __init__(self, path: Path, *, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def done_ids(self, model: str) -> set[str]:
        return {
            str(row["prompt_id"])
            for row in _read_jsonl(self.path)
            if row.get("model_key") == model
            and row.get("error") is None
            and row.get("content") is not None
        }

    def write(self, record: dict[str, Any]) -> None:
        value = {
            **record,
            "run_id": self.run_id,
            "batch": _batch_for(record),
        }
        self._handle.write(json.dumps(value, ensure_ascii=False) + "\n")

    def checkpoint(self) -> None:
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        self.checkpoint()
        self._handle.close()


def generation_plan(
    config: ReleaseConfig,
    *,
    base_url: str | None = None,
    models: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a static plan without contacting the inference endpoint."""

    prompts = load_paths(*config.benchmark.files)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        prompts = prompts[:limit]
    endpoint = (base_url or config.generation.base_url).rstrip("/")
    if not endpoint:
        raise ValueError("an OpenAI-compatible base URL is required")
    return {
        "run_id": config.run_id,
        "backend": "openai-compatible",
        "base_url": endpoint,
        "models": list(_served_models(config, models)),
        "benchmark_files": [str(path) for path in config.benchmark.files],
        "prompts": len(prompts),
        "decoding": {
            "temperature": config.generation.temperature,
            "top_p": config.generation.top_p,
            "max_tokens": config.generation.max_tokens,
            "seed": config.generation.seed,
        },
        "output": str(config.generation.output),
        "metadata": str(config.generation.metadata),
    }


def run_generation(
    config: ReleaseConfig,
    *,
    base_url: str | None = None,
    models: Iterable[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run or resume the benchmark against one or more served model names."""

    plan = generation_plan(
        config,
        base_url=base_url,
        models=models,
        limit=limit,
    )
    if dry_run:
        return plan

    prompts = load_paths(*config.benchmark.files)
    if limit is not None:
        prompts = prompts[:limit]
    prompts = [{**prompt, "batch": _batch_for(prompt)} for prompt in prompts]

    api_key = "local"
    if config.generation.api_key_env:
        api_key = os.environ.get(config.generation.api_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"{config.generation.api_key_env} is required for the configured "
                "inference endpoint"
            )

    selected_models = tuple(plan["models"])
    previous = (
        json.loads(config.generation.metadata.read_text(encoding="utf-8"))
        if config.generation.metadata.is_file()
        else {}
    )
    previous_models = tuple(previous.get("model_keys", ()))
    model_keys = list(dict.fromkeys((*previous_models, *selected_models)))
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "run_id": config.run_id,
        "experiment": config.run_id,
        "backend": "openai-compatible",
        "started_at_utc": previous.get("started_at_utc")
        or datetime.now(UTC).isoformat(),
        "finished_at_utc": None,
        "prompt_sets": [str(path) for path in config.benchmark.files],
        "n_prompts": len(prompts),
        "model_keys": model_keys,
        "decoding": plan["decoding"],
        "reasoning_response_field": "reasoning (fallback: reasoning_content)",
        "runtime": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "endpoint": {
            "base_url": plan["base_url"],
            "served_models": list(selected_models),
        },
        "models": dict(previous.get("models", {})),
        "error": None,
    }
    _write_json(config.generation.metadata, metadata)

    sink = JSONLRecordSink(config.generation.output, run_id=config.run_id)
    try:
        result = generate_from_endpoint(
            prompts,
            served_names=list(selected_models),
            base_url=plan["base_url"],
            api_key=api_key,
            sink=sink,
            temperature=config.generation.temperature,
            top_p=config.generation.top_p,
            max_tokens=config.generation.max_tokens,
            seed=config.generation.seed,
            concurrency=config.generation.concurrency,
        )
        rows = _read_jsonl(config.generation.output)
        for model in selected_models:
            metadata["models"][model] = {
                "served_name": model,
                "endpoint_base_url": plan["base_url"],
                "tokens": summarise_tokens(
                    [row for row in rows if row.get("model_key") == model]
                ),
                "generate_seconds": result["timings"][
                    "generate_seconds_by_model"
                ][model],
            }
        metadata["endpoint"]["timings"] = result["timings"]
    except Exception as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        sink.close()
        metadata["finished_at_utc"] = datetime.now(UTC).isoformat()
        _write_json(config.generation.metadata, metadata)

    return metadata
