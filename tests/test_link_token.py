"""Link-token gate and upload page-cap tests."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def test_healthz_and_robots_open_without_token(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "secret-token-for-tests")
    from pdfsplit.app.main import app

    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /" in r.text


def test_missing_token_returns_404(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "secret-token-for-tests")
    from pdfsplit.app.main import app

    client = TestClient(app)
    assert client.get("/").status_code == 404
    assert client.get("/api/aia/status").status_code == 404


def test_query_token_sets_cookie_and_redirects(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "secret-token-for-tests")
    from pdfsplit.app.main import app

    client = TestClient(app, follow_redirects=False)
    r = client.get("/?k=secret-token-for-tests")
    assert r.status_code == 302
    assert "demo_k=" in r.headers.get("set-cookie", "")
    assert "k=" not in (r.headers.get("location") or "")


def test_cookie_grants_access(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "secret-token-for-tests")
    from pdfsplit.app.main import app

    client = TestClient(app)
    # Establish cookie via share link, then hit API.
    client.get("/?k=secret-token-for-tests")
    r = client.get("/api/aia/status")
    assert r.status_code == 200
    assert "push_enabled" in r.json()


def test_upload_rejects_over_page_cap(monkeypatch, tmp_path):
    monkeypatch.delenv("DEMO_TOKEN", raising=False)
    from pdfsplit.app import main as main_mod
    from pdfsplit.config import Settings

    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(
        main_mod,
        "settings",
        replace(Settings(), max_upload_pages=2, output_dir=out),
    )
    monkeypatch.setattr(
        main_mod,
        "store",
        main_mod.SessionStore(out / "sessions"),
    )

    pdf = tmp_path / "three.pdf"
    c = canvas.Canvas(str(pdf), pagesize=letter)
    for i in range(3):
        c.drawString(72, 720, f"page {i + 1}")
        c.showPage()
    c.save()

    client = TestClient(main_mod.app)
    with pdf.open("rb") as f:
        r = client.post(
            "/api/upload", files={"file": ("three.pdf", f, "application/pdf")}
        )
    assert r.status_code == 400
    assert "upload limit is 2" in r.json()["detail"]
