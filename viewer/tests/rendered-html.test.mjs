import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("renders the matched-v2 response browser shell", async () => {
  const response = await render("/");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Matched-v2 Response Viewer<\/title>/i);
  assert.match(html, /Matched-v2 response viewer/);
  assert.match(html, /Loading model responses/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape/i);
});

test("ships the complete blog-v1 release dataset", async () => {
  const payload = JSON.parse(
    await readFile(
      new URL("../public/matched-v2-full-data.json", import.meta.url),
      "utf8",
    ),
  );

  assert.equal(payload.prompts.length, 304);
  assert.deepEqual(payload.models, [
    "gpt_oss_120b",
    "self_distilled",
    "v4_flash_distilled",
    "v4_flash",
    "gpt_oss_20b",
    "expert_20b_self_sturev",
  ]);
  assert.equal(
    new Set(payload.prompts.map((prompt) => prompt.concept_id)).size,
    76,
  );
  assert.deepEqual(
    payload.prompts.reduce((counts, prompt) => {
      counts[prompt.batch] = (counts[prompt.batch] ?? 0) + 1;
      return counts;
    }, {}),
    { finance: 152, core_political: 152 },
  );
  assert.equal(
    payload.prompts.flatMap((prompt) => Object.values(prompt.responses)).length,
    1824,
  );
  assert.equal(
    payload.prompts.filter(
      (prompt) => Object.keys(prompt.responses).length === 0,
    ).length,
    0,
  );
  assert.ok(payload.judges.length >= 2);
  assert.equal(
    payload.judges.reduce(
      (total, judge) => total + judge.successful_grades,
      0,
    ),
    1824 * payload.judges.length,
  );
  assert.equal(
    payload.prompts
      .filter((prompt) => prompt.batch === "core_political")
      .flatMap((prompt) => [
        prompt.responses.gpt_oss_120b,
        prompt.responses.self_distilled,
        prompt.responses.v4_flash_distilled,
      ])
      .filter((response) => response?.reasoning?.trim()).length,
    456,
  );

  const viewerSource = await readFile(
    new URL("../app/components/EvaluationViewer.tsx", import.meta.url),
    "utf8",
  );
  const statisticsSource = await readFile(
    new URL("../app/components/StatisticsView.tsx", import.meta.url),
    "utf8",
  );
  assert.match(viewerSource, /Finance-adjacent/);
  assert.match(viewerSource, /Core political/);
  assert.match(viewerSource, /Statistics/);
  assert.match(viewerSource, /prompts=\{dataset\.prompts\}/);
  assert.match(statisticsSource, /histogram-examples/);
  assert.match(statisticsSource, /aria-pressed=\{selected\}/);
  assert.match(statisticsSource, /\.slice\(0, 4\)/);

  const invalidResponses = payload.prompts
    .flatMap((prompt) => Object.values(prompt.responses))
    .filter(
      (response) => response.effective_label === "INVALID_DEGENERATE",
    );
  assert.equal(invalidResponses.length, 186);
  assert.equal(
    invalidResponses.filter(
      (response) => response.degeneracy_reason === "missing_output",
    ).length,
    171,
  );
  assert.equal(payload.statistics.coverage.invalid_responses, 186);
  assert.equal(payload.statistics.coverage.response_scores, 1638);
  assert.equal(
    payload.statistics.coverage.successful_classifications,
    1638 * payload.judges.length,
  );
  assert.equal(
    payload.statistics.coverage.excluded_classifications,
    186 * payload.judges.length,
  );
  assert.equal(
    payload.statistics.coverage.raw_successful_classifications,
    1824 * payload.judges.length,
  );
  assert.equal(payload.statistics.coverage.unresolved_failures, 0);
  assert.equal(payload.statistics.methodology.random_seed, 20260724);
  assert.equal(payload.statistics.methodology.randomness_used, false);
  assert.equal(
    payload.statistics.methodology.confidence_intervals,
    "not computed",
  );
  assert.deepEqual(
    payload.statistics.analysis_views.map((view) => view.key),
    ["mean", ...payload.judges.map((judge) => judge.model)],
  );
  assert.equal(
    payload.statistics.analysis_views[0].label,
    `Mean of ${payload.judges.length} judges`,
  );
  assert.equal(
    payload.statistics.analysis_views.length,
    payload.judges.length + 1,
  );
  assert.deepEqual(
    payload.statistics.analysis_scopes.map((scope) => scope.key),
    ["all", "finance", "core_political"],
  );
  const expectedScopeTotals = { all: 1824, finance: 912, core_political: 912 };
  for (const scope of payload.statistics.analysis_scopes) {
    const invalidForScope = payload.prompts
      .filter((prompt) => scope.key === "all" || prompt.batch === scope.key)
      .flatMap((prompt) => Object.values(prompt.responses))
      .filter(
        (response) => response.effective_label === "INVALID_DEGENERATE",
      ).length;
    assert.equal(scope.analysis_views.length, payload.judges.length + 1);
    for (const view of scope.analysis_views) {
      assert.equal(
        view.coverage.response_scores,
        expectedScopeTotals[scope.key] - invalidForScope,
      );
      assert.equal(view.coverage.invalid_responses, invalidForScope);
      assert.equal(view.model_summaries.length, 6);
      assert.equal(view.pairwise_model_comparisons.length, 45);
    }
  }
  for (const view of payload.statistics.analysis_views) {
    assert.equal(view.coverage.response_scores, 1638);
    assert.equal(view.coverage.invalid_responses, 186);
    assert.equal(view.model_summaries.length, 6);
    assert.equal(view.pairwise_model_comparisons.length, 45);
  }
  assert.equal(payload.statistics.model_summaries.length, 6);
  assert.equal(
    payload.statistics.model_summaries.reduce(
      (total, model) => total + model.censorship_gap.n,
      0,
    ),
    753,
  );

  for (const prompt of payload.prompts) {
    assert.equal(prompt.benchmark_version, "matched_v2");
    assert.ok(prompt.prompt_id);
    assert.match(prompt.batch, /^(finance|core_political)$/);
    assert.ok(prompt.prompt);
    assert.ok(prompt.concept);
    assert.ok(prompt.must_engage);
    assert.ok(
      Object.keys(prompt.responses).length === payload.models.length,
    );
    for (const response of Object.values(prompt.responses)) {
      assert.equal(response.error, null);
      if (response.content === null) {
        assert.equal(response.finish_reason, "length");
        assert.ok(response.reasoning);
        assert.equal(response.effective_label, "INVALID_DEGENERATE");
        assert.equal(response.degeneracy_reason, "missing_output");
      } else {
        assert.ok(response.content);
      }
      assert.equal(typeof response.completion_tokens, "number");
      assert.equal(response.judges.length, payload.judges.length);
      for (const judge of response.judges) {
        assert.equal(typeof judge.score, "number");
        assert.match(
          judge.label,
          /^(UNCENSORED|CENSORED_WHITEWASH|CENSORED_REFUSAL)$/,
        );
      }
    }
  }

  const responseCardSource = await readFile(
    new URL("../app/components/ResponseCard.tsx", import.meta.url),
    "utf8",
  );
  const responseDetailSource = await readFile(
    new URL("../app/components/ResponseDetail.tsx", import.meta.url),
    "utf8",
  );
  const markdownSource = await readFile(
    new URL("../app/components/MarkdownResponse.tsx", import.meta.url),
    "utf8",
  );
  assert.match(responseCardSource, /<MarkdownResponse>/);
  assert.match(responseCardSource, /Human score/);
  assert.match(responseCardSource, /annotationEnabled && response/);
  assert.match(viewerSource, /NEXT_PUBLIC_ENABLE_ANNOTATION/);
  assert.match(responseCardSource, /<details className="judges-panel" open>/);
  assert.match(responseDetailSource, /<details className="must-engage" open>/);
  assert.match(responseDetailSource, /className="model-picker"/);
  assert.match(responseDetailSource, /type="checkbox"/);
  assert.match(responseDetailSource, /visibleModelKeys\.map/);
  assert.match(markdownSource, /ReactMarkdown/);
  assert.match(markdownSource, /remarkGfm/);

  const labelsPayload = JSON.parse(
    await readFile(
      new URL(
        "../../data/annotations/blog-v1/human-labels.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  assert.equal(labelsPayload.schema_version, 1);
  assert.equal(labelsPayload.experiment, payload.run.experiment);
  assert.ok(Array.isArray(labelsPayload.labels));
});
