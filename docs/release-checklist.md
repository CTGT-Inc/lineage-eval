# Public release checklist

- [x] Publish from a new, clean Git history; deleted internal files remain
      accessible in the original history.
- [x] Run a secret scan over the complete new history.
- [x] Select and add an explicit code license.
- [x] Select and add an explicit dataset/results license after reviewing source
      dataset terms and model-output terms.
- [x] Add final author and citation metadata (`CITATION.cff` or equivalent).
- [x] State consistently that the three trained adapters are not distributed
      and that the headline comparison cannot be regenerated from public
      weights.
- [ ] Run `lineage-eval doctor` against a live user-managed vLLM endpoint.
- [ ] Complete a two-prompt smoke test for every documented model family.
- [ ] Check captured answer/reasoning separation for every model family.
- [x] Run all platform-independent Python tests.
- [x] Build, test, and lint the viewer from a clean install.
- [x] Verify the published payload counts against
      `data/results/blog-v1/README.md`.
- [x] Confirm public viewer builds have annotation mode disabled.
- [ ] Link the final blog and hosted viewer from the root README.
