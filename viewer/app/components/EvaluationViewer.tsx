"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  labelsToScoreMap,
  scoreMapsEqual,
  scoreMapToLabels,
  type HumanScoreMap,
} from "../lib/humanLabels";
import { promptSearchText } from "../lib/evaluation";
import { MODEL_KEYS } from "../types";
import type {
  HumanLabelsApiResponse,
  ModelKey,
  EvaluationBatch,
  EvaluationDataset,
  Tier,
} from "../types";
import { ConceptSidebar } from "./ConceptSidebar";
import { PromptBrowser } from "./PromptBrowser";
import { ResponseDetail } from "./ResponseDetail";
import { StatisticsView } from "./StatisticsView";

const ANNOTATION_ENABLED =
  process.env.NEXT_PUBLIC_ENABLE_ANNOTATION === "true";

export function EvaluationViewer() {
  const [dataset, setDataset] = useState<EvaluationDataset | null>(null);
  const [loadError, setLoadError] = useState("");
  const [humanScores, setHumanScores] = useState<HumanScoreMap>({});
  const [savedHumanScores, setSavedHumanScores] = useState<HumanScoreMap>({});
  const [labelRevision, setLabelRevision] = useState("");
  const [labelsLoading, setLabelsLoading] = useState(ANNOTATION_ENABLED);
  const [labelError, setLabelError] = useState("");
  const [savingLabels, setSavingLabels] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [batch, setBatch] = useState<EvaluationBatch>("finance");
  const [concept, setConcept] = useState("all");
  const [condition, setCondition] = useState<
    "all" | "sensitive" | "control"
  >("all");
  const [tier, setTier] = useState<"all" | Tier>("all");
  const [frame, setFrame] = useState("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [view, setView] = useState<"responses" | "statistics">("responses");
  const [statisticsScopeKey, setStatisticsScopeKey] = useState("all");
  const [statisticsViewKey, setStatisticsViewKey] = useState("mean");
  const [visibleModelKeys, setVisibleModelKeys] = useState<ModelKey[]>([
    ...MODEL_KEYS,
  ]);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const response = await fetch("/matched-v2-full-data.json", {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Could not load experiment data (${response.status})`);
        }
        const data = (await response.json()) as EvaluationDataset;
        setDataset(data);
        setSelectedId(
          data.prompts.find((prompt) => prompt.batch === "finance")?.prompt_id ??
            "",
        );

        if (ANNOTATION_ENABLED) {
          try {
          const labelsResponse = await fetch("/api/human-labels", {
            cache: "no-store",
            signal: controller.signal,
          });
          const labelsPayload = (await labelsResponse.json()) as
            | HumanLabelsApiResponse
            | { error?: string };
          if (!labelsResponse.ok) {
            throw new Error(
              "error" in labelsPayload && labelsPayload.error
                ? labelsPayload.error
                : `Could not load human labels (${labelsResponse.status})`,
            );
          }
          const humanLabels = labelsPayload as HumanLabelsApiResponse;
          if (humanLabels.experiment !== data.run.experiment) {
            throw new Error(
              `Human labels belong to ${humanLabels.experiment}, not ${data.run.experiment}.`,
            );
          }
          const loadedScores = labelsToScoreMap(humanLabels.labels);
          setHumanScores(loadedScores);
          setSavedHumanScores(loadedScores);
          setLabelRevision(humanLabels.revision);
          setLastSavedAt(humanLabels.saved_at_utc);
          } catch (error) {
            if (error instanceof Error && error.name !== "AbortError") {
              setLabelError(error.message);
            }
          } finally {
            setLabelsLoading(false);
          }
        }
      } catch (error) {
        if (error instanceof Error && error.name !== "AbortError") {
          setLoadError(error.message);
        }
        setLabelsLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, []);

  const labelsDirty = useMemo(
    () =>
      ANNOTATION_ENABLED &&
      !scoreMapsEqual(humanScores, savedHumanScores),
    [humanScores, savedHumanScores],
  );

  useEffect(() => {
    if (!labelsDirty) return;
    function warnBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [labelsDirty]);

  const batchPrompts = useMemo(
    () => (dataset?.prompts ?? []).filter((prompt) => prompt.batch === batch),
    [batch, dataset],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return batchPrompts.filter(
      (prompt) =>
        (concept === "all" || prompt.concept_id === concept) &&
        (condition === "all" || prompt.condition === condition) &&
        (tier === "all" || prompt.tier === tier) &&
        (frame === "all" || prompt.frame === frame) &&
        (!needle || promptSearchText(prompt).includes(needle)),
    );
  }, [batchPrompts, concept, condition, frame, query, tier]);

  const selected =
    filtered.find((prompt) => prompt.prompt_id === selectedId) ??
    filtered[0] ??
    null;
  const selectedIndex = selected
    ? filtered.findIndex((prompt) => prompt.prompt_id === selected.prompt_id)
    : -1;
  const availableIds = useMemo(
    () => new Set(filtered.map((prompt) => prompt.prompt_id)),
    [filtered],
  );

  function moveSelection(delta: number) {
    if (!filtered.length) return;
    const index = selectedIndex < 0 ? 0 : selectedIndex;
    const nextIndex = Math.min(
      filtered.length - 1,
      Math.max(0, index + delta),
    );
    const next = filtered[nextIndex];
    setSelectedId(next.prompt_id);
    document
      .querySelector(`[data-prompt-id="${next.prompt_id}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      const typing =
        target.tagName === "INPUT" ||
        target.tagName === "SELECT" ||
        target.tagName === "TEXTAREA";
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === "ArrowDown" && !typing) {
        event.preventDefault();
        moveSelection(1);
      } else if (event.key === "ArrowUp" && !typing) {
        event.preventDefault();
        moveSelection(-1);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  });

  const responseCount = (dataset?.prompts ?? []).reduce(
    (count, prompt) => count + Object.keys(prompt.responses).length,
    0,
  );
  const expectedResponseCount =
    (dataset?.prompts.length ?? 0) * (dataset?.models.length ?? 0);
  const humanScoreCount = Object.keys(humanScores).length;
  const batchResponseCount = batchPrompts.reduce(
    (count, prompt) => count + Object.keys(prompt.responses).length,
    0,
  );
  const expectedBatchResponseCount =
    batchPrompts.length * (dataset?.models.length ?? 0);
  const conceptCount = new Set(batchPrompts.map((prompt) => prompt.concept_id))
    .size;
  const truncatedCount = batchPrompts.reduce(
    (count, prompt) =>
      count +
      Object.values(prompt.responses).filter(
        (response) => response.finish_reason === "length",
      ).length,
    0,
  );
  const classificationCount = batchPrompts.reduce(
    (count, prompt) =>
      count +
      Object.values(prompt.responses).reduce(
        (responseCount, response) =>
          responseCount + response.judges.length,
        0,
      ),
    0,
  );
  const activeRun = dataset?.runs.find((run) => run.batch === batch);
  const activeStatisticsScope =
    dataset?.statistics.analysis_scopes?.find(
      (scope) => scope.key === statisticsScopeKey,
    ) ?? dataset?.statistics.analysis_scopes?.[0];
  const statisticsViews =
    activeStatisticsScope?.analysis_views ??
    dataset?.statistics.analysis_views ??
    [];
  const activeStatisticsView =
    statisticsViews.find(
      (analysisView) => analysisView.key === statisticsViewKey,
    ) ?? statisticsViews[0];

  function selectBatch(nextBatch: EvaluationBatch) {
    if (!dataset || nextBatch === batch) return;
    setBatch(nextBatch);
    setConcept("all");
    setSelectedId(
      dataset.prompts.find((prompt) => prompt.batch === nextBatch)?.prompt_id ??
        "",
    );
  }

  function toggleModelVisibility(modelKey: ModelKey) {
    setVisibleModelKeys((current) => {
      if (current.includes(modelKey)) {
        return current.length === 1
          ? current
          : current.filter((key) => key !== modelKey);
      }

      return MODEL_KEYS.filter(
        (key) => key === modelKey || current.includes(key),
      );
    });
  }

  async function saveHumanLabels() {
    if (!dataset || !labelRevision || !labelsDirty || savingLabels) return;
    setSavingLabels(true);
    setLabelError("");

    try {
      const response = await fetch("/api/human-labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_revision: labelRevision,
          labels: scoreMapToLabels(dataset, humanScores),
        }),
      });
      const payload = (await response.json()) as
        | HumanLabelsApiResponse
        | { error?: string };
      if (!response.ok) {
        throw new Error(
          "error" in payload && payload.error
            ? payload.error
            : `Could not save human labels (${response.status})`,
        );
      }

      const saved = payload as HumanLabelsApiResponse;
      const savedScores = labelsToScoreMap(saved.labels);
      setHumanScores(savedScores);
      setSavedHumanScores(savedScores);
      setLabelRevision(saved.revision);
      setLastSavedAt(saved.saved_at_utc);
    } catch (error) {
      setLabelError(
        error instanceof Error
          ? error.message
          : "Could not save human labels.",
      );
    } finally {
      setSavingLabels(false);
    }
  }

  if (!dataset) {
    return (
      <main className="loading-screen">
        <div>
          <p className="eyebrow">Experiment 004 · full matched-v2 sweep</p>
          <h1>Matched-v2 response viewer</h1>
          <p className={loadError ? "load-error" : ""}>
            {loadError || "Loading model responses…"}
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="titlegroup">
          <p className="eyebrow">Experiment 004 · Flash teacher-forward</p>
          <h1>Matched-v2 response viewer</h1>
          <nav className="view-tabs" aria-label="Viewer section">
            <button
              className={view === "responses" ? "active" : ""}
              onClick={() => setView("responses")}
              aria-pressed={view === "responses"}
            >
              Responses
            </button>
            <button
              className={view === "statistics" ? "active" : ""}
              onClick={() => setView("statistics")}
              aria-pressed={view === "statistics"}
            >
              Statistics
            </button>
          </nav>
          {view === "responses" && (
            <nav className="dataset-tabs" aria-label="Matched-v2 prompt set">
            <button
              className={batch === "finance" ? "active" : ""}
              onClick={() => selectBatch("finance")}
              aria-pressed={batch === "finance"}
            >
              Finance-adjacent
              <span>
                {
                  dataset.prompts.filter(
                    (prompt) => prompt.batch === "finance",
                  ).length
                }
              </span>
            </button>
            <button
              className={batch === "core_political" ? "active" : ""}
              onClick={() => selectBatch("core_political")}
              aria-pressed={batch === "core_political"}
            >
              Core political
              <span>
                {
                  dataset.prompts.filter(
                    (prompt) => prompt.batch === "core_political",
                  ).length
                }
              </span>
            </button>
            </nav>
          )}
        </div>
        <div className="topbar-side">
          <div className="run-stats" aria-label="Experiment summary">
            {view === "responses" ? (
              <>
          <span>
            <strong>{batchResponseCount}</strong> / {expectedBatchResponseCount} responses
          </span>
          <span>
            <strong>{batchPrompts.length}</strong> prompts
          </span>
          <span>
            <strong>{conceptCount}</strong> concepts
          </span>
          <span className="warning-stat">
            <strong>{truncatedCount}</strong> token-limit cuts
          </span>
          <span className="judge-stat">
            <strong>{dataset.judges.length}</strong> judges
          </span>
          <span className="judge-stat">
            <strong>
              {classificationCount}
            </strong>{" "}
            classifications
          </span>
          <span>
            <strong>${(activeRun?.total_usd_gpu_compute ?? 0).toFixed(2)}</strong>{" "}
            GPU
          </span>
          <span className="all-runs-stat">
            {responseCount} / {expectedResponseCount} responses total
          </span>
              </>
            ) : (
              <>
              <span>
                <strong>{dataset.statistics.models.length}</strong> models
              </span>
              <span className="judge-stat">
                <strong>
                  {activeStatisticsView?.coverage.successful_classifications ??
                    dataset.statistics.coverage.successful_classifications}
                </strong>{" "}
                classifications
              </span>
              <span>
                <strong>
                  {activeStatisticsView?.coverage.response_scores ??
                    dataset.statistics.coverage.response_scores}
                </strong>{" "}
                scored responses
              </span>
              <span className="all-runs-stat">
                {activeStatisticsScope?.label ?? "All prompts"} ·{" "}
                {activeStatisticsView?.judge_model
                  ? "single-judge scores"
                  : activeStatisticsView?.label ??
                    `Mean of ${dataset.statistics.judges.length} judges`}
              </span>
              </>
            )}
          </div>
          {ANNOTATION_ENABLED && (
            <div
              className={`label-save ${
                labelError ? "error" : labelsDirty ? "dirty" : "saved"
              }`}
            >
            <span className="label-save-status" role="status" title={labelError}>
              {labelsLoading
                ? "Loading human labels…"
                : labelError
                  ? labelError
                  : savingLabels
                    ? "Saving all human scores…"
                    : labelsDirty
                      ? `${humanScoreCount} scored · Unsaved changes`
                      : `${humanScoreCount} / ${responseCount} human scores${
                          lastSavedAt ? " · Saved" : ""
                        }`}
            </span>
            <button
              type="button"
              onClick={() => void saveHumanLabels()}
              disabled={
                labelsLoading ||
                Boolean(labelError && !labelRevision) ||
                !labelsDirty ||
                savingLabels
              }
            >
              {savingLabels ? "Saving…" : "Save labels"}
            </button>
            </div>
          )}
        </div>
      </header>

      {view === "responses" ? (
        <section className="workspace">
        <ConceptSidebar
          prompts={batchPrompts}
          selectedConcept={concept}
          onSelect={setConcept}
        />
        <PromptBrowser
          prompts={filtered}
          selectedId={selected?.prompt_id ?? ""}
          selectedIndex={selectedIndex}
          query={query}
          tier={tier}
          frame={frame}
          condition={condition}
          searchRef={searchRef}
          onQueryChange={setQuery}
          onTierChange={setTier}
          onFrameChange={setFrame}
          onConditionChange={setCondition}
          onSelect={setSelectedId}
        />
        <ResponseDetail
          selected={selected}
          selectedIndex={selectedIndex}
          total={filtered.length}
          availableIds={availableIds}
          onMove={moveSelection}
          onSelect={setSelectedId}
          visibleModelKeys={visibleModelKeys}
          onToggleModel={toggleModelVisibility}
          onShowAllModels={() => setVisibleModelKeys([...MODEL_KEYS])}
          humanScores={humanScores}
          annotationEnabled={ANNOTATION_ENABLED}
          labelingEnabled={
            ANNOTATION_ENABLED &&
            Boolean(labelRevision) &&
            !savingLabels &&
            !labelsLoading
          }
          onHumanScoreChange={(key, score) =>
            setHumanScores((current) => {
              const next = { ...current };
              if (score === null) {
                delete next[key];
              } else {
                next[key] = score;
              }
              return next;
            })
          }
        />
        </section>
      ) : (
        <StatisticsView
          statistics={dataset.statistics}
          prompts={dataset.prompts}
          selectedScopeKey={statisticsScopeKey}
          onScopeChange={setStatisticsScopeKey}
          selectedViewKey={statisticsViewKey}
          onViewChange={setStatisticsViewKey}
        />
      )}
    </main>
  );
}
