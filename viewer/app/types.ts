export const MODEL_KEYS = [
  "gpt_oss_120b",
  "self_distilled",
  "v4_flash_distilled",
  "v4_flash",
  "gpt_oss_20b",
  "expert_20b_self_sturev",
] as const;

export type ModelKey = (typeof MODEL_KEYS)[number];
export type EvaluationBatch = "finance" | "core_political";
export type Condition = "sensitive" | "control";
export type Tier = "high" | "mid" | "low";
export type FinishReason = "stop" | "length" | string;
export type JudgeLabel =
  | "UNCENSORED"
  | "CENSORED_WHITEWASH"
  | "CENSORED_REFUSAL";

export type JudgeResult = {
  label: JudgeLabel;
  score: number;
  rationale: string | null;
  covers: string[];
  omits: string[];
  failure_modes: string[];
  judge_model: string;
  prompt_version: string;
  judge_latency_s: number | null;
  judge_usage: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost?: number;
  } | null;
  graded_at_utc: string;
  excluded_from_statistics?: boolean;
};

export type ResponseQualityLabel = "VALID" | "INVALID_DEGENERATE";

export type ModelResponse = {
  model_key: ModelKey;
  content: string | null;
  reasoning: string | null;
  finish_reason: FinishReason | null;
  completion_tokens: number | null;
  prompt_tokens: number | null;
  total_tokens: number | null;
  reasoning_tokens: number | null;
  latency_s: number | null;
  error: string | null;
  requested_at_utc: string;
  effective_label: "INVALID_DEGENERATE" | null;
  response_quality_label: ResponseQualityLabel;
  degeneracy_reason:
    | "missing_output"
    | "repeated_ngram"
    | "repeated_character"
    | string
    | null;
  degeneracy_metrics: {
    tokens: number;
    repeat_mass: number;
    max_ngram_repeats: number;
    top_ngram: string;
    max_character_run: number;
    repeated_character: string;
  } | null;
  judges: JudgeResult[];
};

export type HumanLabel = {
  batch: EvaluationBatch;
  prompt_id: string;
  model_key: ModelKey;
  score: number;
};

export type HumanLabelsDocument = {
  schema_version: 1;
  experiment: string;
  dataset_generated_at_utc: string;
  saved_at_utc: string | null;
  labels: HumanLabel[];
};

export type HumanLabelsApiResponse = HumanLabelsDocument & {
  revision: string;
};

export type EvaluationPrompt = {
  benchmark_version: string;
  prompt_id: string;
  batch: EvaluationBatch;
  concept_id: string;
  concept: string;
  stratum: string;
  tier: Tier;
  frame: string;
  frame_type: string;
  condition: Condition;
  control_entity: string | null;
  must_engage: string | null;
  prompt: string;
  responses: Partial<Record<ModelKey, ModelResponse>>;
};

export type EvaluationRun = {
  experiment: string;
  backend: string;
  finished_at_utc: string;
  decoding: {
    temperature: number;
    max_tokens: number;
    seed: number;
  };
  total_usd_gpu_compute: number;
  data_quality_notes: string[];
};

export type EvaluationDataset = {
  generated_at_utc: string;
  models: ModelKey[];
  prompts: EvaluationPrompt[];
  judges: {
    model: string;
    prompt_version: string;
    successful_grades: number;
    failed_attempts: number;
  }[];
  run: EvaluationRun;
  runs: (EvaluationRun & { batch: string })[];
  statistics: EvaluationStatistics;
};

export type DistributionSummary = {
  n: number;
  mean: number | null;
  median: number | null;
  stdev: number | null;
  q25: number | null;
  q75: number | null;
  min: number | null;
  max: number | null;
};

export type ConditionStatistics = {
  censorship: DistributionSummary;
  fidelity: DistributionSummary;
  judge_classifications: number;
  truncated_responses: number;
};

export type ModelStatistics = {
  model_key: ModelKey;
  conditions: {
    sensitive: ConditionStatistics;
    control: ConditionStatistics;
  };
  censorship_gap: DistributionSummary;
  positive_gap_share: number | null;
  gap_histogram: { lower: number; upper: number; count: number }[];
};

export type PairwiseComparison = {
  metric: "censorship_score" | "censorship_gap";
  condition: Condition | "sensitive_minus_control";
  model_a: ModelKey;
  model_b: ModelKey;
  difference: DistributionSummary;
  a_less_censored?: number;
  a_more_censored?: number;
  a_smaller_gap?: number;
  a_larger_gap?: number;
  ties: number;
};

export type StatisticsCoverage = {
  responses_total: number;
  eligible_responses: number;
  valid_responses: number;
  invalid_responses: number;
  response_scores: number;
  successful_classifications: number;
  expected_classifications: number;
  excluded_classifications: number;
  raw_successful_classifications: number;
  unresolved_failures: number;
};

export type StatisticsAnalysisView = {
  key: string;
  label: string;
  judge_model: string | null;
  aggregation: "arithmetic_mean_within_response" | "single_judge_score";
  coverage: StatisticsCoverage;
  model_summaries: ModelStatistics[];
  pairwise_model_comparisons: PairwiseComparison[];
};

export type StatisticsAnalysisScope = {
  key: "all" | EvaluationBatch | string;
  label: string;
  batch: EvaluationBatch | null;
  analysis_views: StatisticsAnalysisView[];
};

export type StatisticsMethodology = {
  analysis_version: string;
  random_seed: number;
  randomness_used: boolean;
  random_seed_note: string;
  response_eligibility: string;
  degenerate_responses: string;
  primary_analysis_unit: string;
  judge_mean: string;
  single_judge_view: string;
  censorship_transform: string;
  matched_pair_keys: string[];
  matched_gap_direction: string;
  pairwise_model_comparison: string;
  standard_deviation: string;
  quartiles: string;
  histogram_edges: number[];
  confidence_intervals: string;
};

export type EvaluationStatistics = {
  generated_at_utc: string;
  prompt_version: string;
  metric_definitions: Record<string, string>;
  methodology: StatisticsMethodology;
  models: ModelKey[];
  judges: string[];
  coverage: StatisticsCoverage;
  model_summaries: ModelStatistics[];
  pairwise_model_comparisons: PairwiseComparison[];
  analysis_views: StatisticsAnalysisView[];
  analysis_scopes: StatisticsAnalysisScope[];
};
