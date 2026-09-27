"""Adaptive <=N-page batch packing (pure, no provider imports).

Greedy whole-document packing for downstream systems with a hard page cap
(e.g. production AP's 12-page limit). Oversized documents are split into
contiguous parts of at most max_pages that share one doc_id — never mixed
with other documents in the same batch.
"""

from __future__ import annotations

from typing import Any


def pack_documents(
    page_count: int,
    doc_ranges: list[dict[str, Any]],
    max_pages: int,
    excluded_pages: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Pack logical documents into ordered batches of at most ``max_pages``.

    Parameters
    ----------
    page_count:
        Total pages in the source packet (documents ∪ excluded cover 1..N).
    doc_ranges:
        ``{doc_id, page_start, page_end}`` (optional ``doc_type``). Any order.
    max_pages:
        Hard per-batch page cap (must be >= 1).
    excluded_pages:
        Pages deliberately omitted from every batch (still count toward coverage).

    Returns
    -------
    batches:
        Ordered list of
        ``{batch_id, page_start, page_end, documents: [{doc_id, page_start,
        page_end, part, parts, oversized, doc_type?}]}``.
    """
    if page_count < 1:
        raise ValueError(f"page_count must be >= 1, got {page_count}")
    if max_pages < 1:
        raise ValueError(f"max_pages must be >= 1, got {max_pages}")
    if not doc_ranges and not (excluded_pages or []):
        raise ValueError("doc_ranges must be non-empty")

    ordered = sorted(doc_ranges, key=lambda d: int(d["page_start"]))
    _assert_full_coverage(ordered, page_count, excluded_pages)
    if not ordered:
        return []

    batches: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_pages = 0

    def flush() -> None:
        nonlocal current, current_pages
        if not current:
            return
        batches.append(_make_batch(len(batches) + 1, current))
        current = []
        current_pages = 0

    for raw in ordered:
        doc_id = str(raw["doc_id"])
        start = int(raw["page_start"])
        end = int(raw["page_end"])
        length = end - start + 1
        doc_type = raw.get("doc_type")

        if length > max_pages:
            flush()
            parts = _split_oversized(start, end, max_pages)
            n_parts = len(parts)
            for i, (ps, pe) in enumerate(parts, start=1):
                entry: dict[str, Any] = {
                    "doc_id": doc_id,
                    "page_start": ps,
                    "page_end": pe,
                    "part": i,
                    "parts": n_parts,
                    "oversized": True,
                }
                if doc_type is not None:
                    entry["doc_type"] = doc_type
                batches.append(_make_batch(len(batches) + 1, [entry]))
            continue

        if current_pages + length > max_pages:
            flush()

        entry = {
            "doc_id": doc_id,
            "page_start": start,
            "page_end": end,
            "part": 1,
            "parts": 1,
            "oversized": False,
        }
        if doc_type is not None:
            entry["doc_type"] = doc_type
        current.append(entry)
        current_pages += length

    flush()
    _assert_batch_invariants(batches, page_count, max_pages, ordered)
    return batches


def _split_oversized(start: int, end: int, max_pages: int) -> list[tuple[int, int]]:
    parts: list[tuple[int, int]] = []
    cursor = start
    while cursor <= end:
        pe = min(cursor + max_pages - 1, end)
        parts.append((cursor, pe))
        cursor = pe + 1
    return parts


def _make_batch(index: int, documents: list[dict[str, Any]]) -> dict[str, Any]:
    page_start = min(int(d["page_start"]) for d in documents)
    page_end = max(int(d["page_end"]) for d in documents)
    return {
        "batch_id": f"batch_{index:03d}",
        "page_start": page_start,
        "page_end": page_end,
        "documents": documents,
    }


def _assert_full_coverage(
    ordered: list[dict[str, Any]],
    page_count: int,
    excluded_pages: list[int] | None = None,
) -> None:
    """Assert documents ∪ excluded cover pages 1..page_count exactly."""
    excluded = sorted({int(p) for p in (excluded_pages or [])})
    for p in excluded:
        if p < 1 or p > page_count:
            raise ValueError(
                f"Excluded page {p} invalid for {page_count}-page packet."
            )
    excluded_set = set(excluded)
    covered: set[int] = set(excluded_set)
    prev_end = 0
    for d in ordered:
        start, end = int(d["page_start"]), int(d["page_end"])
        if start < 1 or end > page_count or start > end:
            raise ValueError(
                f"Range {start}-{end} invalid for {page_count}-page packet "
                f"(doc_id={d.get('doc_id')!r})."
            )
        for p in range(start, end + 1):
            if p in excluded_set:
                raise ValueError(
                    f"Document range {start}-{end} includes excluded page {p}."
                )
            if p in covered:
                raise ValueError(f"Page {p} covered more than once.")
            covered.add(p)
        if start <= prev_end:
            raise ValueError(
                f"Ranges overlap: expected start > {prev_end}, got {start} "
                f"(doc_id={d.get('doc_id')!r})."
            )
        for p in range(prev_end + 1, start):
            if p not in excluded_set:
                raise ValueError(
                    f"Page {p} is neither in a document nor excluded "
                    f"(gap before {start})."
                )
        prev_end = end
    for p in range(prev_end + 1, page_count + 1):
        if p not in excluded_set:
            raise ValueError(
                f"Page {p} is neither in a document nor excluded "
                f"(coverage ended at {prev_end}, page_count={page_count})."
            )
        covered.add(p)
    if covered != set(range(1, page_count + 1)):
        missing = sorted(set(range(1, page_count + 1)) - covered)
        raise ValueError(
            f"documents ∪ excluded must cover 1..{page_count}; missing {missing}."
        )


def _assert_batch_invariants(
    batches: list[dict[str, Any]],
    page_count: int,
    max_pages: int,
    ordered_docs: list[dict[str, Any]],
) -> None:
    covered: list[int] = []
    for batch in batches:
        bs, be = int(batch["page_start"]), int(batch["page_end"])
        size = be - bs + 1
        if size > max_pages:
            raise AssertionError(
                f"{batch['batch_id']} has {size} pages > max_pages={max_pages}"
            )
        for p in range(bs, be + 1):
            covered.append(p)
        # Documents inside a batch must be contiguous and fill the batch span.
        prev = bs - 1
        for doc in batch["documents"]:
            ds, de = int(doc["page_start"]), int(doc["page_end"])
            if ds != prev + 1:
                raise AssertionError(
                    f"{batch['batch_id']}: gap/overlap inside batch at {ds}"
                )
            prev = de
        if prev != be:
            raise AssertionError(
                f"{batch['batch_id']}: documents end at {prev}, batch at {be}"
            )

    if sorted(covered) != list(range(1, page_count + 1)):
        raise AssertionError(
            "Batch page coverage is not exactly {1..page_count} once each."
        )

    # Document order preserved: flatten non-oversized entries + first parts of
    # oversized docs in input order; part slices stay ascending within a doc.
    flat_ids: list[str] = []
    seen_oversized: set[str] = set()
    for batch in batches:
        for doc in batch["documents"]:
            did = str(doc["doc_id"])
            if doc.get("oversized"):
                if did not in seen_oversized:
                    flat_ids.append(did)
                    seen_oversized.add(did)
            else:
                flat_ids.append(did)
    expected_ids = [str(d["doc_id"]) for d in ordered_docs]
    if flat_ids != expected_ids:
        raise AssertionError(
            f"Document order not preserved: got {flat_ids}, expected {expected_ids}"
        )


def find_duplicate_doc_groups(
    documents_v2: list[Any],
) -> tuple[list[str], list[list[dict[str, int]]]]:
    """Group V2 docs by normalized non-empty ``document_id.value``.

    Returns ``(error_codes, duplicate_groups)`` where each group is a list of
    ``{page_start, page_end}`` for docs sharing the same invoice number, and
    each distinct duplicated value yields one ``duplicate_doc_number:<value>``
    error code.
    """
    buckets: dict[str, list[tuple[str, dict[str, int]]]] = {}
    for doc in documents_v2:
        value = _document_id_value(doc)
        if not value:
            continue
        key = _normalize_doc_number(value)
        if not key:
            continue
        member = {
            "page_start": int(_attr(doc, "page_start")),
            "page_end": int(_attr(doc, "page_end")),
        }
        buckets.setdefault(key, []).append((value.strip(), member))

    errors: list[str] = []
    groups: list[list[dict[str, int]]] = []
    for _key, members in sorted(
        buckets.items(), key=lambda kv: kv[1][0][1]["page_start"]
    ):
        if len(members) < 2:
            continue
        display = members[0][0]
        errors.append(f"duplicate_doc_number:{display}")
        groups.append(
            sorted((m for _, m in members), key=lambda m: m["page_start"])
        )
    return errors, groups


def _document_id_value(doc: Any) -> str:
    fv = _attr(doc, "document_id")
    if fv is None:
        return ""
    if isinstance(fv, dict):
        return str(fv.get("value") or "")
    return str(getattr(fv, "value", "") or "")


def _attr(doc: Any, name: str) -> Any:
    if isinstance(doc, dict):
        return doc.get(name)
    return getattr(doc, name, None)


def _normalize_doc_number(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def doc_ranges_from_split_documents(
    documents: list[Any],
) -> list[dict[str, Any]]:
    """Build packing input from SplitDocument-like objects."""
    ranges: list[dict[str, Any]] = []
    for d in documents:
        start = int(_attr(d, "start_page") if _attr(d, "start_page") is not None else _attr(d, "page_start"))
        end = int(_attr(d, "end_page") if _attr(d, "end_page") is not None else _attr(d, "page_end"))
        doc_type = _attr(d, "doc_type") or "unknown"
        ranges.append(
            {
                "doc_id": f"doc-{start}-{end}",
                "page_start": start,
                "page_end": end,
                "doc_type": doc_type,
            }
        )
    return ranges


def build_pack_manifest(
    *,
    packet_pages: int,
    max_pages: int,
    batches: list[dict[str, Any]],
    duplicate_groups: list[list[dict[str, int]]] | None = None,
) -> dict[str, Any]:
    """Serialize packing output into the save-batched zip manifest shape."""
    manifest_batches = []
    for batch in batches:
        bid = batch["batch_id"]
        file_name = f"{bid}.pdf"
        docs_out = []
        for doc in batch["documents"]:
            docs_out.append(
                {
                    "doc_id": doc["doc_id"],
                    "doc_type": doc.get("doc_type") or "unknown",
                    "page_start": int(doc["page_start"]),
                    "page_end": int(doc["page_end"]),
                    "oversized": bool(doc.get("oversized", False)),
                    "part": int(doc.get("part", 1)),
                    "parts": int(doc.get("parts", 1)),
                }
            )
        manifest_batches.append(
            {
                "batch_id": bid,
                "file": file_name,
                "page_start": int(batch["page_start"]),
                "page_end": int(batch["page_end"]),
                "documents": docs_out,
            }
        )
    return {
        "packet_pages": packet_pages,
        "max_pages": max_pages,
        "batches": manifest_batches,
        "duplicate_groups": duplicate_groups or [],
    }
