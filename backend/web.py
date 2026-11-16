"""The built front end (frontend/dist, from `npm run build`), served by FastAPI in production.

index.html is sent no-cache, so a new build is picked up on the next load; the hashed files under assets/ never
change under their name, so the browser keeps them a year. Any other path that isn't /api is the single-page
app's own route (/documents, /chat/<id>) and gets index.html. Without a build nothing is mounted (in development
Vite serves the pages and proxies /api here).

The page is sent with a Content-Security-Policy: scripts only from this site, pdf.js's CDN and the page's own inline
theme script (allowed by its hash, read from the build), the PDF worker from a blob, styles and fonts from this site
and Google Fonts, requests only to this site, and no framing. An injected script or a stray external image has
nowhere to load from or send to.
"""
import base64
import hashlib
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
NO_CACHE = {"Cache-Control": "no-cache"}
LONG_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}
PDFJS_CDN = "https://cdn.jsdelivr.net"   # frontend/src/lib/pdfViewer.js loads pdf.js and its worker from here
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def content_security_policy(html: str) -> str:
    """The page's policy, allowing each inline <script> in `html` by its SHA-256 (the theme applied before paint)."""
    hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()}'"
                      for body in _INLINE_SCRIPT.findall(html))
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' {hashes} {PDFJS_CDN}".replace("  ", " "),
        "worker-src 'self' blob:",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",   # React, KaTeX and pdf.js set inline styles
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def _inside(root: Path, path: str) -> Path | None:
    """root/path when it is a file inside root (no ../ out of it), else None."""
    try:
        target = (root / path).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def mount_frontend(app: FastAPI, dist: Path = DIST) -> bool:
    """Serve `dist` at / when it holds a build. Call after every /api router is included. Returns whether it did."""
    index = dist / "index.html"
    if not index.is_file():
        return False
    page_headers = {**NO_CACHE, "Content-Security-Policy": content_security_policy(index.read_text(encoding="utf-8"))}

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):   # an /api route that doesn't exist: a JSON 404, not the page
            raise HTTPException(404, "Not Found")
        file = _inside(dist, path) if path else None
        if file is None or file == index.resolve():
            return FileResponse(index, headers=page_headers)
        return FileResponse(file, headers=LONG_CACHE if path.startswith("assets/") else NO_CACHE)

    return True
