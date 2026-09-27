# Boundary design notes

## What a "document" is

A document is **one bill's worth of paper** — everything an accountant would
enter as one voucher. That includes continuation pages, statutory annexures
and tax schedules, same-supplier terms, and signature / declaration pages,
even when those pages reprint the letterhead or carry their own totals.

A new document starts only when a **different bill** begins: a different
supplier, or the same supplier with a different bill reference.

## Shape of the AI answer

The model returns a **list of start pages** (plus a short reason on each
proposed start). Hard rules run before and after; a post-answer check can
merge adjacent cuts that share a grounded bill number and matching vendor.

### Tried and rejected: per-boundary yes/no decisions

We tried replacing the start list with one yes/no decision for every
adjacent page gap (`starts_new_document` + reason on the same object),
hoping the answer and its justification could not disagree.

**Rejected.** On a live full-set run it under-split (`pkt_b05` 5→3,
`pkt_b08` 6→5) because judging one gap in isolation loses the whole-packet
view. Output tokens rose roughly 3× on a 29-page packet. Binding the answer
to its reason still did not stop the model from saying "yes, new bill"
while describing an annexure of the same bill (bare-GGPL). The list-of-
starts shape plus the after-the-fact check remains the design. Do not
reintroduce per-gap decisions without a new, stronger reason.

## Deliberate trade: non-invoice pages may glue

Asking for one bill's worth of paper can glue same-supplier non-invoice
pages (e.g. terms + delivery note in `pkt_b05`) into one cut. Those pages
are excluded before push, so the cost is an extra click, not a wrong
voucher. Same-supplier invoices with different bill numbers (`pkt_b03`)
and duplicate-number different-vendor pairs (`pkt_b06`) must still stay
separate.

## Production debug

`BOUNDARY_DEBUG` is local opt-in only. It is forced off on Render even if
the env var is set — never enable it in production (traces contain page
text).

## Reviewer labels: Checked vs Needs a look

The old 0.98 / Constrained number meant "a hard rule fired", not "safe to
skip." That lied on wrong cuts and trained reviewers to skim past them.

The screen now shows **Checked** or **Needs a look** — no percentage.

**Cuts** need a look when: model-only, hard signals disagreed, model reason
argues against the cut, or the after-check was unsure.

**Holds** (every adjacent page pair inside a multi-page document) need a
look when: pages were held with no hard hold signal, hard signals
disagreed on the hold, different bill numbers or seller GSTINs were held
together, or the after-check merged on thin evidence. Under-splitting is
the wrong-voucher failure; a badge that only labels existing cuts is blind
to it (the `pkt_b05` terms/delivery-note glue was the telling case).

## Seller GSTIN (group companies on one letterhead)

Same brand / letterhead with **different seller GSTINs** must stay separate
vouchers. Buyer GSTIN repeats across a packet and is ignored. Missing GSTIN
is silence, not a match. Overlay will not merge on a shared number when
seller GSTINs differ. Fixture: `pkt_b10_group_letterhead_diff_gstin`.

## Blank separator pages

Blank sheets between scanned bills are **auto-excluded**. They stay visible
in the page list ("Auto-excluded · Blank separator") with one-click restore.
Detection requires empty OCR text **and** a sparse page (no text blocks) so
a faint scan is not dropped silently. Coverage remains:
documents ∪ excluded = every page. Fixture: `pkt_b11_blank_separators`.
