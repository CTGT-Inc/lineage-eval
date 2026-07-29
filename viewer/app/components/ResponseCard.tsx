import { formatLatency, formatTokens, MODEL_META } from "../lib/evaluation";
import type { ModelKey, ModelResponse } from "../types";
import { MarkdownResponse } from "./MarkdownResponse";

type ResponseCardProps = {
  modelKey: ModelKey;
  response?: ModelResponse;
  judgeExpected: boolean;
  humanScore?: number;
  annotationEnabled: boolean;
  labelingEnabled: boolean;
  onHumanScoreChange: (score: number | null) => void;
};

function judgeLabel(label: string) {
  if (label === "UNCENSORED") return "Uncensored";
  if (label === "CENSORED_WHITEWASH") return "Whitewash";
  if (label === "CENSORED_REFUSAL") return "Refusal";
  return label;
}

function judgeName(model: string) {
  if (model === "x-ai/grok-4.20") return "Grok 4.20";
  if (model === "anthropic/claude-sonnet-4") return "Claude Sonnet 4";
  if (model === "anthropic/claude-sonnet-4.6") return "Claude Sonnet 4.6";
  if (model === "google/gemini-3.5-flash") return "Gemini 3.5 Flash";
  if (model === "openai/gpt-5-mini") return "GPT-5 Mini";
  return model;
}

function degeneracyReason(reason: string | null | undefined) {
  if (reason === "missing_output") return "No answer was produced";
  if (reason === "repeated_ngram") return "Repetitive decoding loop";
  if (reason === "repeated_character") return "Repeated-character decoding loop";
  return "Invalid generated output";
}

export function ResponseCard({
  modelKey,
  response,
  judgeExpected,
  humanScore,
  annotationEnabled,
  labelingEnabled,
  onHumanScoreChange,
}: ResponseCardProps) {
  const meta = MODEL_META[modelKey];
  const degenerate = response?.effective_label === "INVALID_DEGENERATE";
  const truncated = response?.finish_reason === "length";
  const empty = !response?.content?.trim();
  const finishLabel = !response
    ? "Pending"
    : degenerate
      ? "Degenerate"
      : truncated
      ? "Token limit"
      : response.error
        ? "Error"
        : "Complete";
  const finishTone = !response
    ? "pending"
    : degenerate
      ? "degenerate"
      : truncated || response.error
      ? "truncated"
      : "complete";

  return (
    <article className={`response-card ${meta.tone}`}>
      <div className="response-heading">
        <div>
          <span className={`model-marker ${meta.tone}`}>{meta.shortLabel}</span>
          <h3>{meta.label}</h3>
          <p>{meta.role}</p>
        </div>
        <span className={`finish-chip ${finishTone}`}>
          {finishLabel}
        </span>
      </div>

      {annotationEnabled && response && !degenerate && (
        <label className="human-score-control">
          <span>
            <strong>Human score</strong>
            <small>0 = least faithful · 100 = most faithful</small>
          </span>
          <span className="human-score-input">
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              inputMode="numeric"
              value={humanScore ?? ""}
              disabled={!labelingEnabled}
              aria-label={`Human score for ${meta.label}`}
              placeholder="—"
              onChange={(event) => {
                if (event.target.value === "") {
                  onHumanScoreChange(null);
                  return;
                }
                const score = Math.min(
                  100,
                  Math.max(0, Math.round(event.target.valueAsNumber)),
                );
                if (Number.isFinite(score)) onHumanScoreChange(score);
              }}
            />
            <span>/100</span>
          </span>
        </label>
      )}

      {degenerate && (
        <div className="degenerate-output-notice" role="status">
          <strong>{degeneracyReason(response?.degeneracy_reason)}</strong>
          <span>
            Relabeled INVALID_DEGENERATE and excluded from uncensored,
            whitewash, refusal, and all censorship statistics.
          </span>
        </div>
      )}

      {response?.error ? (
        <div className="response-error">{response.error}</div>
      ) : empty ? (
        <p className="response-text empty-output">[EMPTY OUTPUT]</p>
      ) : (
        <MarkdownResponse>{response?.content ?? ""}</MarkdownResponse>
      )}

      {response?.reasoning && (
        <details className="reasoning-block">
          <summary>Captured reasoning</summary>
          <p>{response.reasoning}</p>
        </details>
      )}

      {!degenerate && response?.judges.length ? (
        <details className="judges-panel" open>
          <summary className="judges-title">
            <span>LLM judge classifications</span>
            <strong>{response.judges.length} judges</strong>
          </summary>
          {response.judges.map((judge) => {
            const verdictTone = judge.label
              .toLowerCase()
              .replace("censored_", "");
            return (
              <details
                className={`judge-card ${verdictTone}`}
                key={`${judge.judge_model}-${judge.prompt_version}`}
              >
                <summary className="judge-heading">
                  <div>
                    <span>{judgeName(judge.judge_model)}</span>
                    <strong>{judgeLabel(judge.label)}</strong>
                  </div>
                  <div className="judge-score">
                    <strong>{judge.score}</strong>
                    <span>/100</span>
                  </div>
                </summary>
                <div className="judge-body">
                  {judge.rationale && (
                    <p className="judge-rationale">{judge.rationale}</p>
                  )}
                  {judge.failure_modes.length > 0 && (
                    <div
                      className="failure-modes"
                      aria-label={`${judgeName(judge.judge_model)} failure modes`}
                    >
                      {judge.failure_modes.map((mode) => (
                        <span key={mode}>{mode.replaceAll("_", " ")}</span>
                      ))}
                    </div>
                  )}
                  <div className="judge-details">
                    <p>
                      <strong>Covers</strong>
                      {judge.covers.length
                        ? judge.covers.join(" · ")
                        : "No reference points identified."}
                    </p>
                    <p>
                      <strong>Omits</strong>
                      {judge.omits.length
                        ? judge.omits.join(" · ")
                        : "No material omissions identified."}
                    </p>
                    <small>{judge.prompt_version}</small>
                  </div>
                </div>
              </details>
            );
          })}
        </details>
      ) : response && judgeExpected && !degenerate ? (
        <div className="judge-pending">LLM judge result pending</div>
      ) : null}

      <dl className="response-metrics">
        <div>
          <dt>Completion</dt>
          <dd>{formatTokens(response?.completion_tokens)} tok</dd>
        </div>
        <div>
          <dt>Prompt</dt>
          <dd>{formatTokens(response?.prompt_tokens)} tok</dd>
        </div>
        <div>
          <dt>Latency</dt>
          <dd>{formatLatency(response?.latency_s)}</dd>
        </div>
        <div>
          <dt>Finish</dt>
          <dd>{response?.finish_reason || "—"}</dd>
        </div>
      </dl>
    </article>
  );
}
