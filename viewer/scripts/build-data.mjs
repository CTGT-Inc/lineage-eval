import { createReadStream, existsSync } from "node:fs";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runDirectory = resolve(
  root,
  process.env.LINEAGE_EVAL_RUN_DIR ?? "../runs/blog-v1",
);
const judgmentsDirectory = resolve(runDirectory, "judgments");
const destination = resolve(
  root,
  process.env.LINEAGE_EVAL_VIEWER_DATA ??
    "../data/results/blog-v1/matched-v2-full-data.json",
);
const statisticsPath = resolve(runDirectory, "analysis/summary.json");
const benchmarkDirectory = resolve(root, "../data/benchmark");
const batches = [
  {
    id: "finance",
    promptFile: "finance_prompts.jsonl",
  },
  {
    id: "core_political",
    promptFile: "core_political_matched_prompts.jsonl",
  },
];
const modelOrder = [
  "gpt_oss_120b",
  "self_distilled",
  "v4_flash_distilled",
  "v4_flash",
  "gpt_oss_20b",
  "expert_20b_self_sturev",
];
const judgeOrder = [
  "x-ai/grok-4.20",
  "google/gemini-3.5-flash",
  "openai/gpt-5-mini",
  "anthropic/claude-sonnet-4.6",
];
const promptVersion = "v2";

const responseRecords = new Map();
const grades = new Map();
const gradeRecords = [];
const byPrompt = new Map();
const batchByPrompt = new Map();

async function readJsonl(path) {
  const rows = [];
  const input = createInterface({
    input: createReadStream(path),
    crlfDelay: Infinity,
  });
  for await (const line of input) {
    if (line.trim()) rows.push(JSON.parse(line));
  }
  return rows;
}

for (const batch of batches) {
  const promptPath = resolve(benchmarkDirectory, batch.promptFile);
  if (!existsSync(promptPath)) {
    throw new Error(`Missing benchmark prompts at ${promptPath}`);
  }
  for (const prompt of await readJsonl(promptPath)) {
    const promptKey = `${batch.id}\u0000${prompt.prompt_id}`;
    byPrompt.set(promptKey, {
      ...prompt,
      batch: batch.id,
      responses: {},
    });
    batchByPrompt.set(prompt.prompt_id, batch.id);
  }
}

const responsesPath = resolve(runDirectory, "responses.jsonl");
const metadataPath = resolve(runDirectory, "run.json");
if (!existsSync(responsesPath) || !existsSync(metadataPath)) {
  throw new Error(`Missing release run outputs under ${runDirectory}`);
}
for (const response of await readJsonl(responsesPath)) {
  const batch = batchByPrompt.get(response.prompt_id);
  if (!batch) {
    throw new Error(`Unknown response prompt: ${response.prompt_id}`);
  }
  const key = `${batch}\u0000${response.model_key}\u0000${response.prompt_id}`;
  const current = responseRecords.get(key);
  // Generation is append-only: retain failed attempts for audit in the raw JSONL,
  // but publish exactly one row per model/prompt and always prefer a successful retry.
  if (!current || current.error || !response.error) {
    responseRecords.set(key, { ...response, batch });
  }
}
const records = [...responseRecords.values()];

const runMetadata = JSON.parse(await readFile(metadataPath, "utf8"));
const gradePaths = (await readdir(judgmentsDirectory))
  .filter((name) => /^[^._].*\.jsonl$/.test(name))
  .sort()
  .map((name) => resolve(judgmentsDirectory, name));

for (const gradesPath of gradePaths) {
  for (const gradeRecord of await readJsonl(gradesPath)) {
    const batch = batchByPrompt.get(gradeRecord.prompt_id);
    if (!batch) {
      throw new Error(`Unknown graded prompt: ${gradeRecord.prompt_id}`);
    }
    const grade = { ...gradeRecord, batch };
    gradeRecords.push(grade);
    if (
      judgeOrder.includes(grade.judge_model) &&
      grade.prompt_version === promptVersion &&
      grade.judge_error === null &&
      grade.score !== null
    ) {
      grades.set(
        `${grade.batch}\u0000${grade.judge_model}\u0000${grade.prompt_version}\u0000${grade.model_key}\u0000${grade.prompt_id}`,
        grade,
      );
    }
  }
}

