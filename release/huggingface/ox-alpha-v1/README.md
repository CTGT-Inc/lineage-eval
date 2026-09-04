---
pretty_name: Lineage Eval — Ox Alpha comparator
language:
  - en
license: other
task_categories:
  - text-generation
tags:
  - evaluation
  - llm
  - censorship
  - model-comparison
configs:
  - config_name: benchmark
    default: true
    data_files:
      - split: evaluation
        path: data/benchmark/*.jsonl
  - config_name: responses
    data_files:
      - split: evaluation
        path: data/responses/*.jsonl
  - config_name: judgments
    data_files:
      - split: evaluation
        path: data/judgments/*.jsonl
---

# Lineage Eval — Ox Alpha comparator

This release contains the existing Ox Alpha observations from
`experiments/026_card_standard_rejudge`: 152 core-political matched-v2 prompts,
152 generations, and four `cards_v1` judgments for each of the 151 valid
responses. One length-finished control response is marked
`INVALID_DEGENERATE`, leaving 75 complete matched pairs.

Ox Alpha is a comparator. It was served as `stealth/ox-alpha`; its underlying
model provenance was undisclosed, and this release makes no identity or
lineage claim. The endpoint did not support `seed`, so these observations are
not deterministically reproducible.

The four-judge `cards_v1` matched censorship gap is **+7.42** points (95% CI
**[+2.63, +12.76]**, 75 pairs). The separately retained `cards_v2` re-judge is
**+6.05** (95% CI **[+1.45, +11.16]**, 75 pairs). Pair-level CSVs for both
reference-card variants are under `analysis/`; the released judgment rows are
the `cards_v1` panel used by the public site.

Join `benchmark` to `responses` with `prompt_id`, and `responses` to
`judgments` with `response_id`. `MANIFEST.json` records row counts and SHA-256
checksums for every packaged file.

Reference cards are model-drafted research proxies, not independently
fact-checked ground truth. Model responses and automated judgments may contain
errors, fabricated citations, bias, or disturbing political content. Inspect
the prompt, response, quality flag, and individual rationales before drawing
conclusions.

Project-authored data is licensed under CC BY 4.0; repository code and
documentation are Apache-2.0. The core-political benchmark retains the
applicable DECCP Apache-2.0 notice under `THIRD_PARTY_NOTICES/`. Model-generated
content is provided for research reproducibility and may remain subject to the
generating provider's terms.
