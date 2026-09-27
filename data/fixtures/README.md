# Replay fixtures

`pkt_ggpl_continuation_annexure` is a **redacted** replay of a real 3-page
packet that our splitter cut into two documents.

Identifiers (vendor, buyer, GSTINs, invoice prefix) are synthetic (`ZZ Test…`,
`ZZCO/8801/26-27`, `22AAAAA0000A1Z5`). The **structure** is intact on purpose:

- repeated invoice number on every page, without an "Invoice No." label
- page 1 says `continued to page number 3`
- page 2 closes with totals, declaration, and a signature
- page 3 restarts a header as a statutory tax-analysis annexure with its own totals
- printed page labels are wrong and repeat: Page 2, Page 3, Page 2

`pkt_ggpl_bare_number_annexure` is the same packet with the extent sentence
removed. Layer 1 is silent on the number; `document_complete` splits page 3;
the post-judge overlay merges on the grounded shared number.

`pkt_contract_note_signature_page` is a redacted 2-page broker contract note.
Page 2 reprints the full letterhead and empty table headers, then only
"Yours faithfully / Date / Place / GSTIN / Authorised Signatory". Both pages
carry `Contract Note No COMBINED/14531` and no Invoice-labelled number.
Truth: one document, pages 1-2.

Deliberate trade (do not treat as a bug): the one-bill definition can glue
same-supplier non-invoice pages (`pkt_b05` terms + delivery note). Those
pages are excluded before push. See `docs/BOUNDARY_DESIGN.md`.

Corpus additions (synthetic PDFs under `data/corpus/`):
- `pkt_b10_group_letterhead_diff_gstin` — same letterhead brand, different
  seller GSTINs → must stay two vouchers.
- `pkt_b11_blank_separators` — blank sheets between bills → auto-exclude.
- `pkt_b12_undersplit_diff_bill_numbers` — two invoices, different bill
  numbers; badge test forces a 1–2 hold to prove the hold rule fires.

OCR JSON: `vendors/mistral/fixtures/pkt_ggpl_continuation_annexure.json`.
Bare-number sibling: `vendors/mistral/fixtures/pkt_ggpl_bare_number_annexure.json`.
Contract note: `vendors/mistral/fixtures/pkt_contract_note_signature_page.json`.
The PDF is built in the e2e test from that markdown (no customer file in git).
