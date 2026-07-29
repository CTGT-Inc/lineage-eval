"use client";

import { useMemo, useState } from "react";
import { MODEL_META, pretty } from "../lib/evaluation";
import type {
  ModelKey,
  PairwiseComparison,
  EvaluationPrompt,
  EvaluationStatistics,
  StatisticsAnalysisView,
} from "../types";

type StatisticsViewProps = {
  statistics: EvaluationStatistics;
  prompts: EvaluationPrompt[];
  selectedScopeKey: string;
  onScopeChange: (key: string) => void;
  selectedViewKey: string;
  onViewChange: (key: string) => void;
};

type HistogramSelection = {
  scopeKey: string;
  viewKey: string;
  modelKey: ModelKey;
  lower: number;
  upper: number;
};

type HistogramExample = {
  sensitive: EvaluationPrompt;
  control: EvaluationPrompt;
  sensitiveCensorship: number;
  controlCensorship: number;
  gap: number;
};

const JUDGE_NAMES: Record<string, string> = {
  "x-ai/grok-4.20": "Grok 4.20",
  "anthropic/claude-sonnet-4": "Claude Sonnet 4",
  "anthropic/claude-sonnet-4.6": "Claude Sonnet 4.6",
  "google/gemini-3.5-flash": "Gemini 3.5 Flash",
  "openai/gpt-5-mini": "GPT-5 Mini",
};

function score(value: number | null, sign = false) {
  if (value === null) return "—";
  return `${sign && value > 0 ? "+" : ""}${value.toFixed(1)}`;
}

function modelLabel(key: ModelKey) {
  return MODEL_META[key]?.label ?? key;
}

function viewLabel(view: StatisticsAnalysisView) {
  return view.judge_model
    ? (JUDGE_NAMES[view.judge_model] ?? view.judge_model)
    : view.label;
}

function comparisonSentence(row: PairwiseComparison) {
  const value = row.difference.mean;
  if (value === null || value === 0) return "No average difference";
  const first = modelLabel(row.model_a);
  const second = modelLabel(row.model_b);
  if (row.metric === "censorship_gap") {
    return value > 0
      ? `${first} has the larger selective gap`
      : `${first} has the smaller selective gap`;
  }
  return value > 0
    ? `${first} is more censored than ${second}`
    : `${first} is less censored than ${second}`;
}

function signed(value: number) {
  return `${value > 0 ? "+" : ""}${Number.isInteger(value) ? value : value.toFixed(1)}`;
}

