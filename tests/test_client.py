"""Focused transport-retry tests for the OpenRouter client."""
from __future__ import annotations

import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from censorship.client import OpenRouterClient  # noqa: E402


class _FlakySslClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, url: str, *, json: dict) -> httpx.Response:
        self.calls += 1
        if self.calls == 1:
            raise ssl.SSLError("bad record mac")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
            request=httpx.Request("POST", url),
        )


class OpenRouterTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_ssl_error_is_retried(self) -> None:
        client = OpenRouterClient(api_key="test", concurrency=1, max_attempts=2)
        flaky = _FlakySslClient()
        client._client = flaky  # type: ignore[assignment]

        with patch("censorship.client.asyncio.sleep", new=AsyncMock()):
            result = await client._post_with_retry({"model": "test"})

        self.assertIsNone(result.error)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(flaky.calls, 2)


if __name__ == "__main__":
    unittest.main()
