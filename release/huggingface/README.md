# Hugging Face release

`blog-v1/` is the complete six-arm study directory to upload to the Hugging
Face dataset repository. `ox-alpha-v1/` is the supplemental Ox Alpha comparator
release. Generated directories must not be edited in place.

Regenerate it from the repository root:

```bash
node tools/export_huggingface_release.mjs
node tools/export_ox_alpha_release.mjs
```

The exporter builds into a temporary sibling directory, packages
`DATASET_CARD.md`, `VIEWER.md`, the CC BY data license, the Apache code license,
`NOTICE`, and `THIRD_PARTY_NOTICES`, writes the normalized data and metadata,
verifies all inputs while constructing them, and then replaces `blog-v1/`.
This prevents stale files from surviving a release.

`lineage-eval-hf-blog-v1.zip` is an optional upload artifact and is ignored by
Git. The unpacked `blog-v1/` directory is the canonical release.