function roundScore(value: number) {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function responsePreview(value: string | null | undefined) {
  if (!value?.trim()) return "[EMPTY OUTPUT]";
  return value
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_~`>|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function responseCensorship(
  prompt: EvaluationPrompt,
  modelKey: ModelKey,
  judgeModel: string | null,
  promptVersion: string,
) {
  const response = prompt.responses[modelKey];
  if (
    !response ||
    response.effective_label === "INVALID_DEGENERATE" ||
    response.response_quality_label === "INVALID_DEGENERATE"
  ) {
    return null;
  }

  const scores = response.judges
    .filter(
      (judge) =>
        judge.prompt_version === promptVersion &&
        !judge.excluded_from_statistics &&
        (!judgeModel || judge.judge_model === judgeModel),
    )
    .map((judge) => judge.score);
  if (!scores.length) return null;

  return roundScore(
    100 - scores.reduce((total, value) => total + value, 0) / scores.length,
  );
}

function matchedExamples(
  prompts: EvaluationPrompt[],
  modelKey: ModelKey,
  lower: number,
  upper: number,
  finalBin: boolean,
  judgeModel: string | null,
  promptVersion: string,
  batch: string | null,
) {
  const pairs = new Map<
    string,
    { sensitive?: EvaluationPrompt; control?: EvaluationPrompt }
  >();

  for (const prompt of prompts) {
    if (batch && prompt.batch !== batch) continue;
    const key = `${prompt.batch}\u0000${prompt.concept_id}\u0000${prompt.frame}`;
    const pair = pairs.get(key) ?? {};
    pair[prompt.condition] = prompt;
    pairs.set(key, pair);
  }

  const midpoint = (lower + upper) / 2;
  const examples: HistogramExample[] = [];
  for (const pair of pairs.values()) {
    if (!pair.sensitive || !pair.control) continue;
    const sensitiveCensorship = responseCensorship(
      pair.sensitive,
      modelKey,
      judgeModel,
      promptVersion,
    );
    const controlCensorship = responseCensorship(
      pair.control,
      modelKey,
      judgeModel,
      promptVersion,
    );
    if (sensitiveCensorship === null || controlCensorship === null) continue;

    const gap = roundScore(sensitiveCensorship - controlCensorship);
    if (lower <= gap && (gap < upper || (finalBin && gap === upper))) {
      examples.push({
        sensitive: pair.sensitive,
        control: pair.control,
        sensitiveCensorship,
        controlCensorship,
        gap,
      });
    }
  }

  return examples
    .sort(
      (first, second) =>
        Math.abs(first.gap - midpoint) - Math.abs(second.gap - midpoint) ||
        first.sensitive.prompt_id.localeCompare(second.sensitive.prompt_id),
    )
    .slice(0, 4);
}

export function StatisticsView({
  statistics,
  prompts,
  selectedScopeKey,
  onScopeChange,
  selectedViewKey,
  onViewChange,
}: StatisticsViewProps) {
  const [comparisonMetric, setComparisonMetric] = useState<
    "sensitive" | "control" | "sensitive_minus_control"
  >("sensitive_minus_control");
  const [histogramSelection, setHistogramSelection] =
    useState<HistogramSelection | null>(null);
  const fallbackView: StatisticsAnalysisView = {
    key: "mean",
    label: `Mean of ${statistics.judges.length} judges`,
    judge_model: null,
    aggregation: "arithmetic_mean_within_response",
    coverage: statistics.coverage,
    model_summaries: statistics.model_summaries,
    pairwise_model_comparisons: statistics.pairwise_model_comparisons,
  };
  const fallbackScope = {
    key: "all",
    label: "All prompts",
    batch: null,
    analysis_views: statistics.analysis_views?.length
      ? statistics.analysis_views
      : [fallbackView],
  };
  const analysisScopes = statistics.analysis_scopes?.length
    ? statistics.analysis_scopes
    : [fallbackScope];
  const activeScope =
    analysisScopes.find((scope) => scope.key === selectedScopeKey) ??
    analysisScopes[0];
  const analysisViews = activeScope.analysis_views;
  const activeView =
    analysisViews.find((view) => view.key === selectedViewKey) ??
    analysisViews[0];
  const comparisons = activeView.pairwise_model_comparisons.filter(
    (row) => row.condition === comparisonMetric,
  );
  const complete =
    activeView.coverage.successful_classifications ===
    activeView.coverage.expected_classifications;
  const activeHistogramSelection =
    histogramSelection?.scopeKey === activeScope.key &&
    histogramSelection.viewKey === activeView.key
      ? histogramSelection
      : null;
  const selectedModel = activeHistogramSelection
    ? activeView.model_summaries.find(
        (model) => model.model_key === activeHistogramSelection.modelKey,
      )
    : null;
  const selectedBin = selectedModel?.gap_histogram.find(
    (bin) =>
      bin.lower === activeHistogramSelection?.lower &&
      bin.upper === activeHistogramSelection.upper,
  );
  const selectedExamples = useMemo(() => {
    if (!activeHistogramSelection || !selectedModel || !selectedBin) return [];
    return matchedExamples(
      prompts,
      activeHistogramSelection.modelKey,
      activeHistogramSelection.lower,
      activeHistogramSelection.upper,
      selectedModel.gap_histogram.at(-1) === selectedBin,
      activeView.judge_model,
      statistics.prompt_version,
      activeScope.batch,
    );
  }, [
    activeHistogramSelection,
    activeScope.batch,
    activeView.judge_model,
    prompts,
    selectedBin,
    selectedModel,
    statistics.prompt_version,
  ]);

  return (
    <section className="statistics-workspace">
      <div className="statistics-intro">
        <div>
          <p className="eyebrow">
            {activeScope.label} · matched sensitive/control analysis
          </p>
          <h2>Censorship profile by model</h2>
          <p>
            {activeView.judge_model
              ? `${viewLabel(activeView)} scores each response directly. `
              : `${statistics.judges.length} judge scores are averaged within each response first. `}
            Censorship is 100 minus fidelity; a positive gap means the sensitive
            response was more censored than its matched control. Degenerate outputs
            are a fourth category and are excluded from every score and matched
            pair.
          </p>
        </div>
        <div className="statistics-status">
          <label className="judge-view-select">
            <span>Prompt set</span>
            <select
              aria-label="Statistics prompt set"
              value={activeScope.key}
              onChange={(event) => {
                setHistogramSelection(null);
                onScopeChange(event.target.value);
              }}
            >
              {analysisScopes.map((scope) => (
                <option key={scope.key} value={scope.key}>
                  {scope.label}
                </option>
              ))}
            </select>
          </label>
          <label className="judge-view-select">
            <span>Scoring view</span>
            <select
              aria-label="Statistics judge"
              value={activeView.key}
              onChange={(event) => {
                setHistogramSelection(null);
                onViewChange(event.target.value);
              }}
            >
              {analysisViews.map((view) => (
                <option key={view.key} value={view.key}>
                  {viewLabel(view)}
                </option>
              ))}
            </select>
          </label>
          <div className={`coverage-badge ${complete ? "complete" : "partial"}`}>
            <strong>
              {activeView.coverage.successful_classifications.toLocaleString()}
              <span>
                /{activeView.coverage.expected_classifications.toLocaleString()}
              </span>
            </strong>
            <small>judge classifications</small>
          </div>
          <div className="coverage-badge invalid">
            <strong>{activeView.coverage.invalid_responses.toLocaleString()}</strong>
            <small>degenerate outputs excluded</small>
          </div>
        </div>
      </div>

      <div className="statistics-scroll">
        <section className="summary-table-card">
          <div className="analytics-heading">
            <div>
              <p className="eyebrow">Headline comparison</p>
              <h3>Average censorship and matched gap</h3>
            </div>
            <span>0 = fully faithful · 100 = fully censored</span>
          </div>
          <div className="statistics-table-wrap">
            <table className="statistics-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Sensitive</th>
                  <th>Control</th>
                  <th>Average gap</th>
                  <th>Median gap</th>
                  <th>Positive gaps</th>
                  <th>Pairs</th>
                </tr>
              </thead>
              <tbody>
                {activeView.model_summaries.map((model) => (
                  <tr key={model.model_key}>
                    <th>
                      <span
                        className={`analytics-model-dot ${MODEL_META[model.model_key].tone}`}
                      />
                      {modelLabel(model.model_key)}
                    </th>
                    <td>
                      {score(model.conditions.sensitive.censorship.mean)}
                    </td>
                    <td>{score(model.conditions.control.censorship.mean)}</td>
                    <td
                      className={
                        (model.censorship_gap.mean ?? 0) > 0
                          ? "gap-positive"
                          : "gap-negative"
                      }
                    >
                      {score(model.censorship_gap.mean, true)}
                    </td>
                    <td>{score(model.censorship_gap.median, true)}</td>
                    <td>
                      {model.positive_gap_share === null
                        ? "—"
                        : `${(model.positive_gap_share * 100).toFixed(0)}%`}
                    </td>
                    <td>{model.censorship_gap.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="analytics-card">
          <div className="analytics-heading">
            <div>
              <p className="eyebrow">Condition comparison</p>
              <h3>Sensitive versus control</h3>
            </div>
            <span>Mean response-level censorship</span>
          </div>
          <div className="condition-chart">
            {activeView.model_summaries.map((model) => {
              const sensitive =
                model.conditions.sensitive.censorship.mean ?? 0;
              const control = model.conditions.control.censorship.mean ?? 0;
              return (
                <div className="condition-chart-row" key={model.model_key}>
                  <strong>{modelLabel(model.model_key)}</strong>
                  <div className="condition-series">
                    <span>Sensitive</span>
                    <div className="bar-track">
                      <i
                        className="bar-sensitive"
                        style={{ width: `${sensitive}%` }}
                      />
                    </div>
                    <b>{score(sensitive)}</b>
                  </div>
                  <div className="condition-series">
                    <span>Control</span>
                    <div className="bar-track">
                      <i
                        className="bar-control"
                        style={{ width: `${control}%` }}
                      />
                    </div>
                    <b>{score(control)}</b>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="analytics-card">
          <div className="analytics-heading">
            <div>
              <p className="eyebrow">Distribution</p>
              <h3>Matched censorship gaps</h3>
            </div>
            <span>Each bar counts sensitive/control prompt pairs</span>
          </div>
          <div className="histogram-grid">
            {activeView.model_summaries.map((model) => {
              const maximum = Math.max(
                1,
                ...model.gap_histogram.map((bin) => bin.count),
              );
              const modelSelected =
                activeHistogramSelection?.modelKey === model.model_key;
              return (
                <article
                  className={`histogram-card ${
                    modelSelected ? "selected" : ""
                  }`}
                  key={model.model_key}
                >
                  <div className="histogram-title">
                    <strong>{modelLabel(model.model_key)}</strong>
                    <span>
                      μ {score(model.censorship_gap.mean, true)} · n{" "}
                      {model.censorship_gap.n}
                    </span>
                  </div>
                  <div
                    className="histogram"
                    aria-label={`${modelLabel(model.model_key)} gap histogram`}
                  >
                    {model.gap_histogram.map((bin) => {
                      const selected =
                        activeHistogramSelection?.modelKey ===
                          model.model_key &&
                        activeHistogramSelection.lower === bin.lower &&
                        activeHistogramSelection.upper === bin.upper;
                      const range = `${signed(bin.lower)} to ${signed(bin.upper)}`;
                      return (
                        <button
                          type="button"
                          className={`histogram-bin ${
                            selected ? "selected" : ""
                          }`}
                          key={`${bin.lower}-${bin.upper}`}
                          title={`${range}: ${bin.count} pairs${
                            bin.count ? " · Click to inspect examples" : ""
                          }`}
                          aria-label={`${modelLabel(model.model_key)}, gap ${range}: ${
                            bin.count
                          } pairs`}
                          aria-pressed={selected}
                          data-count={bin.count}
                          disabled={bin.count === 0}
                          onClick={() =>
                            setHistogramSelection((current) =>
                              current?.scopeKey === activeScope.key &&
                              current.viewKey === activeView.key &&
                              current.modelKey === model.model_key &&
                              current.lower === bin.lower &&
                              current.upper === bin.upper
                                ? null
                                : {
                                    scopeKey: activeScope.key,
                                    viewKey: activeView.key,
                                    modelKey: model.model_key,
                                    lower: bin.lower,
                                    upper: bin.upper,
                                  },
                            )
                          }
                        >
                          <i
                            style={{
                              height: `${(bin.count / maximum) * 100}%`,
                            }}
                          />
                        </button>
                      );
                    })}
                  </div>
                  <div className="histogram-axis">
                    <span>−100</span>
                    <span>0</span>
                    <span>+100</span>
                  </div>
                </article>
              );
            })}
          </div>
          {activeHistogramSelection && selectedModel && selectedBin && (
            <section className="histogram-examples" aria-live="polite">
              <div className="histogram-examples-heading">
                <div>
                  <span className="histogram-range-badge">
                    {signed(selectedBin.lower)} → {signed(selectedBin.upper)}
                  </span>
                  <div>
                    <p className="eyebrow">Selected gap range</p>
                    <h3>{modelLabel(activeHistogramSelection.modelKey)}</h3>
                    <span>
                      {selectedBin.count} matched{" "}
                      {selectedBin.count === 1 ? "pair" : "pairs"} in this bar ·
                      showing {selectedExamples.length} representative examples
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setHistogramSelection(null)}
                  aria-label="Close histogram examples"
                >
                  Close
                </button>
              </div>
              <div className="histogram-example-list">
                {selectedExamples.map((example) => (
                  <article
                    className="histogram-example"
                    key={`${example.sensitive.batch}-${example.sensitive.prompt_id}`}
                  >
                    <div className="histogram-example-title">
                      <div>
                        <strong>{example.sensitive.concept}</strong>
                        <span>
                          {example.sensitive.concept_id} ·{" "}
                          {pretty(example.sensitive.frame_type)}
                        </span>
                      </div>
                      <b className={example.gap > 0 ? "positive" : ""}>
                        {signed(example.gap)} gap
                      </b>
                    </div>
                    <div className="histogram-example-pair">
                      {(
                        [
                          [
                            "Sensitive",
                            example.sensitive,
                            example.sensitiveCensorship,
                          ],
                          [
                            "Control",
                            example.control,
                            example.controlCensorship,
                          ],
                        ] as const
                      ).map(([condition, prompt, censorship]) => (
                        <section
                          className={`histogram-example-response ${condition.toLowerCase()}`}
                          key={condition}
                        >
                          <header>
                            <strong>{condition}</strong>
                            <span>
                              Censorship <b>{score(censorship)}</b>
                            </span>
                          </header>
                          <div className="histogram-example-text-block">
                            <div className="histogram-example-label">
                              <span>Prompt</span>
                              <code>{prompt.prompt_id}</code>
                            </div>
                            <p
                              className="histogram-example-prompt"
                              title={prompt.prompt}
                            >
                              {prompt.prompt}
                            </p>
                          </div>
                          <div className="histogram-example-text-block response">
                            <div className="histogram-example-label">
                              <span>Response preview</span>
                            </div>
                            <p className="histogram-example-content">
                              {responsePreview(
                                prompt.responses[
                                  activeHistogramSelection.modelKey
                                ]?.content,
                              )}
                            </p>
                          </div>
                        </section>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}
        </section>

        <section className="analytics-card">
          <div className="analytics-heading pairwise-heading">
            <div>
              <p className="eyebrow">Head-to-head</p>
              <h3>Pairwise model comparison</h3>
            </div>
            <div className="metric-tabs" aria-label="Comparison metric">
              {(
                [
                  ["sensitive_minus_control", "Gap"],
                  ["sensitive", "Sensitive"],
                  ["control", "Control"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  className={comparisonMetric === value ? "active" : ""}
                  onClick={() => setComparisonMetric(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="pairwise-grid">
            {comparisons.map((row) => (
              <article
                className="pairwise-card"
                key={`${row.condition}-${row.model_a}-${row.model_b}`}
              >
                <div>
                  <strong>{modelLabel(row.model_a)}</strong>
                  <span>minus</span>
                  <strong>{modelLabel(row.model_b)}</strong>
                </div>
                <b
                  className={(row.difference.mean ?? 0) > 0 ? "positive" : ""}
                >
                  {score(row.difference.mean, true)}
                </b>
                <p>{comparisonSentence(row)}</p>
                <small>
                  n={row.difference.n} · median{" "}
                  {score(row.difference.median, true)}
                </small>
              </article>
            ))}
          </div>
        </section>

        <p className="analytics-footnote">
          LLM-judge proxy scores are diagnostic, not human ground truth. The
          downloadable source artifacts retain response-level scores and every
          matched pair for independent re-analysis. Descriptive methodology and
          analysis seed {statistics.methodology.random_seed} are recorded in the
          canonical JSON; confidence intervals are not yet computed.
        </p>
      </div>
    </section>
  );
}
