#!/usr/bin/env node

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const experimentDirectory = resolve(process.argv[2] ?? "");
const destination = resolve(
  process.argv[3] ?? join(repositoryRoot, "data/results/ox-alpha-v1"),
);
const sourceCommit =
  process.argv[4] ?? "0d61229f760521ff169f76740f154471fcda8ea7";

if (!process.argv[2]) {
  throw new Error(
    "Usage: node tools/import_ox_alpha_release.mjs <experiment-026-dir> [destination] [source-commit]",
  );
}

const judgeFiles = [
  "graded_openai_gpt_5_mini.jsonl",
  "graded_x_ai_grok_4_20.jsonl",
  "graded_google_gemini_3_5_flash.jsonl",
  "graded_anthropic_claude_sonnet_4_6.jsonl",
];
const generationPath = join(
  experimentDirectory,
  "data/ox_alpha_responses.jsonl",
);
const summaryPath = join(experimentDirectory, "analysis/summary.json");
const cardsV1Path = join(
  experimentDirectory,
  "analysis/matched_gaps_cards_v1.csv",
);
const cardsV2Path = join(
  experimentDirectory,
  "analysis/matched_gaps_cards_v2.csv",
);

const [responses, summary, cardsV1, cardsV2] = await Promise.all([
  readJsonl(generationPath),
  readJson(summaryPath),
  readFile(cardsV1Path, "utf8"),
  readFile(cardsV2Path, "utf8"),
]);

const oxResponses = responses.filter((row) => row.model_key === "ox_alpha");
const responseByPrompt = new Map(
  oxResponses.map((row) => [row.prompt_id, { ...row, judges: [] }]),
);
if (responseByPrompt.size !== 152 || oxResponses.length !== 152) {
  throw new Error(`Expected 152 unique Ox Alpha generations, found ${responseByPrompt.size}`);
}

const judgmentRows = [];
for (const filename of judgeFiles) {
  const rows = await readJsonl(
    join(experimentDirectory, "data/cards_v1", filename),
  );
  for (const row of rows) {
    if (row.model_key !== "ox_alpha" || row.judge_error) continue;
    const response = responseByPrompt.get(row.prompt_id);
    if (!response) {
      throw new Error(`Judgment references unknown prompt ${row.prompt_id}`);
    }
    const judgment = {
      label: row.label,
      score: row.score,
      rationale: row.rationale,
      covers: row.covers ?? [],
      omits: row.omits ?? [],
      failure_modes: row.failure_modes ?? [],
      judge_model: row.judge_model,
      prompt_version: row.prompt_version,
      judge_latency_s: row.judge_latency_s ?? null,
      judge_usage: row.judge_usage ?? null,
      graded_at_utc: row.graded_at_utc,
    };
    response.judges.push(judgment);
    judgmentRows.push({ prompt_id: row.prompt_id, ...judgment });
  }
}

const benchmark = await readBenchmark();
const expectedPromptIds = new Set(benchmark.map((row) => row.prompt_id));
if (
  benchmark.length !== 152 ||
  [...responseByPrompt.keys()].some((promptId) => !expectedPromptIds.has(promptId)) ||
  benchmark.some((prompt) => !responseByPrompt.has(prompt.prompt_id))
) {
  throw new Error("Ox Alpha prompt IDs do not exactly match the core-political benchmark");
}

const validResponses = oxResponses.filter(
  (row) => row.response_quality_label === "VALID",
);
const invalidResponses = oxResponses.filter(
  (row) => row.response_quality_label === "INVALID_DEGENERATE",
);
if (
  validResponses.length !== 151 ||
  invalidResponses.length !== 1 ||
  judgmentRows.length !== 604
) {
  throw new Error(
    `Unexpected coverage: ${validResponses.length} valid, ${invalidResponses.length} invalid, ${judgmentRows.length} judgments`,
  );
}
for (const response of responseByPrompt.values()) {
  const expected = response.response_quality_label === "VALID" ? 4 : 0;
  if (response.judges.length !== expected) {
    throw new Error(
      `${response.prompt_id} has ${response.judges.length} judgments; expected ${expected}`,
    );
  }
}

const filteredV1 = filterCsv(cardsV1, "ox_alpha");
const filteredV2 = filterCsv(cardsV2, "ox_alpha");
const v1Rows = parseCsv(filteredV1);
const v2Rows = parseCsv(filteredV2);
const v1Mean = round(mean(v1Rows.map((row) => Number(row.censorship_gap))));
const v2Mean = round(mean(v2Rows.map((row) => Number(row.censorship_gap))));
if (v1Rows.length !== 75 || v2Rows.length !== 75 || v1Mean !== 7.42 || v2Mean !== 6.05) {
  throw new Error(
    `Unexpected matched gaps: cards_v1=${v1Mean} (n=${v1Rows.length}), cards_v2=${v2Mean} (n=${v2Rows.length})`,
  );
}

