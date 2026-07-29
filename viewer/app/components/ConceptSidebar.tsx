import type { EvaluationPrompt } from "../types";

type ConceptSidebarProps = {
  prompts: EvaluationPrompt[];
  selectedConcept: string;
  onSelect: (conceptId: string) => void;
};

export function ConceptSidebar({
  prompts,
  selectedConcept,
  onSelect,
}: ConceptSidebarProps) {
  const concepts = new Map<
    string,
    { label: string; promptCount: number; truncated: number }
  >();

  for (const prompt of prompts) {
    const current = concepts.get(prompt.concept_id) ?? {
      label: prompt.concept,
      promptCount: 0,
      truncated: 0,
    };
    current.promptCount += 1;
    current.truncated += Object.values(prompt.responses).filter(
      (response) => response.finish_reason === "length",
    ).length;
    concepts.set(prompt.concept_id, current);
  }

  const totalTruncated = prompts.reduce(
    (count, prompt) =>
      count +
      Object.values(prompt.responses).filter(
        (response) => response.finish_reason === "length",
      ).length,
    0,
  );

  return (
    <aside className="concept-panel" aria-label="Concepts">
      <div className="panel-heading">
        <span>Concepts</span>
        <span>prompts / cuts</span>
      </div>
      <button
        className={`concept-row ${selectedConcept === "all" ? "active" : ""}`}
        onClick={() => onSelect("all")}
      >
        <span>
          <strong>All concepts</strong>
          <small>Full matched-v2 sweep</small>
        </span>
        <span className="concept-count">
          {prompts.length} / {totalTruncated}
        </span>
      </button>
      {[...concepts.entries()].map(([id, concept]) => (
        <button
          key={id}
          className={`concept-row ${selectedConcept === id ? "active" : ""}`}
          onClick={() => onSelect(id)}
        >
          <span>
            <strong>{id}</strong>
            <small>{concept.label}</small>
          </span>
          <span className="concept-count">
            {concept.promptCount} / {concept.truncated}
          </span>
        </button>
      ))}
      <div className="legend-note">
        “Cuts” are responses stopped at the 4,096-token completion limit.
      </div>
    </aside>
  );
}
