Model-generated responses are provided for research reproducibility and may remain subject to the generating providers' terms.

# Data

This release separates benchmark inputs from observed results:

```text
benchmark/       Immutable matched-v2 prompt records
results/blog-v1/ Released prompts, generations, judgments, run metadata, and statistics
results/ox-alpha-v1/ Supplemental core-political Ox Alpha comparator release
annotations/     Optional local human-label work; not used as headline ground truth
```

## Benchmark

`matched_v2` contains 76 concepts across two topical strata:

| File | Stratum | Concepts | Prompts |
|---|---|---:|---:|
| `benchmark/finance_prompts.jsonl` | `finance_adjacent` | 38 | 152 |
| `benchmark/core_political_matched_prompts.jsonl` | `core_political` | 38 | 152 |

Each concept has two frames and a politically sensitive / difficulty-matched
control pair. Every JSONL record carries:

| Field | Meaning |
|---|---|
| `benchmark_version` | Immutable dataset identity, currently `matched_v2`. |
| `prompt_id` | Stable response/judgment join key. |
| `concept_id` | Clustering unit; split by concept, never individual prompt. |
| `stratum` | `finance_adjacent` or `core_political`. |
| `tier` | Expected sensitivity gradient: `high`, `mid`, or `low`. |
| `frame` | One of two task frames for the concept. |
| `condition` | `sensitive` or `control`. |
| `control_entity` | Named entity substituted in the matched control. |
| `prompt` | User message sent to the model. |
| `must_engage` | Reference fact card used by the judge rubric. |

Benchmark files are immutable after use. A text or membership change requires a
new `benchmark_version` and new filename. Prompt IDs may be carried into a new
version only when the prompt is unchanged.

## Released results

`results/blog-v1/matched-v2-full-data.json` is the canonical browser-oriented
release. It includes all 304 prompts, 1,824 selected successful generations,
the four judgments for each response, run metadata, response-quality labels,
and descriptive statistics.

It is intentionally a single JSON document so the viewer can run without a
database. Treat `prompts[].responses` as observations, not benchmark inputs.
The Python generation and judge runners use append-only JSONL under `runs/`;
`viewer/scripts/build-data.mjs` selects the final successful attempt and builds
this release document.

Empty outputs and mechanically detected decoding loops receive
`INVALID_DEGENERATE`. They have no effective censorship score; any matched pair
containing one is omitted from gap statistics. Raw judge classifications remain
in the release for provenance and are marked as excluded.

## Provenance and limitations

The finance-adjacent benchmark is original. The political inventory is a
substantially modified derivative of the Apache-2.0-licensed
[`augmxnt/deccp`](https://github.com/AUGMXNT/deccp) prompt inventory. Because
the exact row-level source map was not retained, the full core-political
stratum is conservatively treated as derived material. See the repository
`NOTICE` for the pinned upstream revision and modification statement.

`must_engage` cards and LLM-judge labels are research proxies. They should be
human-validated before being treated as general-purpose ground truth. The
release includes model reasoning traces because the source models returned them
through vLLM's reasoning parsers; downstream users who do not need them should
drop the `reasoning` field.

The released results include three trained adapter arms whose weights are not
distributed: `self_distilled`, `v4_flash_distilled`, and
`expert_20b_self_sturev`. Their response and judgment records are available for
inspection, but those generations cannot be independently regenerated from the
public repository. The runnable harness is model-agnostic and evaluates any
model name exposed by a user-managed OpenAI-compatible endpoint.

The supplemental `ox-alpha-v1` release contains the existing 152
core-political generations from the `stealth/ox-alpha` endpoint and the four-
judge `cards_v1` panel used by the standalone site. Its underlying model
provenance was undisclosed; it is a comparator with no identity or lineage
claim. Its endpoint did not support `seed`, so the observations are not
deterministically reproducible. The `cards_v2` matched-gap results are retained
as a secondary analysis, without substituting them for the site-facing
`cards_v1` judgments.

## License

Project-authored data—including the 304 prompts, matched controls, reference
fact cards, rubric, annotations, scoring definitions, metadata, and
statistics—is licensed under Creative Commons Attribution 4.0 International
(CC BY 4.0). See [`LICENSE`](LICENSE).

This grant applies only to material the project has authority to license. The
DECCP-derived portions of the core-political stratum remain under the upstream
Apache License 2.0; they are not relicensed. The exact pinned upstream license,
source revision, and modification statement are in
[`THIRD_PARTY_NOTICES`](../THIRD_PARTY_NOTICES/README.md).

Model-generated responses, reasoning traces, and automated-judge output are
distributed for research reproducibility and may remain subject to the terms of
the providers that generated them.
