"""Gzip (backend/compression.py): the site's scripts and pages compressed; the chat's stream, ranged requests, PDFs
and small bodies left alone (no API calls).

    python -m pytest backend/tests/test_compression.py
"""
import time
from unittest import mock

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient

from backend.api.dependencies import get_current_user
from backend.api.routes import chat
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


def slow_agent(*args, **kwargs):
    yield "status", {"node": "router", "label": "Reading your message…"}
    time.sleep(0.5)   # the answer is still being written
    yield "final", {"answer": "The answer.", "tool": "direct", "intent": "direct", "trace": []}


def test_the_chat_stream_is_not_compressed_and_arrives_as_it_is_written():
    """Over a real local server: TestClient collects a whole response before handing it over, so it can't show
    whether events arrive as they are written."""
    import socket
    import threading

    import httpx
    import uvicorn

    app = FastAPI()
    app.include_router(chat.router)
    app.add_middleware(Compression)
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "tester"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    conv = {"id": "c1", "title": "t"}
    with mock.patch.object(chat, "_prepare", return_value=(conv, [], [], [], {"conversation_id": "c1"})),          mock.patch.object(chat, "stream_agent", side_effect=slow_agent),          mock.patch.object(chat, "_persist", return_value={"conversation_id": "c1", "message_id": "m1",
                                                            "answer": "The answer."}):
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            start = time.monotonic()
            with httpx.stream("POST", f"http://127.0.0.1:{port}/chat/stream", json={"message": "hi"},
                              headers=GZ, timeout=10) as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                assert "content-encoding" not in r.headers
                arrivals = [(time.monotonic() - start, chunk) for chunk in r.iter_raw()]
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    first_at, first = arrivals[0]
    final_at = next(t for t, c in arrivals if b"event: final" in c)
    assert b"event: meta" in first or b"event: status" in first
    assert b"event: final" not in first                      # not one block at the end
    assert final_at - first_at >= 0.4                        # the final event came the agent's pause after the first
