"""Async OpenRouter transport used by the synchronous judge workers."""
from __future__ import annotations

import asyncio
import os
import ssl
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class _HttpResult:
    """Outcome of one POST-with-retry. Never an exception -- a failure is a value.

    `data` is the parsed body when the call returned JSON, kept even on an in-band error so a
    caller can still recover the request id. `error` is set iff the call did not yield a usable
    completion (transport failure, non-200, in-band error, or exhausted retries).
    """

    data: dict[str, Any] | None
    latency_s: float | None
    error: str | None
    attempts: int


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        concurrency: int = 8,
        timeout: float = 180.0,
        max_attempts: int = 4,
    ):
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set (expected in .env)")
        self._key = key
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenRouterClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                # Identifies the run on the OpenRouter dashboard; no effect on generation.
                "X-Title": "lineage-eval",
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def _post_with_retry(self, body: dict[str, Any]) -> _HttpResult:
        """POST with the shared retry policy. Never raises; the outcome is the return value.

        Success is an HTTP 200 whose body carries `choices`. OpenRouter also reports some
        upstream failures in-band with a 200 and an `error` field -- flagged as an error, but
        the parsed body is still returned so the caller keeps the request id.
        """
        assert self._client is not None, "use as an async context manager"
        last_err: str | None = None
        latency: float | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                # Timer starts *inside* the semaphore: outside it, latency_s would record
                # local queue wait as though it were provider latency.
                async with self._sem:
                    t0 = time.perf_counter()
                    r = await self._client.post(API_URL, json=body)
                    latency = time.perf_counter() - t0
            # Python 3.14/httpcore can occasionally leak a raw ssl.SSLError instead of
            # wrapping it as httpx.TransportError. Treat it as the same retryable transport
            # failure so one dropped TLS record cannot abort an otherwise resumable sweep.
            except (httpx.TimeoutException, httpx.TransportError, ssl.SSLError) as e:
                last_err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(min(2 ** attempt, 30))
                continue

            if r.status_code in RETRY_STATUS and attempt < self._max_attempts:
                last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                # Honour Retry-After when the provider sends one.
                wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 30))
                await asyncio.sleep(wait)
                continue

            if r.status_code != 200:
                return _HttpResult(None, latency, f"HTTP {r.status_code}: {r.text[:1000]}", attempt)

            data = r.json()
            if "error" in data and not data.get("choices"):
                return _HttpResult(data, latency, f"upstream: {str(data['error'])[:1000]}", attempt)
            return _HttpResult(data, latency, None, attempt)

        return _HttpResult(None, latency, last_err or "exhausted retries", self._max_attempts)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model_slug: str,
        provider: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        seed: int | None = 0,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Low-level chat call for an arbitrary slug (e.g. an LLM judge).

        Returns a small normalized record and never raises on provider failure.
        """
        assert self._client is not None, "use as an async context manager"
        body: dict[str, Any] = {
            "model": model_slug,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if seed is not None:
            body["seed"] = seed
        if provider:
            body["provider"] = {"order": [provider], "allow_fallbacks": False}
        if response_format:
            body["response_format"] = response_format
        if extra_body:
            body.update(extra_body)

        res = await self._post_with_retry(body)
        if res.error is not None:
            return {"content": None, "reasoning": None, "finish_reason": None,
                    "usage": (res.data or {}).get("usage"), "latency_s": res.latency_s,
                    "error": res.error}
        data = res.data or {}
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        return {
            "content": msg.get("content"),
            "reasoning": msg.get("reasoning") or msg.get("reasoning_content"),
            "finish_reason": choice.get("finish_reason"),
            "usage": data.get("usage"),
            "provider_served": data.get("provider"),
            "latency_s": res.latency_s,
            "error": None,
        }
