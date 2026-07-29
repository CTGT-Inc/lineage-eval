"""Public command-line interface for generation, judging, and analysis."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from .batch_judge import (
    DEFAULT_BATCH_JUDGES,
    BatchJudgeSpec,
    prepare_batch_judge,
    run_batch_judge,
)
from .config import ConfigError, JudgeTarget, ReleaseConfig, load_config
from .datasets import load_paths
from .generation import generation_plan, run_generation
from .judge import JudgeConfig, grade_responses
from .matched_stats import (
    build_evaluation_statistics,
    read_jsonl,
    select_final_responses,
    write_statistics_artifacts,
)

DEFAULT_CONFIG = Path("configs/eval.toml")


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"release TOML file (default: {DEFAULT_CONFIG})",
    )


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _batch_for(row: dict[str, Any]) -> str:
    return str(
        row.get("batch")
        or (
            "finance"
            if row.get("stratum") == "finance_adjacent"
            else "core_political"
        )
    )


def _selected_judges(
    config: ReleaseConfig, requested: list[str] | None
) -> tuple[JudgeTarget, ...]:
    if not requested:
        return config.judging.judges
    requested_set = set(requested)
    known = {judge.key for judge in config.judging.judges}
    unknown = sorted(requested_set - known)
    if unknown:
        raise ConfigError(f"unknown judge keys: {unknown}; known: {sorted(known)}")
    return tuple(
        judge for judge in config.judging.judges if judge.key in requested_set
    )


def _batch_spec(target: JudgeTarget) -> BatchJudgeSpec:
    default_key = "gpt" if target.transport == "openai-batch" else "sonnet"
    default = DEFAULT_BATCH_JUDGES[default_key]
    provider = "openai" if target.transport == "openai-batch" else "anthropic"
    return replace(
        default,
        provider=provider,
        api_model=target.api_model or default.api_model,
        judge_model=target.model,
    )


def run_judging(
    config: ReleaseConfig,
    *,
    requested: list[str] | None = None,
    prepare_only: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run or resume the configured judge panel."""

    responses = config.generation.output
    if not responses.is_file():
        raise RuntimeError(
            f"generation file does not exist: {responses}; run `lineage-eval "
            "generate` first"
        )
    config.judging.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, Any] = {}
    for target in _selected_judges(config, requested):
        output = config.judging.output_dir / f"{target.key}.jsonl"
        judge_config = JudgeConfig(
            model=target.model,
            provider=target.provider,
            prompt_version=config.judging.rubric,
            max_tokens=target.max_tokens,
            reasoning=target.reasoning,
            reasoning_effort=target.reasoning_effort,
            parse_retries=target.parse_retries,
        )
        print(
            f"\n[{target.key}] {target.model} via {target.transport}",
            flush=True,
        )
        if target.transport == "openrouter":
            if prepare_only:
                summaries[target.key] = {
                    "prepared": False,
                    "note": "OpenRouter judging is synchronous; nothing to prepare.",
                }
                continue
            summaries[target.key] = grade_responses(
                responses,
                output,
                config=judge_config,
                limit=limit,
                concurrency=config.judging.concurrency,
            )
            continue

        spec = _batch_spec(target)
        job_dir = config.judging.output_dir / "_jobs" / target.key
        if prepare_only:
            summaries[target.key] = prepare_batch_judge(
                responses,
                output,
                job_dir,
                spec=spec,
                config=judge_config,
                limit=limit,
            )
        else:
            summaries[target.key] = run_batch_judge(
                responses,
                output,
                job_dir,
                spec=spec,
                config=judge_config,
                limit=limit,
                poll_interval_seconds=config.judging.poll_interval_seconds,
            )
    return summaries


def run_analysis(config: ReleaseConfig) -> dict[str, Any]:
    """Build the viewer-compatible descriptive statistics from released schemas."""

    if not config.generation.output.is_file():
        raise RuntimeError(f"generation file does not exist: {config.generation.output}")
    responses = [
        {**row, "batch": _batch_for(row)}
        for row in read_jsonl(config.generation.output)
    ]
    attempts: list[dict[str, Any]] = []
    successful: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    configured_models = {judge.model for judge in config.judging.judges}
    for path in sorted(config.judging.output_dir.glob("*.jsonl")):
        for row in read_jsonl(path):
            tagged = {**row, "batch": _batch_for(row)}
            if tagged.get("judge_model") not in configured_models:
                continue
            attempts.append(tagged)
            if (
                tagged.get("prompt_version") == config.judging.rubric
                and tagged.get("judge_error") is None
                and tagged.get("score") is not None
            ):
                key = (
                    tagged["batch"],
                    tagged["judge_model"],
                    tagged["model_key"],
                    tagged["prompt_id"],
                )
                successful[key] = tagged
    if not successful:
        raise RuntimeError(
            f"no successful {config.judging.rubric} judgments found under "
            f"{config.judging.output_dir}"
        )
    final_responses = select_final_responses(responses)
    observed_models = {
        str(response["model_key"]) for response in final_responses
    }
    preferred_models: list[str] = []
    if config.generation.metadata.is_file():
        metadata = json.loads(
            config.generation.metadata.read_text(encoding="utf-8")
        )
        preferred_models.extend(metadata.get("model_keys", ()))
    preferred_models.extend(config.generation.model_keys)
    model_order = tuple(
        key
        for key in dict.fromkeys(
            (*preferred_models, *sorted(observed_models))
        )
        if key in observed_models
    )
    stats = build_evaluation_statistics(
        final_responses,
        list(successful.values()),
        attempts,
        model_order=model_order,
        prompt_version=config.judging.rubric,
    )
    write_statistics_artifacts(stats, config.analysis.output_dir)
    return {
        "responses": len(stats["response_scores"]),
        "matched_gaps": len(stats["matched_gaps"]),
        "judges": stats["judges"],
        "output_dir": str(config.analysis.output_dir),
    }


