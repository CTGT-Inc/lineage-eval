import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createHumanLabelsStore } from "../scripts/human-labels-api.mjs";

const dataset = {
  generated_at_utc: "2026-07-27T00:00:00.000Z",
  models: ["model_a", "model_b"],
  run: { experiment: "test_experiment" },
  prompts: [
    {
      batch: "finance",
      prompt_id: "A01-S-F1",
      responses: {
        model_a: { content: "First response" },
        model_b: { content: "Second response" },
      },
    },
  ],
};

test("human labels survive reloads, relabel cleanly, and reject stale saves", async () => {
  const directory = await mkdtemp(join(tmpdir(), "viewer-human-labels-"));
  const datasetPath = join(directory, "matched-v2-full-data.json");
  const labelsPath = join(directory, "human-labels.json");
  await writeFile(datasetPath, `${JSON.stringify(dataset)}\n`);

  try {
    const firstStore = createHumanLabelsStore({ datasetPath, labelsPath });
    const empty = await firstStore.read();
    assert.deepEqual(empty.document.labels, []);

    const firstSave = await firstStore.save({
      base_revision: empty.revision,
      labels: [
        {
          batch: "finance",
          prompt_id: "A01-S-F1",
          model_key: "model_a",
          score: 42,
        },
      ],
    });

    const reopenedStore = createHumanLabelsStore({ datasetPath, labelsPath });
    const reopened = await reopenedStore.read();
    assert.equal(reopened.revision, firstSave.revision);
    assert.equal(reopened.document.labels[0].score, 42);

    const relabeled = await reopenedStore.save({
      base_revision: reopened.revision,
      labels: [
        {
          batch: "finance",
          prompt_id: "A01-S-F1",
          model_key: "model_a",
          score: 88,
        },
        {
          batch: "finance",
          prompt_id: "A01-S-F1",
          model_key: "model_b",
          score: 0,
        },
      ],
    });
    assert.deepEqual(
      relabeled.document.labels.map((label) => label.score),
      [88, 0],
    );

    const idempotentRetry = await firstStore.save({
      base_revision: empty.revision,
      labels: relabeled.document.labels,
    });
    assert.equal(idempotentRetry.revision, relabeled.revision);

    await assert.rejects(
      firstStore.save({
        base_revision: empty.revision,
        labels: [],
      }),
      (error) => error.status === 409,
    );

    const persisted = JSON.parse(await readFile(labelsPath, "utf8"));
    assert.deepEqual(
      persisted.labels.map((label) => label.score),
      [88, 0],
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("human labels reject out-of-range scores and unknown responses", async () => {
  const directory = await mkdtemp(join(tmpdir(), "viewer-human-labels-"));
  const datasetPath = join(directory, "matched-v2-full-data.json");
  const labelsPath = join(directory, "human-labels.json");
  await writeFile(datasetPath, `${JSON.stringify(dataset)}\n`);

  try {
    const store = createHumanLabelsStore({ datasetPath, labelsPath });
    const empty = await store.read();

    await assert.rejects(
      store.save({
        base_revision: empty.revision,
        labels: [
          {
            batch: "finance",
            prompt_id: "A01-S-F1",
            model_key: "model_a",
            score: 101,
          },
        ],
      }),
      (error) => error.status === 400,
    );

    await assert.rejects(
      store.save({
        base_revision: empty.revision,
        labels: [
          {
            batch: "finance",
            prompt_id: "missing",
            model_key: "model_a",
            score: 50,
          },
        ],
      }),
      (error) => error.status === 400,
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
