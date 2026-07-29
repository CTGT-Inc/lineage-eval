#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  copyFile,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = resolve(
  process.argv[2] ??
    join(
      repositoryRoot,
      "data/results/blog-v1/matched-v2-full-data.json",
    ),
);
const annotationsPath = resolve(
  process.argv[3] ??
    join(repositoryRoot, "data/annotations/blog-v1/human-labels.json"),
);
const destination = resolve(
  process.argv[4] ??
    join(repositoryRoot, "release/huggingface/blog-v1"),
);
const datasetCardPath = join(
  repositoryRoot,
  "release/huggingface/DATASET_CARD.md",
);
const viewerGuidePath = join(repositoryRoot, "release/huggingface/VIEWER.md");
const dataLicensePath = join(repositoryRoot, "data/LICENSE");
const codeLicensePath = join(repositoryRoot, "LICENSE");
const noticePath = join(repositoryRoot, "NOTICE");
const thirdPartyNoticesPath = join(repositoryRoot, "THIRD_PARTY_NOTICES");

await buildRelease();

async function buildRelease() {
assertSafeDestination(destination);
const destinationParent = dirname(destination);
await mkdir(destinationParent, { recursive: true });
const outputRoot = await mkdtemp(
  join(destinationParent, `.${basename(destination)}-staging-`),
);

try {
await copyFile(datasetCardPath, join(outputRoot, "README.md"));
await copyFile(viewerGuidePath, join(outputRoot, "VIEWER.md"));
await copyFile(dataLicensePath, join(outputRoot, "LICENSE"));
await copyFile(codeLicensePath, join(outputRoot, "CODE_LICENSE"));
await copyFile(noticePath, join(outputRoot, "NOTICE"));
await cp(thirdPartyNoticesPath, join(outputRoot, "THIRD_PARTY_NOTICES"), {
  recursive: true,
});

const sourceBytes = await readFile(sourcePath);
const source = JSON.parse(sourceBytes.toString("utf8"));
const annotations = JSON.parse(await readFile(annotationsPath, "utf8"));
const sanitizedStatistics = structuredClone(source.statistics);
const topicAnalysisCost =
  sanitizedStatistics?.degeneracy?.topic_conditional_test?.cost;
if (topicAnalysisCost) {
  sanitizedStatistics.degeneracy.topic_conditional_test.cost = {
    incremental_usd_for_topic_analysis:
      topicAnalysisCost.incremental_usd_for_topic_analysis,
    source_job_usd_gpu_compute: topicAnalysisCost.source_job_usd_gpu_compute,
    requested_arms_generation_usd_gpu_compute:
      topicAnalysisCost.requested_arms_generation_usd_gpu_compute,
    shared_startup_usd_gpu_compute:
      topicAnalysisCost.shared_startup_usd_gpu_compute,
    note:
      "Historical GPU-compute estimates. Provider-specific infrastructure fields, " +
      "hardware allocation details, and unreleased co-served arm names were removed.",
  };
}

const releaseId = source.run?.experiment ?? "blog-v1";
const promptById = new Map(
  source.prompts.map((prompt) => [prompt.prompt_id, prompt]),
);
const benchmarkVersions = [
  ...new Set(source.prompts.map((prompt) => prompt.benchmark_version)),
];

if (benchmarkVersions.length !== 1) {
  throw new Error(
    `Expected one benchmark version, found: ${benchmarkVersions.join(", ")}`,
  );
}

const benchmarkVersion = benchmarkVersions[0];
const benchmarkRows = source.prompts.map(({ responses: _responses, ...prompt }) => ({
  ...prompt,
  release_id: releaseId,
  reference_card_status: "model_drafted_unverified",
  provenance_status:
    prompt.stratum === "finance_adjacent"
      ? "authored_for_matched_v2"
      : "upstream_inventory_influence_not_fully_mapped",
}));

const responseRows = [];
const judgmentRows = [];

for (const prompt of source.prompts) {
  for (const modelKey of source.models) {
    const response = prompt.responses[modelKey];
    if (!response) {
      throw new Error(
        `Missing response for ${prompt.prompt_id} and ${modelKey}`,
      );
    }

    const includedInStatistics = response.response_quality_label === "VALID";
    const fidelityScores = response.judges.map((judgment) => judgment.score);
    const meanFidelityScore = includedInStatistics
      ? fidelityScores.reduce((sum, score) => sum + score, 0) /
        fidelityScores.length
      : null;
    const responseId = `${prompt.prompt_id}::${modelKey}`;
    const degeneracy = response.degeneracy_metrics ?? {};

    responseRows.push({
      release_id: releaseId,
      experiment: source.run.experiment,
      benchmark_version: prompt.benchmark_version,
      response_id: responseId,
      prompt_id: prompt.prompt_id,
      model_key: modelKey,
      content: response.content,
      reasoning: response.reasoning,
      finish_reason: response.finish_reason,
      response_quality_label: response.response_quality_label,
      included_in_statistics: includedInStatistics,
      effective_label: response.effective_label,
      mean_fidelity_score: meanFidelityScore,
      censorship_score:
        meanFidelityScore === null ? null : 100 - meanFidelityScore,
      degeneracy_reason: response.degeneracy_reason,
      degeneracy_tokens: degeneracy.tokens ?? null,
      degeneracy_repeat_mass: degeneracy.repeat_mass ?? null,
      degeneracy_max_ngram_repeats:
        degeneracy.max_ngram_repeats ?? null,
      degeneracy_top_ngram: degeneracy.top_ngram ?? null,
      degeneracy_max_character_run: degeneracy.max_character_run ?? null,
      degeneracy_repeated_character: degeneracy.repeated_character ?? null,
      completion_tokens: response.completion_tokens,
      prompt_tokens: response.prompt_tokens,
      total_tokens: response.total_tokens,
      reasoning_tokens: response.reasoning_tokens,
      latency_s: response.latency_s,
      error: response.error,
      requested_at_utc: response.requested_at_utc,
    });

    for (const judgment of response.judges) {
      const judgmentId = [
        prompt.prompt_id,
        modelKey,
        judgment.judge_model,
        judgment.prompt_version,
      ].join("::");

      judgmentRows.push({
        release_id: releaseId,
        experiment: source.run.experiment,
        benchmark_version: prompt.benchmark_version,
        judgment_id: judgmentId,
        response_id: responseId,
        prompt_id: prompt.prompt_id,
        model_key: modelKey,
        judge_model: judgment.judge_model,
        prompt_version: judgment.prompt_version,
        label: judgment.label,
        fidelity_score: judgment.score,
        censorship_score: includedInStatistics
          ? 100 - judgment.score
          : null,
        response_quality_label: response.response_quality_label,
        included_in_statistics: includedInStatistics,
        rationale: judgment.rationale,
        covers: judgment.covers,
        omits: judgment.omits,
        failure_modes: judgment.failure_modes,
        judge_latency_s: judgment.judge_latency_s,
        judge_usage_json:
          judgment.judge_usage === null
            ? null
            : JSON.stringify(judgment.judge_usage),
        graded_at_utc: judgment.graded_at_utc,
      });
    }
  }
}

const humanAnnotationRows = annotations.labels.map((label) => {
  const prompt = promptById.get(label.prompt_id);
  if (!prompt) {
    throw new Error(`Annotation references unknown prompt ${label.prompt_id}`);
  }

  return {
    release_id: releaseId,
    experiment: annotations.experiment,
    benchmark_version: prompt.benchmark_version,
    annotation_id: `${label.prompt_id}::${label.model_key}`,
    annotation_scope: "partial_pilot",
    annotation_schema_version: annotations.schema_version,
    prompt_id: label.prompt_id,
    model_key: label.model_key,
    batch: label.batch,
    stratum: prompt.stratum,
    condition: prompt.condition,
    score: label.score,
  };
});

const invalidResponses = responseRows.filter(
  (row) => !row.included_in_statistics,
);
const completeMatchedGaps =
  sanitizedStatistics?.coverage?.response_scores === undefined
    ? null
    : sanitizedStatistics.model_summaries.reduce(
        (total, model) => total + (model.censorship_gap?.n ?? 0),
        0,
      );

const runMetadata = {
  schema_version: 1,
  release_id: releaseId,
  experiment: source.run.experiment,
  benchmark_version: benchmarkVersion,
  generated_at_utc: source.generated_at_utc,
  finished_at_utc: source.run.finished_at_utc,
  generation: {
    backend: "vllm",
    decoding: source.run.decoding,
    total_usd_gpu_compute: source.run.total_usd_gpu_compute,
  },
  counts: {
    prompts: benchmarkRows.length,
    concepts: new Set(benchmarkRows.map((row) => row.concept_id)).size,
    models: source.models.length,
    responses: responseRows.length,
    valid_responses: responseRows.length - invalidResponses.length,
    invalid_responses: invalidResponses.length,
    judges: source.judges.length,
    judgments: judgmentRows.length,
    judgments_included_in_statistics: judgmentRows.filter(
      (row) => row.included_in_statistics,
    ).length,
    judgments_excluded_from_statistics: judgmentRows.filter(
      (row) => !row.included_in_statistics,
    ).length,
    human_annotations: humanAnnotationRows.length,
    human_annotated_prompts: new Set(
      humanAnnotationRows.map((row) => row.prompt_id),
    ).size,
    complete_matched_gaps: completeMatchedGaps,
  },
  model_keys: source.models,
  judge_models: source.judges.map((judge) => judge.model),
  quality: {
    invalid_label: "INVALID_DEGENERATE",
    invalid_reasons: Object.fromEntries(
      [...new Set(invalidResponses.map((row) => row.degeneracy_reason))].map(
        (reason) => [
          reason,
          invalidResponses.filter((row) => row.degeneracy_reason === reason)
            .length,
        ],
      ),
    ),
    invalid_responses_excluded_from_scores: true,
  },
  release_notes: [
    "Reference cards are model-drafted research proxies and have not been independently fact-checked.",
    "The human annotations are a partial pilot, not benchmark-wide ground truth.",
    "Model answers, reasoning traces, citations, and judge rationales are unverified generated text.",
    "Infrastructure paths, private adapter identifiers, unreleased arm names, and attempt-level resume metadata were removed.",
  ],
  source_artifact: {
    filename: sourcePath.split("/").at(-1),
    sha256: createHash("sha256").update(sourceBytes).digest("hex"),
  },
};

const adapterModelKeys = new Set([
  "self_distilled",
  "v4_flash_distilled",
  "expert_20b_self_sturev",
]);
const models = source.models.map((modelKey) => ({
  release_id: releaseId,
  model_key: modelKey,
  result_type: "observed_generation",
  weights_included: false,
  is_adapter_arm: adapterModelKeys.has(modelKey),
  adapter_weights_available:
    adapterModelKeys.has(modelKey) ? false : null,
  reproducibility_note: adapterModelKeys.has(modelKey)
    ? "Adapter weights are not distributed; this row is an auditable observation only."
    : "Weights are not bundled with this dataset; reproduce with a separately obtained compatible model.",
}));

const judges = source.judges.map((judge) => ({
  release_id: releaseId,
  judge_model: judge.model,
  prompt_version: judge.prompt_version,
  successful_grades: judge.successful_grades,
  failed_attempts: judge.failed_attempts,
}));

const sanitizedRun = {
  experiment: source.run.experiment,
  backend: "vllm",
  finished_at_utc: source.run.finished_at_utc,
  decoding: source.run.decoding,
  total_usd_gpu_compute: source.run.total_usd_gpu_compute,
  data_quality_notes: [
    "Infrastructure-specific serving metadata was removed for publication.",
    "The GPU cost is the combined experiment total, not a per-batch allocation.",
  ],
};
const viewerPayload = {
  generated_at_utc: source.generated_at_utc,
  models: source.models,
  prompts: source.prompts,
  judges: source.judges,
  run: sanitizedRun,
  runs: [...new Set(source.prompts.map((prompt) => prompt.batch))].map(
    (batch) => ({
      batch,
      ...sanitizedRun,
    }),
  ),
  statistics: sanitizedStatistics,
};

await writeJsonl(
  join(outputRoot, "data/benchmark/evaluation.jsonl"),
  benchmarkRows,
);
await writeJsonl(
  join(outputRoot, "data/responses/evaluation.jsonl"),
  responseRows,
);
await writeJsonl(
  join(outputRoot, "data/judgments/evaluation.jsonl"),
  judgmentRows,
);
await writeJsonl(
  join(outputRoot, "data/human_annotations/evaluation.jsonl"),
  humanAnnotationRows,
);
await writeJson(join(outputRoot, "metadata/run.json"), runMetadata);
await writeJsonl(join(outputRoot, "metadata/models.jsonl"), models);
await writeJsonl(join(outputRoot, "metadata/judges.jsonl"), judges);
await writeJson(
  join(outputRoot, "metadata/statistics.json"),
  sanitizedStatistics,
);
await writeJson(
  join(outputRoot, "viewer/viewer_payload.json"),
  viewerPayload,
  false,
);

const generatedFiles = await listFiles(outputRoot);
const manifestEntries = [];
for (const filePath of generatedFiles) {
  const relativePath = relative(outputRoot, filePath);
  if (relativePath === "MANIFEST.json") {
    continue;
  }
  const bytes = await readFile(filePath);
  manifestEntries.push({
    path: relativePath,
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  });
}

await writeJson(join(outputRoot, "MANIFEST.json"), {
  schema_version: 1,
  release_id: releaseId,
  source_generated_at_utc: source.generated_at_utc,
  row_counts: {
    benchmark: benchmarkRows.length,
    responses: responseRows.length,
    judgments: judgmentRows.length,
    human_annotations: humanAnnotationRows.length,
    models: models.length,
    judges: judges.length,
  },
  files: manifestEntries.sort((a, b) => a.path.localeCompare(b.path)),
});

await rm(destination, { recursive: true, force: true });
await rename(outputRoot, destination);

console.log(
  [
    `Wrote Hugging Face release to ${destination}`,
    `${benchmarkRows.length} benchmark rows`,
    `${responseRows.length} response rows`,
    `${judgmentRows.length} judgment rows`,
    `${humanAnnotationRows.length} human annotation rows`,
  ].join("\n"),
);
} catch (error) {
  await rm(outputRoot, { recursive: true, force: true });
  throw error;
}
}

function assertSafeDestination(path) {
  if (
    path === dirname(path) ||
    path === repositoryRoot ||
    repositoryRoot.startsWith(`${path}${sep}`)
  ) {
    throw new Error(`Refusing unsafe release destination: ${path}`);
  }
}

async function writeJsonl(path, rows) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${rows.map((row) => JSON.stringify(row)).join("\n")}\n`);
}

async function writeJson(path, value, pretty = true) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(
    path,
    `${JSON.stringify(value, null, pretty ? 2 : undefined)}\n`,
  );
}

async function listFiles(directory) {
  const entries = await readdir(directory);
  const paths = [];
  for (const entry of entries) {
    const path = join(directory, entry);
    if ((await stat(path)).isDirectory()) {
      paths.push(...(await listFiles(path)));
    } else {
      paths.push(path);
    }
  }
  return paths;
}
