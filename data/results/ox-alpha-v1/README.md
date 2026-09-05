# GLM 5.3 (Ox Alpha) responses

`responses.jsonl` contains 152 core-political matched-v2 responses served from
the `stealth/ox-alpha` endpoint. Every row uses exactly the same normalized
field set and field order as the other files under
`release/huggingface/blog-v1/data/responses/`. The production release copy is
`release/huggingface/blog-v1/data/responses/ox-alpha.jsonl`.

The downloadable label is **GLM 5.3 (Ox Alpha)** and the stable machine key is
`ox_alpha`. The endpoint's underlying model provenance was undisclosed, so the
label is not represented as a verified identity or lineage claim.

The source generation log is pinned to
`CTGT-Inc/research-censorship-distillation@0d61229f760521ff169f76740f154471fcda8ea7`,
under `experiments/026_card_standard_rejudge/data/ox_alpha_responses.jsonl`.
There are 151 valid responses and one `INVALID_DEGENERATE` response.
