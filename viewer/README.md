# Matched-v2 response viewer

Read-only research browser for the released matched-v2 run. It compares six
model arms across all 304 prompts, exposes answer and captured-reasoning text,
shows every v2 judge classification, and presents pooled and stratum-specific
matched statistics.

## Run the released viewer

Requirements:

- Node.js 22.13 or newer.
- npm.

No Python environment, GPU, model weights, or API keys are required.
The viewer includes responses from three trained adapters that are not
distributed; browsing the released artifact does not require those weights.

```bash
cd viewer
npm ci
npm run dev
```

Open <http://localhost:3000>.

Before `dev` or `build`, the package copies the authoritative payload from
`../data/results/blog-v1/matched-v2-full-data.json` into the ignored
`public/` directory. This avoids a second committed 39 MB file and works on
platforms where Git symlink checkout is unreliable.

Override the source only with another compatible payload:

```bash
LINEAGE_EVAL_VIEWER_DATA=/absolute/path/to/data.json npm run dev
```

## Production build

```bash
npm ci
npm test
npm run lint
npm run build
```

`npm test` performs a production build and verifies the application shell,
304-prompt / six-arm data completeness, judge coverage, quality exclusions,
statistics scopes, and viewer controls. The production viewer is read-only and
does not need a database or writable filesystem.

## Optional local annotation mode

Human-score editing is disabled by default and should stay disabled in public
deployments. Maintainers can enable the local-only write API:

```bash
NEXT_PUBLIC_ENABLE_ANNOTATION=true npm run dev
```

Scores are written atomically to
`../data/annotations/blog-v1/human-labels.json`. This mode is for local
annotation work only. A deployed worker cannot persist changes back to a Git
checkout, and the feature is not enabled in a normal production build.

## Rebuild the payload from a complete source run

Only maintainers who possess a complete compatible six-arm run should do this.
A custom run produced with `configs/eval.toml` is not automatically a
replacement for the released blog artifact. The builder’s default maintainer
inputs are:

```text
../runs/blog-v1/responses.jsonl
../runs/blog-v1/run.json
../runs/blog-v1/judgments/*.jsonl
../runs/blog-v1/analysis/summary.json
```

With a complete source run in place:

```bash
cd viewer
npm run data
npm test
```

`npm run data` selects the last successful generation per model/prompt, joins
the configured four-judge panel, applies the analysis quality exclusions, writes
the canonical release payload under `data/results/blog-v1/`, and synchronizes
the generated public copy.

To build from a differently located compatible run:

```bash
LINEAGE_EVAL_RUN_DIR=/absolute/path/to/run npm run data
```

Review the generated counts and version-control diff before publication.
