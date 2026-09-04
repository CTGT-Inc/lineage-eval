#!/usr/bin/env node

import { createHash } from "node:crypto";
import { copyFile, cp, mkdir, mkdtemp, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = resolve(process.argv[2] ?? join(repositoryRoot, "data/results/ox-alpha-v1/matched-v2-core-political.json"));
const destination = resolve(process.argv[3] ?? join(repositoryRoot, "release/huggingface/ox-alpha-v1"));
const resultDirectory = dirname(sourcePath);

assertSafeDestination(destination);
await mkdir(dirname(destination), { recursive: true });
const staging = await mkdtemp(join(dirname(destination), `.${basename(destination)}-staging-`));

try {
  const sourceBytes = await readFile(sourcePath);
  const source = JSON.parse(sourceBytes.toString("utf8"));
  validateSource(source);
  await Promise.all([
    copyFile(join(repositoryRoot, "release/huggingface/OX_ALPHA_DATASET_CARD.md"), join(staging, "README.md")),
    copyFile(join(repositoryRoot, "data/LICENSE"), join(staging, "LICENSE")),
    copyFile(join(repositoryRoot, "LICENSE"), join(staging, "CODE_LICENSE")),
    copyFile(join(repositoryRoot, "NOTICE"), join(staging, "NOTICE")),
    cp(join(repositoryRoot, "THIRD_PARTY_NOTICES"), join(staging, "THIRD_PARTY_NOTICES"), { recursive: true }),
    mkdir(join(staging, "data/benchmark"), { recursive: true }),
    mkdir(join(staging, "data/responses"), { recursive: true }),
    mkdir(join(staging, "data/judgments"), { recursive: true }),
    mkdir(join(staging, "metadata"), { recursive: true }),
    mkdir(join(staging, "analysis"), { recursive: true }),
    mkdir(join(staging, "viewer"), { recursive: true }),
  ]);

  const benchmarkRows = [];
  const responseRows = [];
  const judgmentRows = [];
  for (const prompt of source.prompts) {
    const { responses: _responses, ...benchmark } = prompt;
    benchmarkRows.push({ ...benchmark, release_id: source.release_id, reference_card_status: "model_drafted_unverified", provenance_status: "upstream_inventory_influence_not_fully_mapped" });
    const response = prompt.responses.ox_alpha;
    const responseId = `${prompt.prompt_id}::ox_alpha`;
    const scores = response.judges.map((row) => row.score);
    const included = response.response_quality_label === "VALID";
    const meanFidelity = included ? scores.reduce((total, score) => total + score, 0) / scores.length : null;
    const { judges: _judges, ...generation } = response;
    responseRows.push({ release_id: source.release_id, benchmark_version: source.benchmark_version, response_id: responseId, prompt_id: prompt.prompt_id, ...generation, included_in_statistics: included, mean_fidelity_score: meanFidelity, censorship_score: meanFidelity === null ? null : 100 - meanFidelity });
    for (const judgment of response.judges) {
      judgmentRows.push({ release_id: source.release_id, benchmark_version: source.benchmark_version, judgment_id: [prompt.prompt_id, "ox_alpha", judgment.judge_model, judgment.prompt_version].join("::"), response_id: responseId, prompt_id: prompt.prompt_id, model_key: "ox_alpha", ...judgment, included_in_statistics: included, censorship_score: included ? 100 - judgment.score : null });
    }
  }

  await Promise.all([
    writeJsonl(join(staging, "data/benchmark/evaluation.jsonl"), benchmarkRows),
    writeJsonl(join(staging, "data/responses/evaluation.jsonl"), responseRows),
    writeJsonl(join(staging, "data/judgments/evaluation.jsonl"), judgmentRows),
    writeJson(join(staging, "metadata/model.json"), source.model),
    writeJson(join(staging, "metadata/run.json"), { schema_version: source.schema_version, release_id: source.release_id, benchmark_version: source.benchmark_version, generated_at_utc: source.generated_at_utc, scope: source.scope, run: source.run, source_artifacts: source.source_artifacts, source_payload_sha256: createHash("sha256").update(sourceBytes).digest("hex") }),
    writeJson(join(staging, "metadata/statistics.json"), source.statistics),
    writeJson(join(staging, "viewer/viewer_payload.json"), source, false),
    copyFile(join(resultDirectory, "matched_gaps_cards_v1.csv"), join(staging, "analysis/matched_gaps_cards_v1.csv")),
    copyFile(join(resultDirectory, "matched_gaps_cards_v2.csv"), join(staging, "analysis/matched_gaps_cards_v2.csv")),
  ]);

  const files = (await listFiles(staging)).filter((path) => basename(path) !== "MANIFEST.json");
  const manifestFiles = [];
  for (const path of files) {
    const bytes = await readFile(path);
    manifestFiles.push({ path: relative(staging, path), bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") });
  }
  await writeJson(join(staging, "MANIFEST.json"), { schema_version: 1, release_id: source.release_id, row_counts: { benchmark: benchmarkRows.length, responses: responseRows.length, judgments: judgmentRows.length, models: 1, judges: source.judges.length }, files: manifestFiles.sort((a, b) => a.path.localeCompare(b.path)) });
  await rm(destination, { recursive: true, force: true });
  await rename(staging, destination);
  console.log(`Wrote ${benchmarkRows.length} prompts, ${responseRows.length} responses, and ${judgmentRows.length} judgments to ${destination}`);
} catch (error) {
  await rm(staging, { recursive: true, force: true });
  throw error;
}

function validateSource(source) {
  if (source.release_id !== "ox-alpha-v1" || source.model?.model_key !== "ox_alpha" || source.model?.provenance_status !== "undisclosed" || source.model?.lineage_claim !== false || source.model?.identity_claim !== null) throw new Error("Ox Alpha release metadata must retain undisclosed provenance and no lineage claim");
  if (source.prompts?.length !== 152 || source.judges?.length !== 4) throw new Error("Unexpected Ox Alpha release coverage");
}

function assertSafeDestination(path) {
  if (path === dirname(path) || path === repositoryRoot || repositoryRoot.startsWith(`${path}${sep}`)) throw new Error(`Refusing unsafe release destination: ${path}`);
}

async function writeJsonl(path, rows) { await writeFile(path, `${rows.map((row) => JSON.stringify(row)).join("\n")}\n`); }
async function writeJson(path, value, pretty = true) { await writeFile(path, `${JSON.stringify(value, null, pretty ? 2 : undefined)}\n`); }
async function listFiles(directory) { const paths = []; for (const entry of await readdir(directory)) { const path = join(directory, entry); if ((await stat(path)).isDirectory()) paths.push(...(await listFiles(path))); else paths.push(path); } return paths; }
