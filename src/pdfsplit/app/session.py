"""Session storage for the web app.

A session is created on upload and holds:
  - the uploaded PDF (bytes + original filename metadata)
  - the page count
  - the cached SplitResult (never call the provider twice)
  - the user's corrected documents (in-memory until Save)

Sessions live under output/sessions/{id}/. Edits live in browser memory until
Save; the server only persists the upload, the split cache, and the final
corrections.json + split PDFs.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from pdfsplit.schema import SplitDocument, SplitResult
from pdfsplit.extraction import ExtractionResult


@dataclass
class Session:
    id: str
    dir: Path
    filename: str
    page_count: int
    split_result: SplitResult | None = None
    extraction_result: ExtractionResult | None = None
    ocr_response: dict | None = None
    corrected: list[SplitDocument] | None = None

    @property
    def pdf_path(self) -> Path:
        return self.dir / "upload.pdf"

    @property
    def meta_path(self) -> Path:
        """Original upload metadata (filename, page_count) — survives get()."""
        return self.dir / "session_meta.json"

    @property
    def split_cache_path(self) -> Path:
        return self.dir / "split_result.json"

    @property
    def extraction_cache_path(self) -> Path:
        return self.dir / "extraction_result.json"

    @property
    def ocr_cache_path(self) -> Path:
        return self.dir / "ocr_response.json"

    @property
    def corrections_path(self) -> Path:
        return self.dir / "corrections.json"

    @property
    def documents_dir(self) -> Path:
        return self.dir / "documents"

    def has_split(self) -> bool:
        return self.split_result is not None

    def has_ocr(self) -> bool:
        return self.ocr_response is not None

    def has_analyze(self) -> bool:
        """True when stage-1 OCR + boundary result are both cached."""
        return self.has_ocr() and self.has_split()


class SessionStore:
    """Filesystem-backed session store under output/sessions/."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def prune_old_sessions(self, keep: int = 1) -> tuple[list[str], list[str]]:
        """Keep the ``keep`` newest session dirs; delete the rest.

        Newest is by directory mtime. Returns ``(kept_ids, removed_ids)``.
        """
        keep_n = max(1, int(keep))
        dirs = [p for p in self.root.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        kept_dirs = dirs[:keep_n]
        removed_dirs = dirs[keep_n:]
        for d in removed_dirs:
            shutil.rmtree(d, ignore_errors=True)
        return [d.name for d in kept_dirs], [d.name for d in removed_dirs]

    def create(self, filename: str, data: bytes, page_count: int) -> Session:
        sid = uuid.uuid4().hex[:12]
        sdir = self.root / sid
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "upload.pdf").write_bytes(data)
        session = Session(
            id=sid, dir=sdir, filename=filename, page_count=page_count
        )
        self._write_meta(session)
        return session

    def _write_meta(self, session: Session) -> None:
        payload = {
            "filename": session.filename,
            "page_count": int(session.page_count),
        }
        session.meta_path.write_text(json.dumps(payload, indent=2))

    def get(self, sid: str) -> Session | None:
        sdir = self.root / sid
        if not sdir.is_dir():
            return None
        # Prefer persisted upload name; fall back for pre-metadata sessions.
        filename = "upload.pdf"
        page_count = 0
        meta_path = sdir / "session_meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
                raw_name = meta.get("filename")
                if isinstance(raw_name, str) and raw_name.strip():
                    filename = raw_name.strip()
                if meta.get("page_count") is not None:
                    page_count = int(meta["page_count"])
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        session = Session(
            id=sid,
            dir=sdir,
            filename=filename,
            page_count=page_count,
        )
        # Rehydrate page count from the PDF if not known.
        if session.page_count == 0:
            from pypdf import PdfReader

            session.page_count = len(PdfReader(str(session.pdf_path)).pages)
        # Rehydrate cached OCR response (page markdown for stage-2 extract).
        if session.ocr_cache_path.exists():
            session.ocr_response = json.loads(session.ocr_cache_path.read_text())
        # Rehydrate cached split result.
        if session.split_cache_path.exists():
            data = json.loads(session.split_cache_path.read_text())
            session.split_result = SplitResult(**data)
        # Rehydrate cached extraction result.
        if session.extraction_cache_path.exists():
            data = json.loads(session.extraction_cache_path.read_text())
            session.extraction_result = ExtractionResult(**data)
        return session

    def save_ocr(self, session: Session, ocr_response: dict) -> None:
        session.ocr_response = ocr_response
        session.ocr_cache_path.write_text(
            json.dumps(ocr_response, indent=2, default=str)
        )

    def save_split(self, session: Session, result: SplitResult) -> None:
        session.split_result = result
        session.split_cache_path.write_text(
            json.dumps(result.model_dump(), indent=2, default=str)
        )

    def save_extraction(self, session: Session, result: ExtractionResult) -> None:
        session.extraction_result = result
        session.extraction_cache_path.write_text(
            json.dumps(result.model_dump(), indent=2, default=str)
        )

    def save_corrections(
        self,
        session: Session,
        corrected: list[SplitDocument],
        correction_types: list[str],
        field_corrections: list[dict] | None = None,
        excluded_pages: list[int] | None = None,
    ) -> Path:
        """Write corrections.json in a shape consumable by scoring.py.

        scoring.py's score_packet(truth, pred) expects:
          - truth: list[DocTruth]  (doc_type, start_page, end_page)
          - pred:  list[SplitDocument]
        We store both the provider's predicted documents and the user's
        corrected documents, plus per-correction types. Four types are kept
        distinct so we can tell a bad cut from a bad read:
          - BOUNDARY: a cut was moved/added/removed
          - CLASS:    a label changed on an unchanged range
          - FIELD:    an extracted field value was edited (page, field name,
                      model value, user value)
          - EXCLUDE:  a page was deliberately dropped (not an invoice)
        """
        predicted = session.split_result.documents if session.split_result else []
        payload = {
            "input_file": session.filename,
            "provider": session.split_result.provider if session.split_result else None,
            "model_version": (
                session.split_result.model_version if session.split_result else None
            ),
            "page_count": session.page_count,
            "predicted_documents": [
                {
                    "doc_type": d.doc_type,
                    "start_page": d.start_page,
                    "end_page": d.end_page,
                    "confidence": d.confidence,
                }
                for d in predicted
            ],
            "corrected_documents": [
                {
                    "doc_type": d.doc_type,
                    "start_page": d.start_page,
                    "end_page": d.end_page,
                    "confidence": d.confidence,
                }
                for d in corrected
            ],
            "excluded_pages": list(excluded_pages or []),
            "correction_types": correction_types,
            "field_corrections": field_corrections or [],
        }
        session.corrected = corrected
        session.corrections_path.write_text(json.dumps(payload, indent=2))
        return session.corrections_path
