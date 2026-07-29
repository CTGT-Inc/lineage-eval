"""Provider-neutral response and token accounting."""
from __future__ import annotations


def summarise_tokens(records: list[dict]) -> dict:
    """Summarize completions while keeping reasoning tokens separate."""

    ok = [record for record in records if not record.get("error")]
    prompt_tokens = sum(
        (record.get("usage") or {}).get("prompt_tokens", 0) for record in ok
    )
    completion_tokens = sum(
        (record.get("usage") or {}).get("completion_tokens", 0) for record in ok
    )
    reasoning_values = [record.get("reasoning_tokens") for record in ok]
    reasoning_known = bool(ok) and all(
        value is not None for value in reasoning_values
    )
    reasoning_tokens = sum(reasoning_values) if reasoning_known else None
    reasoning_traces = sum(
        bool(str(record.get("reasoning") or "").strip()) for record in ok
    )
    truncated = sum(
        record.get("finish_reason") == "length" for record in ok
    )
    return {
        "responses_ok": len(ok),
        "responses_failed": len(records) - len(ok),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_traces_nonempty": reasoning_traces,
        "reasoning_tokens": reasoning_tokens,
        "reasoning_tokens_reported": sum(
            value is not None for value in reasoning_values
        ),
        "answer_tokens": (
            completion_tokens - reasoning_tokens
            if reasoning_tokens is not None
            else None
        ),
        "reasoning_share": (
            round(reasoning_tokens / completion_tokens, 3)
            if reasoning_tokens is not None and completion_tokens
            else None
        ),
        "truncated_responses": truncated,
    }