const summaryV1 = summary.per_arm_matched_gap?.cards_v1?.ox_alpha;
const summaryV2 = summary.per_arm_matched_gap?.cards_v2?.ox_alpha;
if (summaryV1?.estimate !== v1Mean || summaryV2?.estimate !== v2Mean) {
  throw new Error("Pair-level files do not agree with experiment summary");
}

const model = {
  model_key: "ox_alpha",
  display_name: "Ox Alpha",
  served_model: "stealth/ox-alpha",
  provider: "Stealth",
  role: "comparator",
  scope: "core_political",
  provenance_status: "undisclosed",
  lineage_claim: false,
  identity_claim: null,
  weights_included: false,
  reproducibility_note:
    "The endpoint did not support seed and its underlying model provenance was undisclosed. This release is an auditable observation, not a lineage claim.",
};

const payload = {
  schema_version: 1,
  release_id: "ox-alpha-v1",
  benchmark_version: "matched_v2",
  generated_at_utc: "2026-08-24T21:25:46.042964+00:00",
  scope: {
    stratum: "core_political",
    prompts: 152,
    matched_pairs_available: 76,
    matched_pairs_scored: 75,
  },
  model,
  prompts: benchmark.map((prompt) => ({
    ...prompt,
    responses: { ox_alpha: responseByPrompt.get(prompt.prompt_id) },
  })),
  judges: [...new Set(judgmentRows.map((row) => row.judge_model))].sort(),
  run: {
    experiment: "026_card_standard_rejudge",
    result_arm: "cards_v1",
    rubric: "v2",
    endpoint: "stealth/ox-alpha",
    decoding: {
      temperature: 0,
      top_p: 1,
      max_tokens: 32768,
      seed: null,
    },
    data_quality_notes: [
      "One length-finished control response was marked INVALID_DEGENERATE and excluded from matched statistics.",
      "Seed was unsupported by the endpoint; this arm is not deterministically reproducible.",
      "The endpoint's underlying model provenance was undisclosed, so Ox Alpha is a comparator and carries no lineage claim.",
    ],
  },
  statistics: {
    method: summary.method,
    bootstrap_replicates: summary.bootstrap_replicates,
    seed: summary.seed,
    cards_v1: summaryV1,
    cards_v2: summaryV2,
  },
  source_artifacts: {
    repository: "CTGT-Inc/research-censorship-distillation",
    commit: sourceCommit,
    experiment_path: "experiments/026_card_standard_rejudge",
    generations_sha256: await sha256(generationPath),
    cards_v1_judgments: judgeFiles,
    packaged_judgment_source: {
      repository: "CTGT-Inc/lineage-eval-site",
      commit: "261349e204f743d870ec812d5238c3e069142f71",
      decompressed_payload_sha256:
        "a97c43130624f0f143d07850772422d6e471029ef2dadb8ffcf522650b327ebf",
      note:
        "The existing site payload supplied the committed copy of the cards_v1 judgment records; generation fields were verified against the research log before import.",
    },
  },
};

await mkdir(destination, { recursive: true });
await Promise.all([
  writeFile(
    join(destination, "matched-v2-core-political.json"),
    `${JSON.stringify(payload, null, 2)}\n`,
  ),
  writeFile(join(destination, "matched_gaps_cards_v1.csv"), filteredV1),
  writeFile(join(destination, "matched_gaps_cards_v2.csv"), filteredV2),
]);

console.log(
  `Wrote ${payload.prompts.length} Ox Alpha generations, ${judgmentRows.length} cards_v1 judgments, and both 75-pair gap files to ${destination}`,
);

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function readJsonl(path) {
  const rows = [];
  const input = createInterface({ input: createReadStream(path), crlfDelay: Infinity });
  for await (const line of input) {
    if (line.trim()) rows.push(JSON.parse(line));
  }
  return rows;
}

async function readBenchmark() {
  return readJsonl(
    join(repositoryRoot, "data/benchmark/core_political_matched_prompts.jsonl"),
  );
}

function filterCsv(text, modelKey) {
  const [header, ...rows] = text.trim().split(/\r?\n/);
  const kept = rows.filter((row) => row.split(",", 1)[0] === modelKey);
  return `${[header, ...kept].join("\n")}\n`;
}

function parseCsv(text) {
  const [header, ...rows] = text.trim().split(/\r?\n/);
  const keys = header.split(",");
  return rows.map((row) =>
    Object.fromEntries(row.split(",").map((value, index) => [keys[index], value])),
  );
}

function mean(values) {
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function round(value) {
  return Math.round(value * 100) / 100;
}

async function sha256(path) {
  return createHash("sha256").update(await readFile(path)).digest("hex");
}
