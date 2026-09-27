# Mistral OCR — vendor notes

Source: Mistral docs (`docs.mistral.ai/capabilities/document/`) and the live
API, fetched 2026-08-11.

## What it is

Mistral OCR returns typed structural blocks and per-page text in one call over
the whole document, and can return structured fields against a JSON schema.
This is the candidate to replace Extend entirely: if its own block structure is
good enough to find document boundaries, we do not need a second vendor.

## Auth

- Bearer token, `MISTRAL_API_KEY`, read from the environment only. Never
  hardcoded.

## Base URL / endpoint

- `POST https://api.mistral.ai/v1/ocr`
- Model id: `mistral-ocr-latest` (pins the current generation).

## Request shape (verified live 2026-08-11)

```json
{
  "model": "mistral-ocr-latest",
  "document": { "type": "document_url",
                "document_url": "data:application/pdf;base64,<b64>" },
  "include_blocks": true,
  "confidence_scores_granularity": "word",
  "document_annotation_format": { "type": "json_schema",
    "json_schema": { "name": "invoice_extraction", "schema": {...} } }
}
```

- The input PDF is passed as a base64 data URI in `document.document_url`.
- `include_blocks: true` returns typed blocks (title/text/table/...) with
  bounding boxes per page.
- `confidence_scores_granularity: "word"` returns per-word confidence plus a
  page-level `average_page_confidence_score`.
- `document_annotation_format` (json_schema) returns structured fields in
  `document_annotation` as a JSON **string**.

## Response shape (verified live 2026-08-11)

```json
{
  "pages": [
    { "index": 0, "markdown": "...", "blocks": [
        { "type": "title", "top_left_x": 60, "top_left_y": 59,
          "bottom_right_x": 487, "bottom_right_y": 83,
          "content": "# INVOICE | packet ...", "confidence_scores": null } ],
      "confidence_scores": { "average_page_confidence_score": 0.974, ... } }
  ],
  "model": "mistral-ocr-latest",
  "document_annotation": "<json string>",
  "usage_info": { "pages_processed": 12, "doc_size_bytes": 8644 }
}
```

- `pages[].index` is **0-based**; physical page = index + 1.
- `pages[].blocks[].type` includes `title`, `text`, `table`, etc.
- `pages[].confidence_scores.average_page_confidence_score` is a real
  per-page confidence signal (0–1). Unlike Extend, Mistral DOES return
  confidence, so we populate it honestly and never zero it.
- `document_annotation` is a JSON string; parse it with `json.loads`.

## Limits (docs, 2026-08-11)

- 50 MB per request.
- 1000 pages per request.
- We reject larger inputs with a clear error before calling.

## Boundary rule (our heuristic, tune after real results)

A new document starts on a page where a `title` block appears in the top
region of the page AND the page's header/letterhead text differs from the
previous page's. Page 1 always starts a document. Markdown heading markers
(`#`/`##`) are stripped before comparison so a heading-level change does not
create a false boundary.

## Observed behaviour on pkt_001 (12 pages, 3 docs)

- Blocks: every page has a `title` block at top (y≈59) plus 3 `text` blocks.
- Title text changes exactly at document boundaries (pages 4 and 8).
- Boundary rule produced the correct 1-3 / 4-7 / 8-12 split.
- Per-document confidence: 0.975 / 0.923 / 0.984 (real, not synthesized).

## Replay

A fixture is used ONLY when passed explicitly via `fixture_path` /
`annotated_fixture_path` to the constructor. There is deliberately NO
environment-variable path: an env var silently turning the live app into
replay is how fake data reached the review screen. Replay stays available for
tests and the CLI, never as an implicit default for the web app.
