"""Small, resumable clients for the OpenAI and Anthropic batch APIs.

The lifecycle mirrors the battle-tested batch runner in the compliance-geometry
repository: persist every remote identifier before moving to the next step,
poll resumably, download raw JSONL results, and retry only failed or missing
request lines. Credentials are read from environment variables and are never
written to the job directory.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


OPENAI_API_ROOT = "https://api.openai.com/v1"
ANTHROPIC_API_ROOT = "https://api.anthropic.com/v1"
OPENAI_TERMINAL_STATES = {"completed", "failed", "expired", "cancelled"}
ANTHROPIC_TERMINAL_STATE = "ended"
RETRYABLE_HTTP_CODES = {408, 409, 429, 500, 502, 503, 504}


class BatchAPIError(RuntimeError):
    """A batch provider returned a terminal error or malformed response."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            values.append(value)
    return values


class _JSONHTTPClient:
    api_root: str

    def __init__(self, *, timeout_seconds: float = 180, max_http_attempts: int = 5) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_http_attempts = max_http_attempts

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _request(
        self,
        *,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str | None = "application/json",
    ) -> bytes:
        headers = self._headers()
        if body is not None and content_type is not None:
            headers["Content-Type"] = content_type
        last_error: BaseException | None = None
        for attempt in range(self.max_http_attempts):
            request = urllib.request.Request(
                f"{self.api_root}{path}",
                data=body,
                method=method,
                headers=headers,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:4000]
                last_error = BatchAPIError(
                    f"HTTP {exc.code} for {method} {path}: {detail or exc.reason}"
                )
                if exc.code not in RETRYABLE_HTTP_CODES:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt + 1 < self.max_http_attempts:
                time.sleep(min(2**attempt, 16))
        raise BatchAPIError(
            f"request failed after {self.max_http_attempts} attempts: {method} {path}"
        ) from last_error

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        value: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None if value is None else json.dumps(value, separators=(",", ":")).encode()
        parsed = json.loads(self._request(method=method, path=path, body=body))
        if not isinstance(parsed, dict):
            raise BatchAPIError(f"{method} {path} returned a non-object response")
        return parsed


class OpenAIBatchClient(_JSONHTTPClient):
    """Minimal authenticated client for OpenAI Files and Batch endpoints."""

    api_root = OPENAI_API_ROOT

    def __init__(
        self,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_seconds: float = 180,
        max_http_attempts: int = 5,
    ) -> None:
        api_key = (os.environ.get(api_key_env) or "").strip()
        if not api_key:
            raise RuntimeError(
                f"{api_key_env} is not set; export it or place it in the ignored .env file"
            )
        self._api_key = api_key
        super().__init__(
            timeout_seconds=timeout_seconds,
            max_http_attempts=max_http_attempts,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def upload_batch_file(self, path: Path) -> dict[str, Any]:
        payload = path.read_bytes()
        boundary = f"codex-{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="purpose"\r\n\r\n',
            b"batch\r\n",
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\n'
            ).encode(),
            b"Content-Type: application/jsonl\r\n\r\n",
            payload,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        parsed = json.loads(
            self._request(
                method="POST",
                path="/files",
                body=b"".join(parts),
                content_type=f"multipart/form-data; boundary={boundary}",
            )
        )
        if not isinstance(parsed, dict) or not parsed.get("id"):
            raise BatchAPIError("OpenAI file upload did not return a file id")
        return parsed

    def create_batch(
        self,
        *,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            method="POST",
            path="/batches",
            value={
                "input_file_id": input_file_id,
                "endpoint": endpoint,
                "completion_window": "24h",
                "metadata": metadata or {},
            },
        )

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request_json(method="GET", path=f"/batches/{batch_id}")

    def download_file(self, file_id: str) -> bytes:
        return self._request(
            method="GET",
            path=f"/files/{file_id}/content",
            content_type=None,
        )


