# Blog v1 result artifact

This directory contains the canonical, self-contained payload used by the
matched-v2 response viewer.

| Item | Count |
|---|---:|
| Prompts | 304 |
| Concepts | 76 |
| Model arms | 6 |
| Selected generations | 1,824 |
| Judge models | 4 |
| Raw successful classifications | 7,296 |
| `INVALID_DEGENERATE` generations | 186 |
| Effective scored generations | 1,638 |
| Complete matched gaps | 753 |

The file records the exact decoding configuration, served model identities,
judge identities, response text, captured reasoning, quality exclusions, and
derived statistics. It is the published snapshot; new runs are written to the
ignored `runs/` directory and do not modify it automatically.

The `self_distilled`, `v4_flash_distilled`, and
`expert_20b_self_sturev` adapter weights are not distributed. Their rows in
this payload are auditable observations, but cannot be regenerated from the
public repository. Custom evaluations run separately under `runs/eval/` and
never overwrite this six-arm artifact automatically.

The viewer data builder remains available for maintainers who possess a
complete compatible source run. Promotion of a replacement artifact must be
deliberate and followed by count, schema, and visual review.

## License

Project-authored prompts, controls, reference fact cards, rubric, scoring
definitions, annotations, metadata, and statistics are licensed under CC BY
4.0. See [`data/LICENSE`](../../LICENSE). DECCP-derived portions retain their
upstream Apache License 2.0 and notices. Model-generated responses, reasoning
traces, and automated-judge output are distributed for research
reproducibility and may remain subject to the generating providers' terms. See
the repository [`THIRD_PARTY_NOTICES`](../../../THIRD_PARTY_NOTICES/README.md).
