# research/

Shallow clones of the projects studied in Phase 1–2. They are **not** part of the product, are
never modified, and are git-ignored (only this manifest is committed). To reproduce:

```sh
git clone --depth 1 https://github.com/<owner>/<repo> research/<owner>__<repo>
```

| Clone | Commit | Commit date | Licence (from LICENSE file) |
| --- | --- | --- | --- |
| Kiln-AI__Seahaven | e076fca | 2026-10-06 | MIT |
| Kiln-AI__stripe_world | 83c2762 | 2026-10-01 | **None** (no LICENSE file; see docs/licensing.md) |
| volter-ai__twin-packs-open | 08cf5f7 | 2026-10-08 | Apache-2.0 |
| vercel-labs__emulate | c77cb73 | 2026-10-06 | Apache-2.0 |
| stripe__stripe-mock | 98674a1 | 2026-09-29 | MIT |
| adrienverge__localstripe | ff8ccc4 | 2026-05-21 | GPL-3.0 |
| stripe-ruby-mock__stripe-ruby-mock | 4e553aa | 2026-09-09 | MIT |
| sierra-research__tau2-bench | 4ce7c03 | 2026-10-06 | MIT |
| Shopify__toxiproxy | 94adfeb | 2026-10-08 | MIT |
| Untargetable__stunt | 5e45b2f | 2026-09-06 | MIT |
| sajjadGG__Ghostlab | 487aa11 | 2026-09-28 | MIT |
| opendatahub-io__agentic-ci | 0254b41 | 2026-10-08 | Apache-2.0 |
| MarckMorris__tfminder | f360674 | 2026-09-25 | Apache-2.0 |
| Terfyn__terfyn | 0d6e587 | 2026-10-03 | Apache-2.0 |

npm tarballs (`npm pack`, unpacked under `npm/`):

| Package | Version | Licence |
| --- | --- | --- |
| @volter/twin | 0.1.3 | Apache-2.0 |
| @volter/twin-stripe | 3.0.2 | Apache-2.0 |
