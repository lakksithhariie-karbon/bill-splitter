"""Page rasterization to PNG using pypdfium2 (permissive licence).

Renders a page of a PDF to a PNG at a requested DPI. Renders are cached to
disk under the session so repeated requests (filmstrip thumb + main pane) do
not re-render.

pypdfium2 / PDFium is not safe for concurrent use from multiple threads in
the same process. FastAPI runs sync route handlers on a threadpool, and the
filmstrip fires many /page/*.png requests in parallel — without a lock that
segfaults libpdfium.so and kills uvicorn, which the browser surfaces as
"Failed to fetch".
"""

from __future__ import annotations

import threading
from pathlib import Path

import pypdfium2 as pdfium

#: Rough DPI for the filmstrip thumbnails.
THUMB_DPI = 120
#: Rough DPI for the main pane.
VIEW_DPI = 150

#: Serialize all PdfDocument open/render/close calls process-wide.
_RENDER_LOCK = threading.Lock()


def render_page(
    pdf_path: Path, page_index: int, out_path: Path, dpi: int
) -> Path:
    """Render page `page_index` (0-based) of `pdf_path` to `out_path` at `dpi`.

    Returns the output path. The caller is responsible for ensuring the parent
    directory exists.
    """
    with _RENDER_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            page = pdf[page_index]
            scale = dpi / 72.0
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            pil.save(out_path, format="PNG")
        finally:
            pdf.close()
    return out_path


def ensure_rendered(
    pdf_path: Path, page_index: int, cache_dir: Path, dpi: int
) -> Path:
    """Render a page if not already cached, then return the cached path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"page_{page_index + 1:03d}_{dpi}dpi.png"
    if not out.exists():
        render_page(pdf_path, page_index, out, dpi)
    return out
