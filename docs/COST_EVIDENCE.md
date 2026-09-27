# Cost evidence — Mistral OCR + Small vs Medium

Snapshot from a 29-page dense packet A/B run (architecture B: shared OCR +
shared boundaries, then chat understanding per segment). Raw run JSON is not
kept in the repo; these numbers are the claim.

| Arm | Chat model | Docs returned | Avg filled fields / doc | Estimated cost (29 pages) | Cost / 1000 pages |
|---|---|---|---|---|---|
| A (chosen) | `mistral-small-latest` | 26 | 40.3 | **$0.162** | **~$5.58** |
| B | `mistral-medium-latest` | 23 | lower coverage | $0.739 | ~$25.5 |

**Result:** Small beat Medium on coverage (more documents, higher fill) at
about **4.6× lower cost**. That is why the default chat model is Small.

OCR portion of arm A was ~$0.116 of the $0.162 total (OCR 4 list rate
$4 / 1000 pages × 29 pages); the rest is estimated chat tokens.
