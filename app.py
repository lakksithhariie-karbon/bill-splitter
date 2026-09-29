"""Vercel entrypoint for the existing FastAPI application."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pdfsplit.app.main import app

__all__ = ["app"]
