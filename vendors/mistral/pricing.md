# Mistral OCR — pricing

Source: Mistral pricing page (`mistral.ai/pricing` / `mistral.ai/news/ocr-4/`)
and the live API, fetched 2026-08-11.

## Published rates (docs, 2026-08-11)

| Product | Price per 1,000 pages | Per page |
|---|---|---|
| Mistral OCR 4 (API, real-time) | $4.00 | $0.004 |
| Mistral OCR 4 (Batch API, 50% off) | $2.00 | $0.002 |
| Mistral Document AI (annotated) | $5.00 | $0.005 |

- Billing is per page processed, not per token.
- No monthly minimum, no per-seat fee, no idle hosting fee.
- Free tier: not published on the API pricing page.

## Observed cost from a real call (2026-08-11)

One real bundle: `data/corpus/pkt_001_control_multi_class.pdf`, 12 pages.

| Call | Pages | Rate | Cost |
|---|---|---|---|
| Plain OCR (include_blocks, no annotation) | 12 | $0.004/page | $0.048 |
| Annotated (json_schema extraction) | 12 | $0.005/page | $0.060 |

- `usage_info.pages_processed` = 12 for both calls.
- The annotated (Document AI) rate applies when `document_annotation_format`
  is set; the plain OCR rate applies otherwise.

## Cost at volume (DERIVED, from the observed 12-page bundle)

At 10,000 uploads/month × 12 pages/upload = 120,000 pages/month:

| Mode | $/month |
|---|---|
| Plain OCR only | $480 |
| Annotated (single-call) | $600 |

This is the single-call approach: one annotated pass gets both boundaries and
fields, so the annotated rate is the relevant one. At $600/month for 120k pages
it is far below the Extend comparison (~$1,450–$5,075/month at 10,000 uploads),
which is the whole reason we are testing whether Mistral alone suffices.