class AnthropicBatchClient(_JSONHTTPClient):
    """Minimal authenticated client for Anthropic Message Batches."""

    api_root = ANTHROPIC_API_ROOT

    def __init__(
        self,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        anthropic_version: str = "2023-06-01",
        timeout_seconds: float = 180,
        max_http_attempts: int = 5,
    ) -> None:
        api_key = (os.environ.get(api_key_env) or "").strip()
        if not api_key:
            raise RuntimeError(
                f"{api_key_env} is not set; export it or place it in the ignored .env file"
            )
        self._api_key = api_key
        self._anthropic_version = anthropic_version
        super().__init__(
            timeout_seconds=timeout_seconds,
            max_http_attempts=max_http_attempts,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._anthropic_version,
        }

    def create_batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request_json(
            method="POST",
            path="/messages/batches",
            value={"requests": requests},
        )

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request_json(
            method="GET",
            path=f"/messages/batches/{batch_id}",
        )

    def download_results(self, batch_id: str) -> bytes:
        return self._request(
            method="GET",
            path=f"/messages/batches/{batch_id}/results",
            content_type=None,
        )


def execute_openai_batch(
    *,
    client: OpenAIBatchClient,
    input_path: Path,
    job_dir: Path,
    endpoint: str = "/v1/responses",
    metadata: dict[str, str] | None = None,
    poll_interval_seconds: float = 60,
    status_callback: Callable[[dict[str, Any]], None] | None = None,
    allow_partial_terminal: bool = False,
) -> dict[str, Any]:
    """Submit or resume one OpenAI batch and download terminal outputs."""

    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / "state.json"
    output_path = job_dir / "output.jsonl"
    error_path = job_dir / "errors.jsonl"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    if output_path.exists() and state.get("status") == "completed":
        return state

    if not state.get("input_file_id"):
        uploaded = client.upload_batch_file(input_path)
        state.update(
            {
                "input_path": str(input_path.resolve()),
                "input_file_id": str(uploaded["id"]),
                "uploaded_file": uploaded,
            }
        )
        write_json(state_path, state)

    if not state.get("batch_id"):
        batch = client.create_batch(
            input_file_id=str(state["input_file_id"]),
            endpoint=endpoint,
            metadata=metadata,
        )
        state.update(
            {
                "batch_id": str(batch["id"]),
                "status": str(batch.get("status", "unknown")),
                "batch": batch,
            }
        )
        write_json(state_path, state)

    last_status = ""
    while True:
        batch = client.retrieve_batch(str(state["batch_id"]))
        status = str(batch.get("status", "unknown"))
        state.update({"status": status, "batch": batch})
        write_json(state_path, state)
        if status_callback is not None and status != last_status:
            status_callback(batch)
        last_status = status
        if status in OPENAI_TERMINAL_STATES:
            break
        time.sleep(poll_interval_seconds)

    if batch.get("output_file_id") and not output_path.exists():
        write_bytes(output_path, client.download_file(str(batch["output_file_id"])))
    if batch.get("error_file_id") and not error_path.exists():
        write_bytes(error_path, client.download_file(str(batch["error_file_id"])))

    state.update(
        {
            "output_path": str(output_path) if output_path.exists() else None,
            "error_path": str(error_path) if error_path.exists() else None,
        }
    )
    write_json(state_path, state)
    if state["status"] != "completed" and not allow_partial_terminal:
        raise BatchAPIError(
            f"OpenAI batch {state['batch_id']} ended with status {state['status']}"
        )
    return state


