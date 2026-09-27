"""CLI entry point for the aia-pdf-split POC.

Usage (from project root, with .venv active):
  pdfsplit build-corpus
  pdfsplit stage3-physical <pdf> [--out DIR] [--pages N] [--ranges 1-3,4-6]
  pdfsplit stage5-benchmark [--provider mistral] [--limit N]
  pdfsplit boundary-benchmark [--provider mock|mistral] [--compare-baseline]
  pdfsplit stage6-report
  pdfsplit pack --session <id> [--max-pages N]
  pdfsplit pack <pdf> [--provider mistral] [--max-pages N]
  pdfsplit push-session <session-id> [--push] [--erp tally|zoho]
  pdfsplit prune-sessions   # delete all but the newest upload session
  pdfsplit smoke            # end-to-end with mock provider (no credentials)
  pdfsplit e2e [--live]     # local HTTP e2e (fixtures; --live hits Mistral)
  pdfsplit serve [--port 7790] [--reload]   # run the FastAPI web app
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pdfsplit.config import settings
from pdfsplit.corpus import build_corpus


def cmd_build_corpus(args) -> int:
    corpus_dir = settings.corpus_dir
    corpus = build_corpus(corpus_dir, seed=args.seed)
    manifest = corpus.save_manifest()
    print(f"Built {len(corpus.packets)} packets -> {manifest}")
    for spec in corpus.packets:
        print(
            f"  {spec.packet_id} ({spec.category}): {len(spec.documents)} docs, "
            f"{spec.pages} pages"
        )
    return 0


def cmd_stage3(args) -> int:
    """Physical splitting: produce real PDF files from page ranges."""
    from pdfsplit.pdf import split_pdf_by_ranges, verify_split
    from pdfsplit.schema import SplitDocument

    if args.ranges:
        documents = []
        for part in args.ranges.split(","):
            start, end = part.split("-")
            documents.append(
                SplitDocument(
                    doc_type="doc",
                    start_page=int(start),
                    end_page=int(end),
                    confidence=1.0,
                )
            )
    else:
        provider = _build_provider("mistral")
        result = provider.split(args.pdf)
        documents = result.documents

    out = Path(args.out)
    splits = split_pdf_by_ranges(Path(args.pdf), documents, out, total_pages=args.pages)
    print("Created:")
    for s in splits:
        print(f"  {s.path} ({s.doc.start_page}-{s.doc.end_page})")
    problems = verify_split(Path(args.pdf), splits)
    print("Verification:", "OK" if not problems else problems)
    return 0


def _build_provider(name: str, create_processor=True):
    if name == "mock":
        from pdfsplit.providers.mock import MockSplitterProvider

        return MockSplitterProvider(
            documents=[
                __import__("pdfsplit.schema", fromlist=["SplitDocument"]).SplitDocument(
                    doc_type="invoice", start_page=1, end_page=4, confidence=0.98
                ),
                __import__("pdfsplit.schema", fromlist=["SplitDocument"]).SplitDocument(
                    doc_type="bank_statement", start_page=5, end_page=7, confidence=0.94
                ),
            ]
        )
    if name == "mistral":
        from pdfsplit.providers.mistral import MistralSplitterProvider

        return MistralSplitterProvider(settings=settings)
    raise ValueError(f"Unknown provider: {name}")


#: Every provider selectable by stage5-benchmark.
PROVIDER_NAMES = ["mistral"]


def _provider_credential_status(name: str) -> str:
    """Return a ProviderStatus value ("ready" | "missing_credentials") for a provider.

    Uses the existing ProviderStatus Literal from schema.py. Never calls any API.
    """
    if name == "mistral":
        return (
            "ready" if settings.has_mistral_credentials() else "missing_credentials"
        )
    return "missing_credentials"


def cmd_stage5(args) -> int:
    """Benchmark harness: run a provider over the corpus and record results."""
    from pdfsplit.benchmark import run_benchmark

    provider = _build_provider(args.provider)
    out_dir = settings.output_dir / "runs"
    report = run_benchmark(
        provider, settings.corpus_dir, out_dir, limit=args.limit, sleep_s=args.sleep
    )
    print(f"Wrote results to {out_dir}/{report.run_id}.json")
    return 0


def cmd_boundary_benchmark(args) -> int:
    """Score the boundary pipeline on the synthetic corpus via scoring.py."""
    from pdfsplit.boundary_benchmark import (
        render_boundary_report,
        run_boundary_benchmark,
    )

    out_dir = settings.output_dir / "runs" / "boundary"
    report = run_boundary_benchmark(
        settings.corpus_dir,
        out_dir,
        provider=args.provider,
        mode=args.mode,
        layer1=not args.no_layer1,
        use_signature=args.signature,
        limit=args.limit,
        settings=settings,
        compare_baseline=args.compare_baseline,
    )
    print(render_boundary_report(report))
    print(f"Wrote {report.get('report_path')}")
    return 0


def cmd_stage6(args) -> int:
    """Report: load the latest run and print a human-readable summary."""
    from pdfsplit.benchmark import render_markdown

    runs_dir = settings.output_dir / "runs"
    if args.run:
        run_id = args.run
    else:
        candidates = sorted(runs_dir.glob("*.json"))
        if not candidates:
            print("No benchmark runs found. Run 'pdfsplit stage5-benchmark' first.")
            return 1
        run_id = candidates[-1].stem
    data = json.loads((runs_dir / f"{run_id}.json").read_text())
    summary = data["summary"]
    # Rebuild a minimal report object for markdown.
    from pdfsplit.benchmark import BenchmarkReport

    report = BenchmarkReport(
        provider=summary["provider"],
        model_version=summary["model_version"],
        run_id=run_id,
        runs=[],
    )
    print(render_markdown(report, summary))
    return 0


def cmd_serve(args) -> int:
    """Run the FastAPI web app with uvicorn."""
    import uvicorn

    uvicorn.run(
        "pdfsplit.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_pack(args) -> int:
    """Pack documents into <=N-page batches; print manifest summary."""
    from pdfsplit.packing import (
        build_pack_manifest,
        doc_ranges_from_split_documents,
        find_duplicate_doc_groups,
        pack_documents,
    )
    from pdfsplit.schema import SplitDocument

    max_pages = int(args.max_pages or settings.max_downstream_pages)
    duplicate_groups: list = []

    if args.session:
        from pdfsplit.app.session import SessionStore

        store = SessionStore(settings.output_dir / "sessions")
        session = store.get(args.session)
        if session is None:
            print(f"Session not found: {args.session}")
            return 1
        if not session.has_split():
            print(f"Session {args.session} has no cached split result.")
            return 1
        page_count = session.page_count
        ranges = doc_ranges_from_split_documents(session.split_result.documents)
        extraction = session.extraction_result
        if extraction is not None:
            duplicate_groups = list(getattr(extraction, "duplicate_groups", None) or [])
            if not duplicate_groups and extraction.documents_v2:
                _, duplicate_groups = find_duplicate_doc_groups(extraction.documents_v2)
    else:
        pdf = Path(args.pdf)
        if not pdf.is_file():
            print(f"PDF not found: {pdf}")
            return 1
        provider = _build_provider(args.provider)
        # Prefer extraction (chat) when the provider supports it; else split.
        if hasattr(provider, "extract"):
            extraction = provider.extract(pdf)
            documents = [
                SplitDocument(
                    doc_type=d.doc_type,
                    start_page=d.page_start,
                    end_page=d.page_end,
                    confidence=d.confidence,
                )
                for d in extraction.documents
            ]
            page_count = extraction.pages_processed or sum(
                d.end_page - d.start_page + 1 for d in documents
            )
            duplicate_groups = list(getattr(extraction, "duplicate_groups", None) or [])
            if not duplicate_groups and extraction.documents_v2:
                _, duplicate_groups = find_duplicate_doc_groups(extraction.documents_v2)
        else:
            result = provider.split(pdf)
            documents = result.documents
            page_count = max(d.end_page for d in documents)
        ranges = doc_ranges_from_split_documents(documents)

    batches = pack_documents(page_count, ranges, max_pages)
    manifest = build_pack_manifest(
        packet_pages=page_count,
        max_pages=max_pages,
        batches=batches,
        duplicate_groups=duplicate_groups,
    )
    print(
        f"packet_pages={manifest['packet_pages']} max_pages={manifest['max_pages']} "
        f"batches={len(manifest['batches'])} "
        f"duplicate_groups={len(manifest['duplicate_groups'])}"
    )
    for batch in manifest["batches"]:
        bits = []
        for d in batch["documents"]:
            label = f"{d['doc_id']}[{d['page_start']}-{d['page_end']}"
            if d["oversized"]:
                label += " ov"
            if d["parts"] > 1:
                label += f" {d['part']}/{d['parts']}"
            label += "]"
            bits.append(label)
        print(
            f"  {batch['batch_id']} pages {batch['page_start']}-{batch['page_end']} "
            f"({batch['page_end'] - batch['page_start'] + 1}p): {', '.join(bits)}"
        )
    if args.json:
        print(json.dumps(manifest, indent=2))
    return 0



def cmd_prune_sessions(args) -> int:
    """Delete upload sessions older than the newest one (frees output/sessions)."""
    from pdfsplit.app.session import SessionStore

    store = SessionStore(settings.output_dir / "sessions")
    kept, removed = store.prune_old_sessions(keep=max(1, int(args.keep or 1)))
    print(f"Kept {len(kept)} session(s); removed {len(removed)}.")
    for sid in kept:
        print(f"  keep  {sid}")
    for sid in removed[:20]:
        print(f"  rm    {sid}")
    if len(removed) > 20:
        print(f"  ... and {len(removed) - 20} more")
    return 0


def cmd_e2e(args) -> int:
    """Upload → analyze → confirm → cut. Fixtures by default; --live hits Mistral."""
    import pytest

    test = str(Path(__file__).resolve().parents[2] / "tests" / "test_e2e_local.py")
    q = ["-q", test]
    if getattr(args, "live", False):
        q.append("--live")
    return int(pytest.main(q))


def cmd_smoke(args) -> int:
    """End-to-end smoke test using the mock provider (no credentials)."""
    cmd_build_corpus(args)
    from pdfsplit.benchmark import run_benchmark
    from pdfsplit.providers.mock import MockSplitterProvider
    from pdfsplit.schema import SplitDocument

    # Mock that actually mirrors corpus ground truth per packet is complex; use a
    # generic two-doc prediction and show the harness works mechanically.
    provider = MockSplitterProvider(
        documents=[
            SplitDocument(doc_type="invoice", start_page=1, end_page=4, confidence=0.9),
            SplitDocument(
                doc_type="bank_statement", start_page=5, end_page=7, confidence=0.9
            ),
        ]
    )
    out_dir = settings.output_dir / "runs"
    report = run_benchmark(provider, settings.corpus_dir, out_dir, limit=args.limit)
    data = json.loads((out_dir / f"{report.run_id}.json").read_text())
    print(json.dumps(data["summary"], indent=2))
    return 0


def cmd_push_session(args) -> int:
    """Dry-run (default) or execute Mode A push for a local session."""
    from pdfsplit.aia.client import AiaApClient
    from pdfsplit.aia.push import push_session
    from pdfsplit.app.session import SessionStore

    dry_run = not bool(getattr(args, "push", False))
    erp = str(args.erp or "tally").lower()
    if erp not in ("tally", "zoho"):
        print("error: --erp must be tally or zoho", file=sys.stderr)
        return 2

    if not dry_run and not AiaApClient.live_writes_enabled(settings):
        print(
            "error: live push requires AIA_API_BASE and AIA_ALLOW_WRITES=true "
            "(omit --push for dry-run)",
            file=sys.stderr,
        )
        return 2

    store = SessionStore(settings.output_dir / "sessions")
    try:
        outcomes = push_session(
            args.session_id,
            dry_run=dry_run,
            erp=erp,  # type: ignore[arg-type]
            settings=settings,
            store=store,
            wait_for_hitl=bool(getattr(args, "wait", False)) and not dry_run,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if dry_run else "PUSH"
    print(f"[{mode}] session={args.session_id} erp={erp} invoices={len(outcomes)}")
    for o in outcomes:
        inv = o.invoice_number or "(no invoice #)"
        extra = ""
        if o.reject_reason is not None:
            extra = f" reason={o.reject_reason.code.value}"
        if o.duplicate_in_aia is True:
            extra += " duplicate=yes"
        if o.hitl_status:
            extra += f" hitl={o.hitl_status}"
        print(
            f"  {o.action:20s}  {o.file_name}  pages={o.page_count}  inv={inv}{extra}"
        )
    oversized = sum(1 for o in outcomes if o.action == "skipped_oversized")
    if oversized:
        print(
            f"oversized_count={oversized}  "
            f"(AIA limit {settings.max_downstream_pages}; not auto-split)"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdfsplit", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("build-corpus", help="Generate test corpus + ground truth")
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(func=cmd_build_corpus)

    s = sub.add_parser("stage3-physical", help="Physically split a PDF by ranges")
    s.add_argument("pdf", type=str)
    s.add_argument("--out", type=str, default="output/splits")
    s.add_argument("--pages", type=int, default=None)
    s.add_argument("--ranges", type=str, default=None, help="e.g. 1-3,4-6")
    s.set_defaults(func=cmd_stage3)

    s = sub.add_parser("stage5-benchmark", help="Run benchmark over corpus")
    s.add_argument(
        "--provider",
        choices=PROVIDER_NAMES,
        default="mistral",
        help="Provider to benchmark (mistral)",
    )
    s.add_argument("--limit", type=int, default=None)
    s.add_argument(
        "--sleep", type=float, default=0.0, help="sleep between packets (rate limiting)"
    )
    s.set_defaults(func=cmd_stage5)

    s = sub.add_parser(
        "boundary-benchmark",
        help=(
            "Score boundary accuracy on the synthetic corpus (scoring.py). "
            "SYNTHETIC — not a real-world rate."
        ),
    )
    s.add_argument(
        "--provider",
        choices=["mock", "mistral"],
        default="mock",
        help="mock = PDF text + heuristic/constraints (free); mistral = OCR+LLM",
    )
    s.add_argument(
        "--mode",
        choices=["llm", "heuristic"],
        default="llm",
        help="llm requires mistral; mock falls back to heuristic",
    )
    s.add_argument(
        "--no-layer1",
        action="store_true",
        help="Disable deterministic constraints",
    )
    s.add_argument(
        "--signature",
        action="store_true",
        help="Enable signature-block must_split (evaluate before shipping)",
    )
    s.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Also run without Layer 1 and report delta",
    )
    s.add_argument("--limit", type=int, default=None)
    s.set_defaults(func=cmd_boundary_benchmark)

    s = sub.add_parser("stage6-report", help="Render human-readable report")
    s.add_argument("--run", type=str, default=None)
    s.set_defaults(func=cmd_stage6)

    s = sub.add_parser(
        "prune-sessions",
        help="Delete all but the newest session under output/sessions/",
    )
    s.add_argument(
        "--keep",
        type=int,
        default=1,
        help="Number of newest sessions to keep (default 1)",
    )
    s.set_defaults(func=cmd_prune_sessions)

    s = sub.add_parser("smoke", help="End-to-end with mock provider (no credentials)")
    s.add_argument("--limit", type=int, default=3)
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(func=cmd_smoke)

    s = sub.add_parser(
        "e2e",
        help="Local HTTP e2e: upload → analyze → confirm → cut (fixtures; --live for Mistral)",
    )
    s.add_argument(
        "--live",
        action="store_true",
        help="Call live Mistral (costs money). Default is fixture replay.",
    )
    s.set_defaults(func=cmd_e2e)

    s = sub.add_parser("serve", help="Run the FastAPI web app (default port 7790)")
    s.add_argument("--host", type=str, default="0.0.0.0")
    s.add_argument("--port", type=int, default=7790)
    s.add_argument("--reload", action="store_true", help="Enable auto-reload")
    s.set_defaults(func=cmd_serve)


    s = sub.add_parser(
        "pack",
        help="Pack docs into <=N-page batches (session cache or fresh PDF)",
    )
    s.add_argument(
        "pdf",
        type=str,
        nargs="?",
        default=None,
        help="PDF path for a fresh provider run (omit when using --session)",
    )
    s.add_argument(
        "--session",
        type=str,
        default=None,
        help="Session id — pack from cached split (no re-extraction)",
    )
    s.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"Downstream page cap (default {settings.max_downstream_pages})",
    )
    s.add_argument(
        "--provider",
        choices=PROVIDER_NAMES,
        default="mistral",
        help="Provider for fresh PDF runs (mirrors stage5)",
    )
    s.add_argument(
        "--json",
        action="store_true",
        help="Also print the full manifest JSON",
    )
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser(
        "push-session",
        help=(
            "Push confirmed session invoices to AIA AP (Mode A). "
            "Dry-run by default — zero network calls with fixture transport."
        ),
    )
    s.add_argument("session_id", type=str, help="Session id under output/sessions")
    s.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report what would be pushed (default)",
    )
    s.add_argument(
        "--push",
        action="store_true",
        help="Execute Mode A push (requires AIA_ALLOW_WRITES for live host)",
    )
    s.add_argument(
        "--erp",
        choices=["tally", "zoho"],
        default="tally",
        help="ERP route family (default tally)",
    )
    s.add_argument(
        "--wait",
        action="store_true",
        help="After push, poll bill-status until hitl terminal (not for dry-run)",
    )
    s.set_defaults(func=cmd_push_session)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) == "pack":
        if not args.session and not args.pdf:
            parser.error("pack requires --session <id> or a <pdf> path")
        if args.session and args.pdf:
            parser.error("pack: pass either --session or a pdf path, not both")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
