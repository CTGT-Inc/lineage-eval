# Ox Alpha comparator release

This immutable supplemental release carries the Ox Alpha arm from
`CTGT-Inc/research-censorship-distillation/experiments/026_card_standard_rejudge`
into the canonical Lineage Eval repository.

- `matched-v2-core-political.json` contains all 152 generations, the 604
  successful `cards_v1` judgments, exact run settings, source hashes, and the
  published `cards_v1` and `cards_v2` summary estimates.
- `matched_gaps_cards_v1.csv` and `matched_gaps_cards_v2.csv` contain the 75
  complete Ox Alpha pairs for each reference-card variant.

Ox Alpha was served as `stealth/ox-alpha`. Its underlying model provenance was
undisclosed. It is included only as a comparator and carries no model identity
or lineage claim. The endpoint did not support `seed`, so the observations are
auditable but not deterministically reproducible.

The canonical site read is the four-judge `cards_v1` panel: **+7.42** matched
censorship points (95% CI **[+2.63, +12.76]**, 75 pairs). The `cards_v2`
re-judge is retained as a secondary result: **+6.05** (95% CI
**[+1.45, +11.16]**, 75 pairs).

Maintainers with the private experiment checkout can rebuild this directory:

```bash
node tools/import_ox_alpha_release.mjs \
  ../research-censorship-distillation/experiments/026_card_standard_rejudge

node tools/export_ox_alpha_release.mjs
```