def execute_anthropic_batch(
    *,
    client: AnthropicBatchClient,
    input_path: Path,
    job_dir: Path,
    metadata: dict[str, str] | None = None,
    poll_interval_seconds: float = 60,
    status_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Submit or resume one Anthropic batch and download its result JSONL."""

    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / "state.json"
    output_path = job_dir / "output.jsonl"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    if output_path.exists() and state.get("status") == ANTHROPIC_TERMINAL_STATE:
        return state

    if not state.get("batch_id"):
        batch = client.create_batch(read_jsonl(input_path))
        state.update(
            {
                "input_path": str(input_path.resolve()),
                "batch_id": str(batch["id"]),
                "status": str(batch.get("processing_status", "unknown")),
                "batch": batch,
                "metadata": metadata or {},
            }
        )
        write_json(state_path, state)

    last_status = ""
    while True:
        batch = client.retrieve_batch(str(state["batch_id"]))
        status = str(batch.get("processing_status", "unknown"))
        state.update({"status": status, "batch": batch})
        write_json(state_path, state)
        if status_callback is not None and status != last_status:
            status_callback(batch)
        last_status = status
        if status == ANTHROPIC_TERMINAL_STATE:
            break
        time.sleep(poll_interval_seconds)

    if not output_path.exists():
        write_bytes(output_path, client.download_results(str(state["batch_id"])))
    state["output_path"] = str(output_path)
    write_json(state_path, state)
    return state


def _openai_success(value: dict[str, Any]) -> bool:
    response = value.get("response")
    return (
        not value.get("error")
        and isinstance(response, dict)
        and int(response.get("status_code", 0)) == 200
    )


def _anthropic_success(value: dict[str, Any]) -> bool:
    result = value.get("result")
    return isinstance(result, dict) and result.get("type") == "succeeded"


def execute_batch_with_retries(
    *,
    provider: str,
    client: OpenAIBatchClient | AnthropicBatchClient,
    input_path: Path,
    job_dir: Path,
    metadata: dict[str, str] | None = None,
    max_request_retries: int = 2,
    poll_interval_seconds: float = 60,
    status_callback: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """Retry failed or missing request lines and preserve all successful work."""

    requests = read_jsonl(input_path)
    request_by_id = {str(item["custom_id"]): item for item in requests}
    if len(request_by_id) != len(requests):
        raise ValueError(f"{input_path}: custom_id values must be unique")
    successful: dict[str, dict[str, Any]] = {}
    latest_failure: dict[str, dict[str, Any]] = {}
    pending_ids = set(request_by_id)

    for attempt in range(max_request_retries + 1):
        if not pending_ids:
            break
        attempt_dir = job_dir / f"attempt-{attempt + 1:02d}"
        attempt_input = attempt_dir / "input.jsonl"
        write_jsonl(
            attempt_input,
            (request_by_id[custom_id] for custom_id in sorted(pending_ids)),
        )
        attempt_metadata = dict(metadata or {})
        attempt_metadata["retry_attempt"] = str(attempt + 1)
        if provider == "openai":
            if not isinstance(client, OpenAIBatchClient):
                raise TypeError("openai provider requires OpenAIBatchClient")
            execute_openai_batch(
                client=client,
                input_path=attempt_input,
                job_dir=attempt_dir,
                metadata=attempt_metadata,
                poll_interval_seconds=poll_interval_seconds,
                status_callback=status_callback,
                allow_partial_terminal=True,
            )
            output_paths = (attempt_dir / "output.jsonl", attempt_dir / "errors.jsonl")
            is_success = _openai_success
        elif provider == "anthropic":
            if not isinstance(client, AnthropicBatchClient):
                raise TypeError("anthropic provider requires AnthropicBatchClient")
            execute_anthropic_batch(
                client=client,
                input_path=attempt_input,
                job_dir=attempt_dir,
                metadata=attempt_metadata,
                poll_interval_seconds=poll_interval_seconds,
                status_callback=status_callback,
            )
            output_paths = (attempt_dir / "output.jsonl",)
            is_success = _anthropic_success
        else:
            raise ValueError(f"unsupported batch provider: {provider}")

        observed: set[str] = set()
        for output_path in output_paths:
            if not output_path.exists():
                continue
            for value in read_jsonl(output_path):
                custom_id = str(value.get("custom_id", ""))
                if custom_id not in pending_ids:
                    continue
                observed.add(custom_id)
                if is_success(value):
                    successful[custom_id] = value
                    latest_failure.pop(custom_id, None)
                else:
                    latest_failure[custom_id] = value
        pending_ids -= set(successful)
        for custom_id in pending_ids - observed:
            latest_failure[custom_id] = {
                "custom_id": custom_id,
                "response": None,
                "error": {
                    "code": "missing_batch_result",
                    "message": "No output or error line was returned for this request.",
                },
            }

    combined_path = job_dir / "combined-output.jsonl"
    write_jsonl(
        combined_path,
        (
            (
                successful[custom_id]
                if custom_id in successful
                else latest_failure[custom_id]
            )
            for custom_id in sorted(request_by_id)
        ),
    )
    write_json(
        job_dir / "retry-summary.json",
        {
            "requests": len(request_by_id),
            "successful": len(successful),
            "failed_after_retries": len(request_by_id) - len(successful),
            "max_request_retries": max_request_retries,
        },
    )
    return combined_path
