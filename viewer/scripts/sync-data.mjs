import { copyFile, mkdir, stat } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const viewerRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(
  viewerRoot,
  process.env.LINEAGE_EVAL_VIEWER_DATA ??
    "../data/results/blog-v1/matched-v2-full-data.json",
);
const destination = resolve(viewerRoot, "public/matched-v2-full-data.json");

try {
  await stat(source);
} catch {
  throw new Error(
    `Missing released viewer dataset at ${source}. Restore the published data ` +
      "artifact or set LINEAGE_EVAL_VIEWER_DATA to another compatible payload.",
  );
}

await mkdir(dirname(destination), { recursive: true });
await copyFile(source, destination);
console.log(`Synced viewer data from ${source}`);
