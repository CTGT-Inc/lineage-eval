"""Generate through a user-managed OpenAI-compatible inference endpoint."""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any


class RecordSink:
    """Incremental store used to checkpoint expensive model responses."""

    def done_ids(self, model: str) -> set[str]:
        """Return prompt IDs already stored successfully for one model."""
        return set()

    def write(self, record: dict[str, Any]) -> None:
        """Append one response record."""

    def checkpoint(self) -> None:
        """Make all appended records durable."""


async def _generate_all(
    prompts: list[dict[str, Any]],
    *,
    served_name: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    seed: int | None,
    concurrency: int,
    sink: RecordSink,
    checkpoint_every: int = 25,
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Generate one model's outstanding prompts and checkpoint as they finish."""

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        timeout=600.0,
        max_retries=2,
    )
    semaphore = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()
    done = 0
    since_checkpoint = 0

    async def one(prompt: dict[str, Any]) -> dict[str, Any]:
        nonlocal done, since_checkpoint
        overrides = prompt.get("_decoding") or {}
        request_temperature = float(overrides.get("temperature", temperature))
        request_top_p = float(overrides.get("top_p", top_p))
        request_max_tokens = int(overrides.get("max_tokens", max_tokens))
        request_seed = overrides.get("seed", seed)
        if request_seed is not None:
            request_seed = int(request_seed)
        record: dict[str, Any] = {
            **{
                key: value
                for key, value in prompt.items()
                if key != "_decoding"
            },
            "model_key": served_name,
            "backend": "openai-compatible",
            "requested_at_utc": datetime.now(UTC).isoformat(),
            "decoding": {
                "temperature": request_temperature,
                "top_p": request_top_p,
                "max_tokens": request_max_tokens,
                "seed": request_seed,
            },
        }
        try:
            async with semaphore:
                started = time.perf_counter()
                response = await client.chat.completions.create(
                    model=served_name,
                    messages=[{"role": "user", "content": prompt["prompt"]}],
                    temperature=request_temperature,
                    top_p=request_top_p,
                    max_tokens=request_max_tokens,
                    seed=request_seed,
                )
                latency = time.perf_counter() - started
            choice = response.choices[0]
            message = choice.message
            usage = response.usage.model_dump() if response.usage else {}
            reasoning = getattr(message, "reasoning", None)
            if reasoning is None:
                reasoning = getattr(message, "reasoning_content", None)
            record.update(
                content=message.content,
                reasoning=reasoning,
                finish_reason=choice.finish_reason,
                usage=usage,
                reasoning_tokens=(
                    usage.get("completion_tokens_details") or {}
                ).get("reasoning_tokens"),
                latency_s=round(latency, 3),
                error=None,
            )
        except Exception as exc:  # a failed prompt is resumable data
            record.update(
                content=None,
                reasoning=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        async with write_lock:
            sink.write(record)
            since_checkpoint += 1
            done += 1
            if since_checkpoint >= checkpoint_every or done == len(prompts):
                await asyncio.to_thread(sink.checkpoint)
                since_checkpoint = 0
            if done % checkpoint_every == 0 or done == len(prompts):
                print(
                    f"  generated {done}/{len(prompts)} (checkpointed)",
                    flush=True,
                )
        return record

    try:
        return await asyncio.gather(*(one(prompt) for prompt in prompts))
    finally:
        await client.close()


def generate_from_endpoint(
    prompts: list[dict[str, Any]],
    *,
    served_names: list[str],
    base_url: str,
    api_key: str,
    sink: RecordSink | None = None,
    temperature: float = 0.0,
    top_p: float = 1.0,
    max_tokens: int = 4096,
    seed: int | None = 0,
    concurrency: int = 16,
) -> dict[str, Any]:
    """Run or resume prompts against model names exposed by one endpoint."""

    sink = sink or RecordSink()
    records: list[dict[str, Any]] = []
    per_model: dict[str, float] = {}
    started = time.perf_counter()
    for served_name in served_names:
        model_started = time.perf_counter()
        already = sink.done_ids(served_name)
        todo = [
            prompt for prompt in prompts
            if prompt["prompt_id"] not in already
        ]
        print(
            f"{served_name}: {len(already)} already stored, "
            f"{len(todo)} to generate",
            flush=True,
        )
        if todo:
            records.extend(
                asyncio.run(
                    _generate_all(
                        todo,
                        served_name=served_name,
                        temperature=temperature,
                        top_p=top_p,
                        max_tokens=max_tokens,
                        seed=seed,
                        concurrency=concurrency,
                        sink=sink,
                        base_url=base_url,
                        api_key=api_key,
                    )
                )
            )
        per_model[served_name] = round(
            time.perf_counter() - model_started,
            1,
        )
    sink.checkpoint()
    elapsed = round(time.perf_counter() - started, 1)
    return {
        "records": records,
        "timings": {
            "generate_seconds": elapsed,
            "wall_seconds": elapsed,
            "generate_seconds_by_model": per_model,
        },
        "base_url": base_url.rstrip("/"),
    }
