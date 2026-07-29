# Running the evaluation on your model

## Public scope

The checked-in viewer is the immutable six-arm blog artifact. Three adapter
weights from that study are not distributed, so the original headline
comparison cannot be regenerated from this repository.

The public harness is intentionally model-agnostic. It sends the 304 matched-v2
prompts to model names exposed by a user-managed OpenAI-compatible endpoint,
records answers and reasoning separately, runs the configured judge panel, and
builds matched statistics.

## 1. Serve the model

Install vLLM independently using the instructions appropriate for the target
GPU, driver, CUDA version, model architecture, and quantization. The evaluation
package does not depend on vLLM and never starts or stops the server.

The model argument can be:

- a Hugging Face model ID;
- a local checkpoint directory;
- a merged finetune; or
- any other format supported by the installed vLLM version.

Example:

```bash
vllm serve /models/my-finetune \
  --served-model-name my-finetune \
  --host 127.0.0.1 \
  --port 8000
```

Add model-specific flags as necessary. For a reasoning model, configure vLLM’s
reasoning parser so answer text and reasoning are exposed separately. The
harness reads `message.reasoning` and falls back to
`message.reasoning_content`; it does not parse raw proprietary reasoning
formats itself.

If the evaluator runs on another machine, bind vLLM to an appropriate interface
and use a private network, firewall, or authenticated proxy. Do not expose an
unauthenticated generation endpoint to the public internet.

Verify the OpenAI-compatible surface directly:

```bash
curl http://127.0.0.1:8000/v1/models
```

The response must contain the exact `--served-model-name` value.

## 2. Install and preflight the evaluator

```bash
git clone <repository-url>
cd lineage-eval
uv sync
cp .env.example .env
```

Add the configured judge keys to `.env`. Then check the benchmark, keys,
endpoint, and served name:

```bash
uv run lineage-eval doctor \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-finetune
```

`doctor` calls `GET /v1/models` and fails when the requested name is absent.
Use `--static --skip-api-keys` only for repository/configuration validation
without a live server.

For an authenticated inference endpoint, copy `configs/eval.toml`, set:

```toml
api_key_env = "INFERENCE_API_KEY"
```

and add `INFERENCE_API_KEY` to `.env`.

## 3. Smoke-test generation

Print the resolved plan without contacting the server:

```bash
uv run lineage-eval plan \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-finetune \
  --limit 2
```

Generate two prompts:

```bash
uv run lineage-eval generate \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-finetune \
  --limit 2
```

Inspect:

- `runs/eval/responses.jsonl`; and
- `runs/eval/run.json`.

Confirm that both rows have:

- `error: null`;
- substantive `content`;
- a plausible `finish_reason`;
- token `usage`; and
- separate `reasoning` when the served model supports it.

Also inspect the recorded decoding parameters. Model servers vary in their
support for `seed`, `top_p`, and reasoning metadata; a successful HTTP response
does not by itself establish equivalent decoding behavior.

## 4. Complete or compare runs

Resume all 304 prompts:

```bash
uv run lineage-eval generate \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-finetune
```

To compare multiple models served at the same endpoint:

```bash
uv run lineage-eval generate \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model base-model \
  --model my-finetune
```

If the models cannot coexist on one server, run the command once per model
while reusing `runs/eval/`. Successful model/prompt pairs are skipped, so the
second model can be served later at the same URL.

Use a new config with a new `run_id` and output directory when decoding,
benchmark membership, or other run semantics change.

## 5. Judge

The default panel requires:

```text
OPENROUTER_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

Prepare native batch payloads without submitting them:

```bash
uv run lineage-eval judge --config configs/eval.toml \
  --judge gpt --judge sonnet --prepare-only
```

Review `runs/eval/judgments/_jobs/`, then run the panel:

```bash
uv run lineage-eval judge --config configs/eval.toml
```

Judging is resumable by judge/model/prompt/rubric identity. Do not modify
`responses.jsonl` after preparing a native batch; the stored job checksum will
reject a different source file.

## 6. Analyze

```bash
uv run lineage-eval analyze --config configs/eval.toml
```

Statistics are written under `runs/eval/analysis/`. Model order is taken from
the actual generation metadata, including names supplied through repeated
`--model` arguments.

The checked-in viewer remains the released blog artifact and is not
automatically overwritten by a custom evaluation run.

## Recovery rules

- Re-run `generate`; successful model/prompt pairs are skipped.
- Error rows remain as attempts and are retried until a success exists.
- Re-run `judge`; successful judge/model/prompt/rubric tuples are skipped.
- Never hand-edit append-only response or judgment logs.
- Preserve `run.json` with the JSONL files; it records endpoint, model names,
  decoding, timestamps, and runtime details.
