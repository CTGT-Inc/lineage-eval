import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const SCHEMA_VERSION = 1;
const MAX_REQUEST_BYTES = 1024 * 1024;

class HumanLabelsError extends Error {
  constructor(message, status = 500, details = {}) {
    super(message);
    this.name = "HumanLabelsError";
    this.status = status;
    this.details = details;
  }
}

function revisionFor(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function readJson(path, label) {
  let content;
  try {
    content = await readFile(path, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }

  try {
    return { content, value: JSON.parse(content) };
  } catch {
    throw new HumanLabelsError(`${label} is not valid JSON.`);
  }
}

function datasetIndex(dataset) {
  if (
    !dataset ||
    typeof dataset !== "object" ||
    !dataset.run?.experiment ||
    !Array.isArray(dataset.prompts)
  ) {
    throw new HumanLabelsError(
      "matched-v2-full-data.json has an invalid data contract.",
    );
  }

  const responseOrder = new Map();
  let index = 0;
  for (const prompt of dataset.prompts) {
    for (const modelKey of dataset.models ?? []) {
      if (!prompt.responses?.[modelKey]) continue;
      const key = `${prompt.batch}\u0000${prompt.prompt_id}\u0000${modelKey}`;
      responseOrder.set(key, index);
      index += 1;
    }
  }
  return responseOrder;
}

function normalizeLabels(labels, dataset) {
  if (!Array.isArray(labels)) {
    throw new HumanLabelsError("labels must be an array.", 400);
  }

  const responseOrder = datasetIndex(dataset);
  const seen = new Set();
  const normalized = labels.map((label) => {
    if (!label || typeof label !== "object") {
      throw new HumanLabelsError("Every label must be an object.", 400);
    }

    const { batch, prompt_id: promptId, model_key: modelKey, score } = label;
    const key = `${batch}\u0000${promptId}\u0000${modelKey}`;
    if (!responseOrder.has(key)) {
      throw new HumanLabelsError(
        `Label references an unknown response: ${batch}/${promptId}/${modelKey}.`,
        400,
      );
    }
    if (!Number.isInteger(score) || score < 0 || score > 100) {
      throw new HumanLabelsError(
        `Human score for ${promptId}/${modelKey} must be an integer from 0 to 100.`,
        400,
      );
    }
    if (seen.has(key)) {
      throw new HumanLabelsError(
        `Duplicate human label for ${promptId}/${modelKey}.`,
        400,
      );
    }
    seen.add(key);
    return {
      batch,
      prompt_id: promptId,
      model_key: modelKey,
      score,
    };
  });

  normalized.sort((left, right) => {
    const leftKey = `${left.batch}\u0000${left.prompt_id}\u0000${left.model_key}`;
    const rightKey = `${right.batch}\u0000${right.prompt_id}\u0000${right.model_key}`;
    return responseOrder.get(leftKey) - responseOrder.get(rightKey);
  });
  return normalized;
}

function emptyDocument(dataset) {
  return {
    schema_version: SCHEMA_VERSION,
    experiment: dataset.run.experiment,
    dataset_generated_at_utc: dataset.generated_at_utc,
    saved_at_utc: null,
    labels: [],
  };
}

function validateDocument(document, dataset) {
  if (!document || typeof document !== "object") {
    throw new HumanLabelsError("human-labels.json must contain an object.");
  }
  if (document.schema_version !== SCHEMA_VERSION) {
    throw new HumanLabelsError(
      `Unsupported human-labels.json schema version: ${document.schema_version}.`,
    );
  }
  if (document.experiment !== dataset.run.experiment) {
    throw new HumanLabelsError(
      `human-labels.json belongs to ${document.experiment}, not ${dataset.run.experiment}.`,
    );
  }
  if (
    document.saved_at_utc !== null &&
    (typeof document.saved_at_utc !== "string" ||
      Number.isNaN(Date.parse(document.saved_at_utc)))
  ) {
    throw new HumanLabelsError("human-labels.json has an invalid saved_at_utc.");
  }

  return {
    schema_version: SCHEMA_VERSION,
    experiment: document.experiment,
    dataset_generated_at_utc:
      typeof document.dataset_generated_at_utc === "string"
        ? document.dataset_generated_at_utc
        : dataset.generated_at_utc,
    saved_at_utc: document.saved_at_utc,
    labels: normalizeLabels(document.labels, dataset),
  };
}

export function createHumanLabelsStore({ datasetPath, labelsPath }) {
  let saveQueue = Promise.resolve();

  async function readDataset() {
    const parsed = await readJson(datasetPath, "matched-v2-full-data.json");
    if (!parsed) {
      throw new HumanLabelsError(
        "matched-v2-full-data.json could not be found.",
      );
    }
    return parsed.value;
  }

  async function read() {
    const dataset = await readDataset();
    const parsed = await readJson(labelsPath, "human-labels.json");
    const document = parsed
      ? validateDocument(parsed.value, dataset)
      : emptyDocument(dataset);
    const serialized = parsed?.content ?? `${JSON.stringify(document, null, 2)}\n`;
    return {
      document,
      revision: revisionFor(serialized),
    };
  }

  async function saveUnlocked({ base_revision: baseRevision, labels }) {
    if (typeof baseRevision !== "string" || !baseRevision) {
      throw new HumanLabelsError("base_revision is required.", 400);
    }

    const current = await read();
    const dataset = await readDataset();
    const normalizedLabels = normalizeLabels(labels, dataset);
    if (current.revision !== baseRevision) {
      if (
        JSON.stringify(current.document.labels) ===
        JSON.stringify(normalizedLabels)
      ) {
        return current;
      }
      throw new HumanLabelsError(
        "The human-label file changed after this page loaded. Refresh before saving so another annotator's work is not overwritten.",
        409,
        { current_revision: current.revision },
      );
    }

    const document = {
      schema_version: SCHEMA_VERSION,
      experiment: dataset.run.experiment,
      dataset_generated_at_utc: dataset.generated_at_utc,
      saved_at_utc: new Date().toISOString(),
      labels: normalizedLabels,
    };
    const serialized = `${JSON.stringify(document, null, 2)}\n`;
    const temporaryPath = `${labelsPath}.${process.pid}.${randomUUID()}.tmp`;

    try {
      await mkdir(dirname(labelsPath), { recursive: true });
      await writeFile(temporaryPath, serialized, { encoding: "utf8", flag: "wx" });
      await rename(temporaryPath, labelsPath);
    } catch (error) {
      await unlink(temporaryPath).catch(() => {});
      throw error;
    }

    return {
      document,
      revision: revisionFor(serialized),
    };
  }

  function save(input) {
    const operation = saveQueue.then(() => saveUnlocked(input));
    saveQueue = operation.catch(() => {});
    return operation;
  }

  return { read, save };
}

async function readRequestJson(request) {
  const contentType = request.headers["content-type"] ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new HumanLabelsError("Content-Type must be application/json.", 415);
  }

  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) {
      throw new HumanLabelsError("Request body is too large.", 413);
    }
    chunks.push(chunk);
  }

  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new HumanLabelsError("Request body is not valid JSON.", 400);
  }
}

