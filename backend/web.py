"""The built front end (frontend/dist, from `npm run build`), served by FastAPI in production.

index.html is sent no-cache, so a new build is picked up on the next load; the hashed files under assets/ never
change under their name, so the browser keeps them a year. Any other path that isn't /api is the single-page
app's own route (/documents, /chat/<id>) and gets index.html. Without a build nothing is mounted (in development
Vite serves the pages and proxies /api here).
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
NO_CACHE = {"Cache-Control": "no-cache"}
LONG_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


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

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):   # an /api route that doesn't exist: a JSON 404, not the page
            raise HTTPException(404, "Not Found")
        file = _inside(dist, path) if path else None
        if file is None:
            return FileResponse(index, headers=NO_CACHE)
        if file.name == "index.html":
            return FileResponse(file, headers=NO_CACHE)
        return FileResponse(file, headers=LONG_CACHE if path.startswith("assets/") else NO_CACHE)

    return True
