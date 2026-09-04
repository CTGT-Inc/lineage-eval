# Lineage Eval

Benchmark data, a model-agnostic generation and judging harness, released study
results, and a read-only interactive viewer for the matched-v2 censorship
evaluation.

The released blog artifact contains 304 prompts, six model arms, 1,824
responses, and 7,296 classifications from four LLM judges. A supplemental Ox
Alpha comparator release adds 152 core-political generations and 604
`cards_v1` classifications; its endpoint provenance was undisclosed and it
carries no lineage claim. The three trained
adapters used in the study are not distributed. Their completed outputs can be
audited in the viewer, but the adapter generations and headline comparison
cannot be regenerated from this public repository.

## Browse the released results

The viewer requires Node.js 22.13 or newer. It does not need Python, a GPU,
model access, or API keys:

```bash
cd viewer
npm ci
npm run dev
```

Open <http://localhost:3000>. The canonical payload is
`data/results/blog-v1/matched-v2-full-data.json`; the viewer copies it into its
ignored `public/` directory before building. See
[viewer/README.md](viewer/README.md) for production and local annotation
details.

## Evaluate any model you can serve

The harness does not download, load, or manage model weights. You run vLLM—or
another server implementing the OpenAI chat-completions API—and tell the
harness its `/v1` URL and served model name. The checkpoint can be a released
model, a local directory, or a model you trained yourself.

Install vLLM separately in the GPU environment appropriate for your hardware.
For example, on the GPU machine:

```bash
vllm serve /path/to/your-model \
  --served-model-name my-model \
  --host 127.0.0.1 \
  --port 8000
```

Use whatever additional vLLM flags your model needs, including its reasoning
parser. Keep the endpoint private or add authentication if it is reachable
over a network.

Install the lightweight evaluation client:

```bash
uv sync
cp .env.example .env
```

Add the judge API keys to `.env`, then verify that the endpoint exposes the
expected served name:

```bash
uv run lineage-eval doctor \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-model
```

Start with two prompts:

```bash
uv run lineage-eval generate \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-model \
  --limit 2
```

Inspect `runs/eval/responses.jsonl`, then resume the full 304-prompt run:

```bash
uv run lineage-eval generate \
  --config configs/eval.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-model
```

Generation is append-only and resumable. Successful model/prompt pairs are
skipped; failed attempts remain in the log and are retried. To evaluate several
models exposed by the same endpoint, repeat `--model`:

```bash
uv run lineage-eval generate \
  --model base-model \
  --model my-finetune
```

The URL and default placeholder model can also be edited in
`configs/eval.toml`. If the inference endpoint requires a key, set
`generation.api_key_env` in a copy of the config and add that variable to
`.env`.

## Judge and analyze

The configured panel uses two synchronous OpenRouter judges plus the native
OpenAI and Anthropic batch APIs:

```text
OPENROUTER_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

Run the panel and analysis:

```bash
uv run lineage-eval judge --config configs/eval.toml
uv run lineage-eval analyze --config configs/eval.toml
```

Native batches can be prepared for inspection before paid submission:

```bash
uv run lineage-eval judge --config configs/eval.toml \
  --judge gpt --judge sonnet --prepare-only
```

Responses, provider jobs, judgments, and statistics stay under `runs/eval/`.
Credentials are never written to run artifacts. See
[docs/running.md](docs/running.md) for endpoint requirements, reasoning-output
handling, and recovery behavior.

## Repository layout

```text
configs/eval.toml       Benchmark, endpoint defaults, decoding, and judge panel
src/censorship/         Endpoint generation, judging, and matched analysis
data/benchmark/         Immutable matched-v2 prompt sets
data/results/blog-v1/   Canonical six-arm blog/viewer artifact
data/results/ox-alpha-v1/  Supplemental Ox Alpha comparator observations
viewer/                 Read-only released-results browser
docs/                   Methodology, runtime, and release notes
tests/                  Platform-independent Python tests
runs/                   Local resumable work products; ignored
```

## Validation

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests

cd viewer
npm ci
npm test
npm run lint
```

The repository tests the endpoint client with an in-process mock. A live vLLM
server and model-specific serving flags must be smoke-tested in the user’s GPU
environment.

Read [docs/methodology.md](docs/methodology.md) and
[data/README.md](data/README.md) before interpreting the statistics. Release
validation and the remaining environment-specific checks are tracked in
[docs/release-checklist.md](docs/release-checklist.md).

## License and citation

Code, documentation, and viewer assets are licensed under Apache License 2.0;
see [LICENSE](LICENSE). Project-authored data—including the 304 prompts,
controls, reference fact cards, rubric, and scoring definitions—is licensed
under CC BY 4.0; see [data/LICENSE](data/LICENSE). DECCP-derived portions retain
their upstream Apache License 2.0 and are documented, with modifications, in
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES/README.md).

Model-generated responses are provided for research reproducibility and may
remain subject to the generating providers' terms. No model weights are
distributed in this repository. Any separately published project weights use
Apache License 2.0 and their Hugging Face model card must retain attribution to
OpenAI's Apache-2.0-licensed gpt-oss base with `license: apache-2.0` metadata.
Citation metadata is provided in [CITATION.cff](CITATION.cff).
