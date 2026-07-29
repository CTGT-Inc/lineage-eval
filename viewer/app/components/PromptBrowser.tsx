import type { RefObject } from "react";
import { pretty } from "../lib/evaluation";
import { MODEL_KEYS, type EvaluationPrompt, type Tier } from "../types";

type PromptBrowserProps = {
  prompts: EvaluationPrompt[];
  selectedId: string;
  selectedIndex: number;
  query: string;
  tier: "all" | Tier;
  frame: string;
  condition: "all" | "sensitive" | "control";
  searchRef: RefObject<HTMLInputElement | null>;
  onQueryChange: (value: string) => void;
  onTierChange: (value: "all" | Tier) => void;
  onFrameChange: (value: string) => void;
  onConditionChange: (value: "all" | "sensitive" | "control") => void;
  onSelect: (id: string) => void;
};

export function PromptBrowser({
  prompts,
  selectedId,
  selectedIndex,
  query,
  tier,
  frame,
  condition,
  searchRef,
  onQueryChange,
  onTierChange,
  onFrameChange,
  onConditionChange,
  onSelect,
}: PromptBrowserProps) {
  return (
    <section className="browser-panel" aria-label="Matched-v2 prompts">
      <div className="filters">
        <label className="search-box">
          <span aria-hidden="true">⌕</span>
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search prompts and responses…"
            aria-label="Search experiment responses"
          />
          <kbd>/</kbd>
        </label>
        <div className="filter-row">
          <select
            value={condition}
            onChange={(event) =>
              onConditionChange(
                event.target.value as "all" | "sensitive" | "control",
              )
            }
            aria-label="Filter by condition"
          >
            <option value="all">Both conditions</option>
            <option value="sensitive">Sensitive</option>
            <option value="control">Control</option>
          </select>
          <select
            value={frame}
            onChange={(event) => onFrameChange(event.target.value)}
            aria-label="Filter by frame"
          >
            <option value="all">Both frames</option>
            <option value="f1">F1 · factual</option>
            <option value="f2">F2 · advisory</option>
          </select>
          <select
            value={tier}
            onChange={(event) =>
              onTierChange(event.target.value as "all" | Tier)
            }
            aria-label="Filter by sensitivity tier"
          >
            <option value="all">All tiers</option>
            <option value="high">High tier</option>
            <option value="mid">Mid tier</option>
            <option value="low">Low tier</option>
          </select>
        </div>
      </div>
      <div className="result-summary">
        <span>{prompts.length.toLocaleString()} prompts</span>
        {selectedIndex >= 0 && (
          <span>
            {selectedIndex + 1} of {prompts.length}
          </span>
        )}
      </div>
      <div className="prompt-list">
        {!prompts.length && (
          <div className="empty-state">No prompts match these filters.</div>
        )}
        {prompts.map((prompt) => {
          const responseCount = Object.keys(prompt.responses).length;
          const truncated = Object.values(prompt.responses).filter(
            (response) => response.finish_reason === "length",
          ).length;
          return (
            <button
              key={prompt.prompt_id}
              data-prompt-id={prompt.prompt_id}
              className={`prompt-row ${
                selectedId === prompt.prompt_id ? "active" : ""
              }`}
              onClick={() => onSelect(prompt.prompt_id)}
            >
              <div className="prompt-row-top">
                <span className={`condition-chip ${prompt.condition}`}>
                  {prompt.condition}
                </span>
                <span>
                  {prompt.frame.toUpperCase()} · {prompt.tier}
                </span>
              </div>
              <strong>{prompt.prompt}</strong>
              <p>{prompt.concept}</p>
              <div className="prompt-row-footer">
                <code>{prompt.prompt_id}</code>
                {truncated > 0 && (
                  <span className="truncation-count">
                    {truncated} {truncated === 1 ? "cut" : "cuts"}
                  </span>
                )}
                {responseCount < MODEL_KEYS.length && (
                  <span className="pending-count">
                    {responseCount === 0
                      ? "no outputs"
                      : `${responseCount}/${MODEL_KEYS.length} outputs`}
                  </span>
                )}
                <span>{pretty(prompt.frame_type)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