for (const record of records) {
  const promptKey = `${record.batch}\u0000${record.prompt_id}`;
  const current = byPrompt.get(promptKey);
  if (!current) {
    throw new Error(
      `Response ${record.model_key}/${record.prompt_id} has no benchmark prompt`,
    );
  }
  if (!modelOrder.includes(record.model_key)) {
    throw new Error(`Unknown response model: ${record.model_key}`);
  }

  current.responses[record.model_key] = {
    model_key: record.model_key,
    content: record.content,
    reasoning: record.reasoning,
    finish_reason: record.finish_reason,
    completion_tokens: record.usage?.completion_tokens ?? null,
    prompt_tokens: record.usage?.prompt_tokens ?? null,
    total_tokens: record.usage?.total_tokens ?? null,
    reasoning_tokens: record.reasoning_tokens,
    latency_s: record.latency_s,
    error: record.error,
    requested_at_utc: record.requested_at_utc,
    effective_label: null,
    response_quality_label: "VALID",
    degeneracy_reason: null,
    degeneracy_metrics: null,
    judges: [],
  };
  for (const judgeModel of judgeOrder) {
    const grade = grades.get(
      `${record.batch}\u0000${judgeModel}\u0000${promptVersion}\u0000${record.model_key}\u0000${record.prompt_id}`,
    );
    if (grade) {
      current.responses[record.model_key].judges.push({
        label: grade.label,
        score: grade.score,
        rationale: grade.rationale,
        covers: grade.covers ?? [],
        omits: grade.omits ?? [],
        failure_modes: grade.failure_modes ?? [],
        judge_model: grade.judge_model,
        prompt_version: grade.prompt_version,
        judge_latency_s: grade.judge_latency_s,
        judge_usage: grade.judge_usage,
        graded_at_utc: grade.graded_at_utc,
      });
    }
  }
  byPrompt.set(promptKey, current);
}

const prompts = [...byPrompt.values()].sort((a, b) => {
  const concept = a.concept_id.localeCompare(b.concept_id);
  if (concept !== 0) return concept;
  const frame = a.frame.localeCompare(b.frame);
  if (frame !== 0) return frame;
  return a.condition === b.condition ? 0 : a.condition === "sensitive" ? -1 : 1;
});

if (!existsSync(statisticsPath)) {
  throw new Error(
    `Missing ${statisticsPath}; run lineage-eval analyze first`,
  );
}
const fullStatistics = JSON.parse(await readFile(statisticsPath, "utf8"));
const invalidResponses = new Map(
  fullStatistics.degeneracy.responses.map((row) => [
    `${row.batch}\u0000${row.model_key}\u0000${row.prompt_id}`,
    row,
  ]),
);
for (const prompt of prompts) {
  for (const [modelKey, response] of Object.entries(prompt.responses)) {
    const invalid = invalidResponses.get(
      `${prompt.batch}\u0000${modelKey}\u0000${prompt.prompt_id}`,
    );
    if (!invalid) continue;
    response.effective_label = invalid.response_quality_label;
    response.response_quality_label = invalid.response_quality_label;
    response.degeneracy_reason = invalid.degeneracy_reason;
    response.degeneracy_metrics = {
      tokens: invalid.degeneracy_tokens,
      repeat_mass: invalid.degeneracy_repeat_mass,
      max_ngram_repeats: invalid.degeneracy_max_ngram_repeats,
      top_ngram: invalid.degeneracy_top_ngram,
      max_character_run: invalid.degeneracy_max_character_run,
      repeated_character: invalid.degeneracy_repeated_character,
    };
    response.judges = response.judges.map((judge) => ({
      ...judge,
      excluded_from_statistics: true,
    }));
  }
}
const statistics = { ...fullStatistics };
delete statistics.response_scores;
delete statistics.matched_gaps;

const payload = {
  generated_at_utc: new Date().toISOString(),
  models: modelOrder,
  prompts,
  judges: judgeOrder
    .map((model) => {
      const modelGrades = [...grades.values()].filter(
        (grade) => grade.judge_model === model,
      );
      return {
        model,
        prompt_version: modelGrades[0]?.prompt_version ?? promptVersion,
        successful_grades: modelGrades.length,
        failed_attempts: gradeRecords.filter(
          (grade) =>
            grade.judge_model === model &&
            grade.judge_error !== null &&
            !grades.has(
              `${grade.batch}\u0000${grade.judge_model}\u0000${grade.prompt_version}\u0000${grade.model_key}\u0000${grade.prompt_id}`,
            ),
        ).length,
      };
    })
    .filter((judge) => judge.successful_grades > 0 || judge.failed_attempts > 0),
  run: {
    experiment: runMetadata.run_id ?? runMetadata.experiment,
    backend: runMetadata.backend,
    finished_at_utc: runMetadata.finished_at_utc,
    decoding: runMetadata.decoding,
    total_usd_gpu_compute: runMetadata.total_usd_gpu_compute ?? 0,
    data_quality_notes: runMetadata.data_quality_notes ?? [],
  },
  runs: batches.map((batch) => ({ batch: batch.id, ...runMetadata })),
  statistics,
};

await mkdir(dirname(destination), { recursive: true });
await writeFile(destination, `${JSON.stringify(payload)}\n`);

console.log(
  `Wrote ${prompts.length} prompts (${prompts.filter((prompt) => Object.keys(prompt.responses).length === 0).length} awaiting outputs), ${records.length} responses, and ${grades.size} judge classifications to ${destination}`,
);
