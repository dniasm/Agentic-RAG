"""Complete fonts downloaded when a PDF needs one this machine doesn't have, instead of committed to the repository.

fonts/manifest.json (written by backend/scripts/build_font_manifest.py) lists every font that may be fetched:
LaTeX's cm-super fonts (SFRM1095, ...), and free copies of Microsoft's fonts with the same letter widths (Liberation
for Arial, Times New Roman and Courier New; Carlito for Calibri; Caladea for Cambria). Each has one fixed HTTPS URL
and a SHA-256; a download that doesn't match is thrown away before anything reads it.

  * prefetch(names): downloads what is missing, in parallel, within a few seconds in all. It is called before an
    edit takes the library lock, so a slow server never holds up other document operations. Any failure (offline,
    slow, wrong hash, too big) just leaves the font missing: the edit then goes as it did before this existed.
  * cached(name): the downloaded copy, if there is one. Never touches the network (safe under the lock).

Downloads are kept in data/fonts/fetched (git-ignored with the rest of backend/data).
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.library import config

log = logging.getLogger(__name__)

MANIFEST = Path(__file__).parent / "fonts" / "manifest.json"
CACHE = config.DATA_DIR / "fonts" / "fetched"
TIMEOUT = 4.0                   # seconds per request, and for a whole prefetch
MAX_BYTES = 8_000_000           # no font or archive listed is larger
RETRY_AFTER = 600.0             # a failed source isn't asked again for ten minutes

_failed: dict[str, float] = {}          # file (or archive source) -> when it last failed
_verified: set[Path] = set()            # cached files whose hash was checked in this process
_locks: dict[str, threading.Lock] = {}  # one download of a file (or an archive) at a time
_locks_lock = threading.Lock()


class FetchError(Exception):
    pass


@functools.cache
def manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sources": {}, "files": {}, "names": {}}


def file_for(name: str) -> str | None:
    """The manifest's file for a PDF font name, by font_key ("Arial-BoldMT" -> "LiberationSans-Bold.ttf")."""
    from backend.library.pdf_edit import font_key
    return manifest()["names"].get(font_key(name))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cached(name: str) -> Path | None:
    """The downloaded copy of the font for `name`, when it is there and its hash is right. No network."""
    file = file_for(name)
    if not file:
        return None
    path = CACHE / file
    if path in _verified:
        return path
    try:
        ok = _sha(path.read_bytes()) == manifest()["files"][file]["sha256"]
    except OSError:
        return None
    if ok:
        _verified.add(path)
        return path
    path.unlink(missing_ok=True)        # damaged or changed: fetched again next time
    return None


def stand_in(path: Path | None) -> bool:
    """Whether a font file is a downloaded look-alike of another font (Liberation Sans for Arial, ...)."""
    if path is None or Path(path).parent != CACHE:
        return False
    entry = manifest()["files"].get(Path(path).name)
    return bool(entry and manifest()["sources"].get(entry["source"], {}).get("stand_in"))


# ---------------------------------------------------------------- downloading
def _download(url: str, size_cap: int, redirect_host: str | None = None) -> bytes:
    """GET one URL over HTTPS, no redirects except one to `redirect_host` (GitHub's release files), at most
    size_cap bytes. Kept separate so tests replace it and never touch the network."""
    for _ in range(2):
        if urlsplit(url).scheme != "https":
            raise FetchError(f"not HTTPS: {url}")
        with httpx.stream("GET", url, timeout=TIMEOUT, follow_redirects=False) as r:
            if r.is_redirect and redirect_host and urlsplit(r.headers.get("location", "")).hostname == redirect_host:
                url, redirect_host = r.headers["location"], None
                continue
            if r.status_code != 200:
                raise FetchError(f"HTTP {r.status_code}")
            data = bytearray()
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > size_cap:
                    raise FetchError("larger than expected")
            return bytes(data)
    raise FetchError("redirected more than once")


def _store(file: str, data: bytes) -> None:
    entry = manifest()["files"][file]
    if _sha(data) != entry["sha256"]:
        raise FetchError(f"{file}: hash mismatch")
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / (file + ".part")
    tmp.write_bytes(data)
    tmp.replace(CACHE / file)
    _verified.add(CACHE / file)


def _fetch_source(source_name: str, file: str) -> None:
    """Download `file` (and, for an archive, every font listed from it) and store it after checking its hash."""
    source = manifest()["sources"][source_name]
    entry = manifest()["files"][file]
    if "member" not in entry:
        _store(file, _download(source["url"].format(file=file), min(entry["size"], MAX_BYTES),
                               source.get("redirect_host")))
        return
    archive = _download(source["url"], min(source["size"], MAX_BYTES), source.get("redirect_host"))
    if _sha(archive) != source["sha256"]:           # checked before the archive is opened
        raise FetchError(f"{source_name}: archive hash mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for other, e in manifest()["files"].items():
            if e["source"] == source_name and e.get("member"):
                member = tar.extractfile(e["member"])
                if member is not None:
                    _store(other, member.read())


def fetch(name: str) -> Path | None:
    """The font for `name`: from the cache, else downloaded now. None when it isn't listed or can't be had."""
    path = cached(name)
    if path or not (file := file_for(name)):
        return path
    entry = manifest()["files"][file]
    source = entry["source"]
    key = source if "member" in entry else file     # an archive brings all its fonts in one download
    with _locks_lock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if (path := cached(name)) is not None:
            return path
        if time.monotonic() - _failed.get(key, -RETRY_AFTER) < RETRY_AFTER:
            return None
        try:
            _fetch_source(source, file)
        except Exception as e:      # offline, slow, wrong hash, too large: the font stays missing
            _failed[key] = time.monotonic()
            log.warning("Could not fetch font %s (%s): %s", file, source, e)
            return None
    log.info("Fetched font %s for %r", file, name)
    return cached(name)


def prefetch(names) -> None:
    """Download the listed fonts for these PDF font names that aren't here yet, within TIMEOUT seconds overall."""
    wanted = {n for n in names if file_for(n) and not cached(n)}
    if not wanted:
        return
    pool = ThreadPoolExecutor(max_workers=4)
    futures = [pool.submit(fetch, n) for n in wanted]
    wait(futures, timeout=TIMEOUT)
    pool.shutdown(wait=False)       # a download still running finishes on its own; the edit doesn't wait for it
