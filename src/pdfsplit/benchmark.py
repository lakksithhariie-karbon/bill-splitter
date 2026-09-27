"""Benchmark harness.

Iterates a corpus manifest, calls a provider abstraction per packet, records
raw+normalized results, and scores against ground truth. Provider-agnostic:
any SplitterProvider can be plugged in without touching this module.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from pdfsplit.providers import SplitterProvider
from pdfsplit.schema import SplitResult
from pdfsplit.scoring import (
    DocTruth,
    boundary_metrics,
    calibration_curve,
    classification_accuracy,
    exact_doc_accuracy,
    perfect_packet,
)


@dataclass
class PacketRun:
    packet_id: str
    category: str
    scenario_tags: list[str]
    pages: int
    truth: list[DocTruth]
    result: SplitResult
    score: dict


@dataclass
class BenchmarkReport:
    provider: str
    model_version: str
    run_id: str
    runs: list[PacketRun] = field(default_factory=list)
    corpus_version: str | None = None

    def save(self, out_dir: Path, summary: dict) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "provider": self.provider,
            "model_version": self.model_version,
            "corpus_version": self.corpus_version,
            "summary": summary,
            "per_packet": [
                {
                    "packet_id": r.packet_id,
                    "category": r.category,
                    "scenario_tags": r.scenario_tags,
                    "pages": r.pages,
                    "perfect": r.score["perfect"],
                    "boundary_precision": r.score["boundary_precision"],
                    "boundary_recall": r.score["boundary_recall"],
                    "boundary_f1": r.score["boundary_f1"],
                    "exact_doc_accuracy": r.score["exact_doc_accuracy"],
                    "classification_accuracy": r.score["classification_accuracy"],
                    "latency_ms": r.result.latency_ms,
                    "errors": r.result.errors,
                    "predicted": [
                        {
                            "type": d.doc_type,
                            "start_page": d.start_page,
                            "end_page": d.end_page,
                            "confidence": d.confidence,
                        }
                        for d in r.result.documents
                    ],
                }
                for r in self.runs
            ],
        }
        path = out_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(payload, indent=2))
        return path


def load_manifest(corpus_dir: Path) -> dict:
    manifest_path = corpus_dir / "manifest.json"
    return json.loads(manifest_path.read_text())


def load_ground_truth(manifest: dict, packet: dict) -> list[DocTruth]:
    return [
        DocTruth(
            doc_type=d["class"],
            start_page=d["start_page"],
            end_page=d["end_page"],
        )
        for d in packet["expected_documents"]
    ]


def run_benchmark(
    provider: SplitterProvider,
    corpus_dir: Path,
    out_dir: Path,
    limit: int | None = None,
    sleep_s: float = 0.0,
) -> BenchmarkReport:
    manifest = load_manifest(corpus_dir)
    packets = manifest["packets"]
    if limit:
        packets = packets[:limit]

    runs: list[PacketRun] = []
    for packet in packets:
        pdf_path = corpus_dir / packet["file"]
        truth = load_ground_truth(manifest, packet)
        result = provider.split(pdf_path)
        score = score_packet(truth, result)
        runs.append(
            PacketRun(
                packet_id=packet["packet_id"],
                category=packet["category"],
                scenario_tags=packet["scenario_tags"],
                pages=packet["pages"],
                truth=truth,
                result=result,
                score=score,
            )
        )
        if sleep_s:
            time.sleep(sleep_s)

    report = BenchmarkReport(
        provider=provider.name,
        model_version=provider.model_version,
        run_id=f"{provider.name}_{provider.model_version}",
        runs=runs,
        corpus_version=manifest.get("corpus_version"),
    )
    summary = _summarize(report)
    report.save(out_dir, summary)
    return report


def score_packet(truth: list[DocTruth], result: SplitResult) -> dict:
    bm = boundary_metrics(truth, result.documents)
    return {
        **bm,
        "exact_doc_accuracy": round(exact_doc_accuracy(truth, result.documents), 4),
        "perfect": perfect_packet(truth, result.documents),
        "classification_accuracy": round(
            classification_accuracy(truth, result.documents), 4
        ),
    }


def _summarize(report: BenchmarkReport) -> dict:
    if not report.runs:
        return {}

    def avg(attr):
        vals = [r.score[attr] for r in report.runs]
        return round(statistics.mean(vals), 4) if vals else 0.0

    total_pages = sum(r.pages for r in report.runs)
    total_latency_ms = sum(r.result.latency_ms for r in report.runs)
    latency_vals = [r.result.latency_ms for r in report.runs if r.result.latency_ms > 0]

    def percentile(vals, p):
        if not vals:
            return 0.0
        s = sorted(vals)
        k = (len(s) - 1) * p
        f = int(k)
        return round(s[f], 1)

    # Per-category buckets.
    by_category: dict[str, dict] = {}
    for r in report.runs:
        b = by_category.setdefault(r.category, {"count": 0, "perfect": 0})
        b["count"] += 1
        b["perfect"] += int(r.score["perfect"])

    # Calibration curve over all packets.
    packets_for_cal = [
        (r.truth, r.result.documents) for r in report.runs if not r.result.errors
    ]

    return {
        "provider": report.provider,
        "model_version": report.model_version,
        "packets": len(report.runs),
        "pages": total_pages,
        "avg_boundary_precision": avg("boundary_precision"),
        "avg_boundary_recall": avg("boundary_recall"),
        "avg_boundary_f1": avg("boundary_f1"),
        "avg_exact_doc_accuracy": avg("exact_doc_accuracy"),
        "avg_classification_accuracy": avg("classification_accuracy"),
        "perfect_packet_rate": round(
            sum(1 for r in report.runs if r.score["perfect"]) / len(report.runs), 4
        ),
        "latency_ms_p50": percentile(latency_vals, 0.5),
        "latency_ms_p95": percentile(latency_vals, 0.95),
        "pages_per_minute": round(total_pages / (total_latency_ms / 60000.0), 2)
        if total_latency_ms
        else 0.0,
        "timeouts": sum(1 for r in report.runs if r.result.timeout),
        "provider_errors": sum(1 for r in report.runs if r.result.errors),
        "by_category": {
            k: {
                "packets": v["count"],
                "perfect_packet_rate": round(v["perfect"] / v["count"], 4),
            }
            for k, v in by_category.items()
        },
        "confidence_calibration": calibration_curve(packets_for_cal),
    }


def render_markdown(report: BenchmarkReport, summary: dict) -> str:
    lines = [
        f"# Benchmark Report — {summary['provider']} `{summary['model_version']}`",
        "",
        f"- Packets: {summary['packets']} | Pages: {summary['pages']}",
        f"- **Perfect Packet Rate: {summary['perfect_packet_rate']:.2%}**",
        f"- Boundary F1: {summary['avg_boundary_f1']:.3f} (P {summary['avg_boundary_precision']:.3f} / R {summary['avg_boundary_recall']:.3f})",
        f"- Exact Doc Accuracy: {summary['avg_exact_doc_accuracy']:.3f}",
        f"- Classification Accuracy: {summary['avg_classification_accuracy']:.3f}",
        f"- Latency p50: {summary['latency_ms_p50']}ms / p95: {summary['latency_ms_p95']}ms",
        f"- Pages/min: {summary['pages_per_minute']}",
        f"- Timeouts: {summary['timeouts']} | Provider errors: {summary['provider_errors']}",
        "",
        "## Confidence calibration",
        "",
        "| threshold | exact_doc_accuracy | coverage |",
        "|---|---|---|",
    ]
    for v in summary.get("confidence_calibration", {}).values():
        lines.append(
            f"| {v['threshold']:.2f} | {v['exact_doc_accuracy']:.3f} | {v['coverage']:.3f} |"
        )

    lines += [
        "",
        "## Per-category perfect-packet rate",
        "",
        "| category | packets | PPR |",
        "|---|---|---|",
    ]
    for cat, v in summary.get("by_category", {}).items():
        lines.append(f"| {cat} | {v['packets']} | {v['perfect_packet_rate']:.3f} |")

    lines += [
        "",
        "## Per-packet detail",
        "",
        "| packet | perfect | bF1 | exact | class | latency | errors |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report.runs:
        s = r.score
        lines.append(
            f"| {r.packet_id} | {s['perfect']} | {s['boundary_f1']:.2f} | "
            f"{s['exact_doc_accuracy']:.2f} | {s['classification_accuracy']:.2f} | "
            f"{r.result.latency_ms}ms | {','.join(r.result.errors) or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)