def doctor(
    config: ReleaseConfig,
    *,
    static_only: bool,
    skip_api_keys: bool,
    base_url: str | None = None,
    models: list[str] | None = None,
) -> int:
    """Check the benchmark, judge keys, and user-managed inference endpoint."""

    failures: list[str] = []
    try:
        prompts = load_paths(*config.benchmark.files)
    except Exception as exc:
        failures.append(f"benchmark: {type(exc).__name__}: {exc}")
        prompts = []
    else:
        versions = sorted({row["benchmark_version"] for row in prompts})
        print(f"ok  benchmark: {len(prompts)} prompts, versions={versions}")
    plan = generation_plan(
        config,
        base_url=base_url,
        models=models,
        limit=1,
    )
    print(
        f"ok  config: {len(plan['models'])} served model(s), "
        f"{len(config.judging.judges)} judges"
    )

    if not skip_api_keys:
        required = {
            "OPENROUTER_API_KEY"
            for judge in config.judging.judges
            if judge.transport == "openrouter"
        }
        required.update(
            "OPENAI_API_KEY"
            for judge in config.judging.judges
            if judge.transport == "openai-batch"
        )
        required.update(
            "ANTHROPIC_API_KEY"
            for judge in config.judging.judges
            if judge.transport == "anthropic-batch"
        )
        for name in sorted(required):
            if os.environ.get(name):
                print(f"ok  environment: {name} is set")
            else:
                failures.append(f"environment: {name} is not set")
    if not static_only:
        inference_key = "local"
        if config.generation.api_key_env:
            inference_key = os.environ.get(
                config.generation.api_key_env,
                "",
            )
            if not inference_key:
                failures.append(
                    f"environment: {config.generation.api_key_env} is not set"
                )
        if inference_key:
            try:
                response = httpx.get(
                    f"{plan['base_url']}/models",
                    headers={"Authorization": f"Bearer {inference_key}"},
                    timeout=15.0,
                )
                response.raise_for_status()
                available = {
                    str(row["id"])
                    for row in response.json().get("data", ())
                    if row.get("id")
                }
                missing = sorted(set(plan["models"]) - available)
                if missing:
                    failures.append(
                        f"endpoint: models not served: {missing}; available: "
                        f"{sorted(available)}"
                    )
                else:
                    print(
                        f"ok  endpoint: {plan['base_url']} serves "
                        f"{plan['models']}"
                    )
            except Exception as exc:
                failures.append(
                    f"endpoint: {type(exc).__name__}: {exc}"
                )

    for failure in failures:
        print(f"FAIL  {failure}")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lineage-eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="validate and print the run plan")
    _add_config_argument(plan_parser)
    plan_parser.add_argument("--base-url")
    plan_parser.add_argument("--limit", type=int)
    plan_parser.add_argument(
        "--model",
        action="append",
        help="served model name; repeat for several (overrides config)",
    )

    doctor_parser = commands.add_parser("doctor", help="check prerequisites before spend")
    _add_config_argument(doctor_parser)
    doctor_parser.add_argument(
        "--static",
        action="store_true",
        help="validate config/data only; skip endpoint reachability",
    )
    doctor_parser.add_argument("--skip-api-keys", action="store_true")
    doctor_parser.add_argument("--base-url")
    doctor_parser.add_argument(
        "--model",
        action="append",
        help="served model name; repeat for several (overrides config)",
    )

    generate_parser = commands.add_parser("generate", help="run or resume generation")
    _add_config_argument(generate_parser)
    generate_parser.add_argument(
        "--base-url",
        help="user-managed OpenAI-compatible /v1 endpoint",
    )
    generate_parser.add_argument("--limit", type=int, help="smoke-test prompt count")
    generate_parser.add_argument(
        "--model",
        action="append",
        help="served model name; repeat for several (overrides config)",
    )
    generate_parser.add_argument("--dry-run", action="store_true")

    judge_parser = commands.add_parser("judge", help="run or resume configured judges")
    _add_config_argument(judge_parser)
    judge_parser.add_argument(
        "--judge",
        action="append",
        help="judge key to run; repeat to select several (default: all)",
    )
    judge_parser.add_argument("--prepare-only", action="store_true")
    judge_parser.add_argument("--limit", type=int)

    analyze_parser = commands.add_parser("analyze", help="build statistics artifacts")
    _add_config_argument(analyze_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "plan":
            _print_json(
                generation_plan(
                    config,
                    base_url=args.base_url,
                    models=args.model,
                    limit=args.limit,
                )
            )
            return 0
        if args.command == "doctor":
            return doctor(
                config,
                static_only=args.static,
                skip_api_keys=args.skip_api_keys,
                base_url=args.base_url,
                models=args.model,
            )
        if args.command == "generate":
            result = run_generation(
                config,
                base_url=args.base_url,
                models=args.model,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            _print_json(result)
            return 0
        if args.command == "judge":
            _print_json(
                run_judging(
                    config,
                    requested=args.judge,
                    prepare_only=args.prepare_only,
                    limit=args.limit,
                )
            )
            return 0
        if args.command == "analyze":
            _print_json(run_analysis(config))
            return 0
    except (ConfigError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unhandled command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
