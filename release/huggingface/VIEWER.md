# Using the interactive viewer

The Hugging Face tables are normalized for filtering and analysis. The
repository's interactive viewer expects a single nested document, so this
release also includes `viewer/viewer_payload.json`.

From a checkout of the Lineage Eval code repository:

```bash
cp /path/to/viewer_payload.json \
  data/results/blog-v1/matched-v2-full-data.json
cd viewer
npm ci
npm run dev
```

Open <http://localhost:3000>.

For a production build:

```bash
cd viewer
npm ci
npm run build
npm start
```

The viewer payload contains the same 304 prompts, 1,824 responses, 7,296
judgments, and descriptive statistics as the normalized tables. Publication
sanitization changed only run metadata:

- backend names were normalized to `vllm`;
- absolute serving and cache paths were removed;
- private adapter repository identifiers and pinned revisions were removed;
- unreleased model-arm names and resumable attempt histories were removed; and
- the combined GPU cost is explicitly identified as experiment-wide rather
  than a per-batch measurement.

Public builds are read-only. Do not enable the repository's local annotation
mode in a hosted deployment.