function writeJson(response, status, value) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  response.end(`${JSON.stringify(value)}\n`);
}

function sameOrigin(request) {
  const origin = request.headers.origin;
  const host = request.headers.host;
  if (!origin || !host) return true;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function humanLabelsApi() {
  return {
    name: "human-labels-api",
    apply: "serve",
    configureServer(server) {
      const store = createHumanLabelsStore({
        datasetPath: resolve(
          server.config.root,
          "public/matched-v2-full-data.json",
        ),
        labelsPath: resolve(
          server.config.root,
          "../data/annotations/blog-v1/human-labels.json",
        ),
      });

      server.middlewares.use(async (request, response, next) => {
        const path = new URL(request.url ?? "/", "http://viewer.local").pathname;
        if (path !== "/api/human-labels") {
          next();
          return;
        }

        try {
          if (request.method === "GET") {
            const current = await store.read();
            writeJson(response, 200, {
              ...current.document,
              revision: current.revision,
            });
            return;
          }

          if (request.method === "POST") {
            if (!sameOrigin(request)) {
              throw new HumanLabelsError("Cross-origin saves are not allowed.", 403);
            }
            const input = await readRequestJson(request);
            const saved = await store.save(input);
            writeJson(response, 200, {
              ...saved.document,
              revision: saved.revision,
            });
            return;
          }

          response.setHeader("Allow", "GET, POST");
          writeJson(response, 405, { error: "Method not allowed." });
        } catch (error) {
          const status =
            error instanceof HumanLabelsError ? error.status : 500;
          writeJson(response, status, {
            error:
              error instanceof Error
                ? error.message
                : "Could not update human-labels.json.",
            ...(error instanceof HumanLabelsError ? error.details : {}),
          });
        }
      });
    },
  };
}
