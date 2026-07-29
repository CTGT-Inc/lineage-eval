import {
  MODEL_KEYS,
  type HumanLabel,
  type ModelKey,
  type EvaluationBatch,
  type EvaluationDataset,
} from "../types";

export type HumanScoreMap = Record<string, number>;

export function humanLabelKey(
  batch: EvaluationBatch,
  promptId: string,
  modelKey: ModelKey,
) {
  return `${batch}\u0000${promptId}\u0000${modelKey}`;
}

export function labelsToScoreMap(labels: HumanLabel[]): HumanScoreMap {
  return Object.fromEntries(
    labels.map((label) => [
      humanLabelKey(label.batch, label.prompt_id, label.model_key),
      label.score,
    ]),
  );
}

export function scoreMapToLabels(
  dataset: EvaluationDataset,
  scores: HumanScoreMap,
): HumanLabel[] {
  const labels: HumanLabel[] = [];

  for (const prompt of dataset.prompts) {
    for (const modelKey of MODEL_KEYS) {
      if (!prompt.responses[modelKey]) continue;
      const score = scores[humanLabelKey(prompt.batch, prompt.prompt_id, modelKey)];
      if (score === undefined) continue;
      labels.push({
        batch: prompt.batch,
        prompt_id: prompt.prompt_id,
        model_key: modelKey,
        score,
      });
    }
  }

  return labels;
}

export function scoreMapsEqual(left: HumanScoreMap, right: HumanScoreMap) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every((key) => left[key] === right[key])
  );
}
