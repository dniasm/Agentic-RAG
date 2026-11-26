"""Gzip (backend/compression.py): the site's scripts and pages compressed; the WebSocket, ranged requests, PDFs
and small bodies left alone (no API calls).

    python -m pytest backend/tests/test_compression.py
"""
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient

from backend.compression import MIN_SIZE, Compression
from backend.web import mount_frontend

GZ = {"Accept-Encoding": "gzip"}


def _site(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>" + "<p>x</p>" * 300, encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log('a long script');\n" * 400, encoding="utf-8")
    (dist / "assets" / "pdf.worker.min-abc.mjs").write_text("self.onmessage = () => {};\n" * 400, encoding="utf-8")
    text = tmp_path / "notes.txt"
    text.write_text("".join(f"line {i:05d}\n" for i in range(2000)), encoding="utf-8")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 20_000)
    app = FastAPI()

    @app.get("/api/small")
    def small():
        return {"ok": True}

    @app.get("/api/big")
    def big():
        return JSONResponse({"items": ["item"] * 2000})

    @app.get("/api/file")
    def file():
        return FileResponse(text, media_type="text/plain")

    @app.get("/api/pdf")
    def pdf_file():
        return FileResponse(pdf, media_type="application/pdf")

    mount_frontend(app, dist)
    app.add_middleware(Compression)
    return TestClient(app), text


def test_scripts_pages_and_json_are_compressed(tmp_path):
    client, _ = _site(tmp_path)
    for path in ("/", "/assets/index-abc.js", "/assets/pdf.worker.min-abc.mjs", "/api/big"):
        r = client.get(path, headers=GZ)
        assert r.headers.get("content-encoding") == "gzip", path
        assert int(r.headers["content-length"]) < len(r.content), path   # httpx has unzipped it: smaller on the wire
    # the worker keeps its JavaScript type (nosniff refuses a module worker served as anything else)
    assert client.get("/assets/pdf.worker.min-abc.mjs", headers=GZ).headers["content-type"].startswith("text/javascript")


def test_small_bodies_and_pdfs_are_sent_as_they_are(tmp_path):
    client, _ = _site(tmp_path)
    small = client.get("/api/small", headers=GZ)
    assert len(small.content) < MIN_SIZE and "content-encoding" not in small.headers
    assert "content-encoding" not in client.get("/api/pdf", headers=GZ).headers


def test_a_ranged_request_gets_exact_bytes(tmp_path):
    client, text = _site(tmp_path)
    r = client.get("/api/file", headers={**GZ, "Range": "bytes=100-199"})
    assert r.status_code == 206 and "content-encoding" not in r.headers
    assert r.headers["content-range"] == f"bytes 100-199/{text.stat().st_size}"
    assert r.content == text.read_bytes()[100:200]


def test_the_websocket_passes_through_compression_untouched():
    """The chat's answer arrives over the WebSocket as it is written: the gzip middleware must leave the socket's
    frames alone (it only handles HTTP responses), while the same app still compresses a big JSON body."""
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"type": "draft", "text": "x" * 5000})
        await websocket.close()

    @app.get("/api/big")
    def big():
        return JSONResponse({"items": ["item"] * 2000})

    app.add_middleware(Compression)
    client = TestClient(app)
    with client.websocket_connect("/ws") as sock:
        assert sock.receive_json() == {"type": "draft", "text": "x" * 5000}
    assert client.get("/api/big", headers=GZ).headers.get("content-encoding") == "gzip"
