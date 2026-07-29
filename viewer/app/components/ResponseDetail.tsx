import { MODEL_META, pairId, pretty } from "../lib/evaluation";
import {
  humanLabelKey,
  type HumanScoreMap,
} from "../lib/humanLabels";
import { MODEL_KEYS, type EvaluationPrompt, type ModelKey } from "../types";
import { ResponseCard } from "./ResponseCard";

type ResponseDetailProps = {
  selected: EvaluationPrompt | null;
  selectedIndex: number;
  total: number;
  availableIds: Set<string>;
  onMove: (delta: number) => void;
  onSelect: (id: string) => void;
  visibleModelKeys: ModelKey[];
  onToggleModel: (modelKey: ModelKey) => void;
  onShowAllModels: () => void;
  humanScores: HumanScoreMap;
  annotationEnabled: boolean;
  labelingEnabled: boolean;
  onHumanScoreChange: (key: string, score: number | null) => void;
};

export function ResponseDetail({
  selected,
  selectedIndex,
  total,
  availableIds,
  onMove,
  onSelect,
  visibleModelKeys,
  onToggleModel,
  onShowAllModels,
  humanScores,
  annotationEnabled,
  labelingEnabled,
  onHumanScoreChange,
}: ResponseDetailProps) {
  if (!selected) {
    return (
      <section className="detail-panel">
        <div className="detail-empty">Select a prompt to inspect it.</div>
      </section>
    );
  }

  const counterpart =
    selected.condition === "sensitive"
      ? pairId(selected, "control")
      : pairId(selected, "sensitive");
  const counterpartAvailable = availableIds.has(counterpart);

  return (
    <section className="detail-panel" aria-live="polite">
      <div className="detail-header">
        <div>
          <div className="detail-kicker">
            <span>{selected.concept_id}</span>
            <span>·</span>
            <span>{pretty(selected.condition)}</span>
            <span>·</span>
            <span>{selected.tier} tier</span>
          </div>
          <h2>{selected.concept}</h2>
        </div>
        <div className="detail-actions">
          <button
            className="pair-jump"
            onClick={() => onSelect(counterpart)}
            disabled={!counterpartAvailable}
          >
            View {selected.condition === "sensitive" ? "control" : "sensitive"} pair
          </button>
          <div className="pager" aria-label="Prompt navigation">
            <button
              onClick={() => onMove(-1)}
              disabled={selectedIndex <= 0}
              aria-label="Previous prompt"
            >
              ↑
            </button>
            <button
              onClick={() => onMove(1)}
              disabled={selectedIndex >= total - 1}
              aria-label="Next prompt"
            >
              ↓
            </button>
          </div>
        </div>
      </div>

      <div className="detail-scroll">
        <section className="prompt-card">
          <div className="block-label">
            <span>Benchmark prompt</span>
            <code>{selected.prompt_id}</code>
          </div>
          <p>{selected.prompt}</p>
          <div className="prompt-metadata">
            <span>{pretty(selected.frame_type)}</span>
            <span>{selected.condition}</span>
            <span>{selected.tier} sensitivity</span>
            {selected.control_entity && <span>{selected.control_entity}</span>}
          </div>
        </section>

        {selected.must_engage && (
          <details className="must-engage" open>
            <summary className="block-label">
              <span>Must-engage fact card</span>
              <span>draft benchmark guidance</span>
              <span className="must-engage-toggle" aria-hidden="true">
                <span className="when-open">Collapse</span>
                <span className="when-closed">Expand</span>
              </span>
            </summary>
            <div className="must-engage-content">
              <p>{selected.must_engage}</p>
            </div>
          </details>
        )}

        <div className="comparison-heading">
          <div>
            <p className="eyebrow">
              Same prompt, {visibleModelKeys.length}{" "}
              {visibleModelKeys.length === 1 ? "arm" : "arms"}
            </p>
            <h3>Generated responses</h3>
          </div>
          <div className="comparison-controls">
            <span>120B lineage · Flash teacher · 20B base · expert 20B LoRA</span>
            <details className="model-picker">
              <summary
                aria-label={`Choose model columns. ${visibleModelKeys.length} of ${MODEL_KEYS.length} selected.`}
              >
                Models
                <strong>
                  {visibleModelKeys.length}/{MODEL_KEYS.length}
                </strong>
              </summary>
              <div className="model-picker-menu">
                <div className="model-picker-header">
                  <span>Columns to display</span>
                  <button
                    type="button"
                    onClick={onShowAllModels}
                    disabled={visibleModelKeys.length === MODEL_KEYS.length}
                  >
                    Show all
                  </button>
                </div>
                {MODEL_KEYS.map((modelKey) => {
                  const checked = visibleModelKeys.includes(modelKey);
                  const meta = MODEL_META[modelKey];
                  return (
                    <label className="model-picker-option" key={modelKey}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={checked && visibleModelKeys.length === 1}
                        onChange={() => onToggleModel(modelKey)}
                      />
                      <span className={`model-picker-swatch ${meta.tone}`} />
                      <span>
                        <strong>{meta.label}</strong>
                        <small>{meta.role}</small>
                      </span>
                    </label>
                  );
                })}
                <p>Keep at least one model visible.</p>
              </div>
            </details>
          </div>
        </div>

        <section className="response-grid">
          {visibleModelKeys.map((modelKey) => {
            const labelKey = humanLabelKey(
              selected.batch,
              selected.prompt_id,
              modelKey,
            );
            return (
              <ResponseCard
                key={modelKey}
                modelKey={modelKey}
                response={selected.responses[modelKey]}
                judgeExpected={Boolean(selected.must_engage)}
                humanScore={humanScores[labelKey]}
                annotationEnabled={annotationEnabled}
                labelingEnabled={labelingEnabled}
                onHumanScoreChange={(score) =>
                  onHumanScoreChange(labelKey, score)
                }
              />
            );
          })}
        </section>
      </div>
    </section>
  );
}
