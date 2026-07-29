import type { EvaluationPrompt, ModelKey } from "../types";

export const MODEL_META: Record<
  ModelKey,
  { label: string; shortLabel: string; role: string; tone: string }
> = {
  gpt_oss_120b: {
    label: "GPT-OSS 120B",
    shortLabel: "Base",
    role: "Student base",
    tone: "base",
  },
  self_distilled: {
    label: "Self-distilled",
    shortLabel: "Self",
    role: "Finetuning control",
    tone: "self-distilled",
  },
  v4_flash_distilled: {
    label: "V4 Flash-distilled (forward)",
    shortLabel: "Flash",
    role: "Teacher-forward student LoRA",
    tone: "flash-distilled",
  },
  v4_flash: {
    label: "V4 Flash",
    shortLabel: "Teacher",
    role: "Teacher comparator",
    tone: "teacher",
  },
  gpt_oss_20b: {
    label: "GPT-OSS 20B",
    shortLabel: "20B base",
    role: "20B student base",
    tone: "base",
  },
  expert_20b_self_sturev: {
    label: "Expert 20B self-distilled",
    shortLabel: "20B expert",
    role: "Self-distilled expert LoRA",
    tone: "self-distilled",
  },
};

export function pretty(value: string) {
  if (value === "gpt_oss_120b") return "GPT-OSS 120B";
  if (value === "gpt_oss_20b") return "GPT-OSS 20B";
  if (value === "expert_20b_self_sturev") return "Expert 20B self-distilled";
  return value
    .replaceAll("_", " ")
    .replaceAll("/", " / ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function promptSearchText(prompt: EvaluationPrompt) {
  return [
    prompt.prompt_id,
    prompt.concept_id,
    prompt.concept,
    prompt.stratum,
    prompt.tier,
    prompt.frame_type,
    prompt.condition,
    prompt.control_entity,
    prompt.must_engage,
    prompt.prompt,
    ...Object.values(prompt.responses).map((response) => response.content),
    ...Object.values(prompt.responses).flatMap((response) =>
      response.judges.flatMap((judge) => [
        judge.judge_model,
        judge.label,
        judge.rationale,
        ...judge.failure_modes,
      ]),
    ),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function formatTokens(value: number | null | undefined) {
  if (value == null) return "—";
  return value.toLocaleString();
}

export function formatLatency(value: number | null | undefined) {
  if (value == null) return "—";
  return `${value.toFixed(1)}s`;
}

export function pairId(prompt: EvaluationPrompt, condition: "sensitive" | "control") {
  return prompt.prompt_id.replace(/-(S|C)-/, condition === "sensitive" ? "-S-" : "-C-");
}
