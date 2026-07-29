"""Load and validate the public, provider-neutral evaluation configuration."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class ConfigError(ValueError):
    """The release configuration is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class BenchmarkConfig:
    files: tuple[Path, ...]


@dataclass(frozen=True)
class GenerationConfig:
    output: Path
    metadata: Path
    model_keys: tuple[str, ...]
    temperature: float
    top_p: float
    max_tokens: int
    seed: int | None
    concurrency: int
    base_url: str
    api_key_env: str | None


@dataclass(frozen=True)
class JudgeTarget:
    key: str
    transport: str
    model: str
    provider: str | None
    api_model: str | None
    max_tokens: int
    reasoning: bool
    reasoning_effort: str | None
    parse_retries: int


@dataclass(frozen=True)
class JudgingConfig:
    output_dir: Path
    rubric: str
    concurrency: int
    poll_interval_seconds: float
    judges: tuple[JudgeTarget, ...]


@dataclass(frozen=True)
class AnalysisConfig:
    output_dir: Path


@dataclass(frozen=True)
class ReleaseConfig:
    path: Path
    run_id: str
    benchmark: BenchmarkConfig
    generation: GenerationConfig
    judging: JudgingConfig
    analysis: AnalysisConfig


def _table(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    return value


def _nonempty_strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty array")
    strings = tuple(str(item).strip() for item in value)
    if any(not item for item in strings):
        raise ConfigError(f"{name} cannot contain empty values")
    return strings


def _resolve(config_path: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a path string")
    path = Path(value)
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_config(path: str | Path) -> ReleaseConfig:
    """Load one release TOML file.

    Relative paths are resolved from the directory containing the TOML file, not
    from the caller's current working directory.
    """

    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file does not exist: {config_path}")
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    if raw.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    run_id = str(raw.get("run_id") or "").strip()
    if not run_id:
        raise ConfigError("run_id is required")

    benchmark_raw = _table(raw.get("benchmark"), "benchmark")
    benchmark_files = tuple(
        _resolve(config_path, item, "benchmark.files")
        for item in _nonempty_strings(benchmark_raw.get("files"), "benchmark.files")
    )

    generation_raw = _table(raw.get("generation"), "generation")
    model_keys = _nonempty_strings(
        generation_raw.get("model_keys"), "generation.model_keys"
    )
    if len(model_keys) != len(set(model_keys)):
        raise ConfigError("generation.model_keys must be unique")
    base_url = _optional_string(generation_raw.get("base_url"))
    if not base_url:
        raise ConfigError("generation.base_url is required")

    seed_value = generation_raw.get("seed", 0)
    generation = GenerationConfig(
        output=_resolve(config_path, generation_raw.get("output"), "generation.output"),
        metadata=_resolve(
            config_path, generation_raw.get("metadata"), "generation.metadata"
        ),
        model_keys=model_keys,
        temperature=float(generation_raw.get("temperature", 0.0)),
        top_p=float(generation_raw.get("top_p", 1.0)),
        max_tokens=int(generation_raw.get("max_tokens", 4096)),
        seed=None if seed_value is None else int(seed_value),
        concurrency=int(generation_raw.get("concurrency", 16)),
        base_url=base_url,
        api_key_env=_optional_string(generation_raw.get("api_key_env")),
    )
    if generation.max_tokens < 1:
        raise ConfigError("generation.max_tokens must be at least 1")
    if generation.concurrency < 1:
        raise ConfigError("generation.concurrency must be at least 1")

    judging_raw = _table(raw.get("judging"), "judging")
    judge_rows = judging_raw.get("judges")
    if not isinstance(judge_rows, list) or not judge_rows:
        raise ConfigError("judging.judges must contain at least one [[judging.judges]]")
    judges = []
    for index, value in enumerate(judge_rows):
        row = _table(value, f"judging.judges[{index}]")
        key = str(row.get("key") or "").strip()
        model = str(row.get("model") or "").strip()
        transport = str(row.get("transport") or "").strip()
        if not key or not model:
            raise ConfigError(f"judging.judges[{index}] requires key and model")
        if transport not in {"openrouter", "openai-batch", "anthropic-batch"}:
            raise ConfigError(
                f"judging.judges[{index}].transport is unsupported: {transport!r}"
            )
        api_model = _optional_string(row.get("api_model"))
        if transport != "openrouter" and not api_model:
            raise ConfigError(
                f"judging.judges[{index}].api_model is required for {transport}"
            )
        judges.append(
            JudgeTarget(
                key=key,
                transport=transport,
                model=model,
                provider=_optional_string(row.get("provider")),
                api_model=api_model,
                max_tokens=int(row.get("max_tokens", 2048)),
                reasoning=bool(row.get("reasoning", True)),
                reasoning_effort=_optional_string(row.get("reasoning_effort")),
                parse_retries=int(row.get("parse_retries", 1)),
            )
        )
    judge_keys = [judge.key for judge in judges]
    if len(judge_keys) != len(set(judge_keys)):
        raise ConfigError("judging judge keys must be unique")

    judging = JudgingConfig(
        output_dir=_resolve(
            config_path, judging_raw.get("output_dir"), "judging.output_dir"
        ),
        rubric=str(judging_raw.get("rubric") or "v2"),
        concurrency=int(judging_raw.get("concurrency", 8)),
        poll_interval_seconds=float(
            judging_raw.get("poll_interval_seconds", 60)
        ),
        judges=tuple(judges),
    )
    if judging.concurrency < 1:
        raise ConfigError("judging.concurrency must be at least 1")

    analysis_raw = _table(raw.get("analysis"), "analysis")
    return ReleaseConfig(
        path=config_path,
        run_id=run_id,
        benchmark=BenchmarkConfig(files=benchmark_files),
        generation=generation,
        judging=judging,
        analysis=AnalysisConfig(
            output_dir=_resolve(
                config_path, analysis_raw.get("output_dir"), "analysis.output_dir"
            )
        ),
    )
