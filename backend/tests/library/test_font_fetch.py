"""Fonts a PDF needs and the machine lacks are downloaded on demand (not committed): hash-checked before use, from
one fixed HTTPS URL, outside the library lock, and any failure leaves the edit exactly as it was before.
No test here touches the network: downloads are faked (conftest makes the real one fail)."""
import hashlib
import io
import re
import tarfile
import threading
import time

import httpx
import pymupdf
import pytest

from backend.library import editing, font_fetch, library, pdf_edit
from backend.tests.library.test_tiny_font import CMR10

REAL_DOWNLOAD = font_fetch._download        # taken before conftest's fixture replaces it
FONT = CMR10.read_bytes()                   # stands in for SFRM1095 (any Type 1 font with every letter)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A one-font manifest, an empty cache, and a download that serves `served` and counts its calls."""
    m = {"sources": {"cm-super": {"url": "https://ctan.example/pfb/{file}"}},
         "files": {"sfrm1095.pfb": {"source": "cm-super", "sha256": _sha(FONT), "size": len(FONT)}},
         "names": {"sfrm1095": "sfrm1095.pfb"}}
    state = {"served": FONT, "calls": 0, "gate": None, "done": threading.Event()}

    def download(url, cap, redirect_host=None):
        state["calls"] += 1
        try:
            if state["gate"]:
                state["gate"].wait(5)
            return state["served"]
        finally:
            state["done"].set()
    monkeypatch.setattr(font_fetch, "manifest", lambda: m)
    monkeypatch.setattr(font_fetch, "CACHE", tmp_path / "fetched")
    monkeypatch.setattr(font_fetch, "_verified", set())
    monkeypatch.setattr(font_fetch, "_download", download)
    state["manifest"] = m
    return state


def test_a_font_is_downloaded_once_checked_and_then_read_from_the_cache(fake):
    path = font_fetch.fetch("MRRPUK+SFRM1095")
    assert path and path.read_bytes() == FONT and fake["calls"] == 1
    font_fetch._verified.clear()                                 # a new process: the cached copy is checked again
    assert font_fetch.fetch("SFRM1095") == path and fake["calls"] == 1


def test_a_download_with_the_wrong_hash_is_thrown_away_and_not_retried_at_once(fake):
    fake["served"] = FONT[:-10] + b"x" * 10
    assert font_fetch.fetch("SFRM1095") is None
    assert not list(font_fetch.CACHE.glob("*.pfb"))
    assert font_fetch.fetch("SFRM1095") is None and fake["calls"] == 1      # the failure is remembered


def test_a_cached_file_that_changed_is_removed(fake):
    path = font_fetch.fetch("SFRM1095")
    path.write_bytes(b"not a font")
    font_fetch._verified.clear()
    assert font_fetch.cached("SFRM1095") is None and not path.exists()


def test_fonts_not_listed_are_never_fetched(fake):
    assert font_fetch.fetch("SomeUnknownFont") is None and fake["calls"] == 0


def test_an_archive_is_checked_before_it_is_opened(fake, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("lib/LiberationSans-Regular.ttf")
        info.size = len(FONT)
        tar.addfile(info, io.BytesIO(FONT))
    archive = buf.getvalue()
    m = fake["manifest"]
    m["sources"]["liberation"] = {"url": "https://github.example/lib.tar.gz", "sha256": _sha(archive),
                                  "size": len(archive)}
    m["files"]["LiberationSans-Regular.ttf"] = {"source": "liberation", "member": "lib/LiberationSans-Regular.ttf",
                                                "sha256": _sha(FONT), "size": len(FONT)}
    m["names"]["arial"] = "LiberationSans-Regular.ttf"
    fake["served"] = archive[:20] + bytes([archive[20] ^ 0xFF]) + archive[21:]
    opened = []
    real_open = tarfile.open
    monkeypatch.setattr(tarfile, "open", lambda *a, **k: (opened.append(1), real_open(*a, **k))[1])
    assert font_fetch.fetch("ArialMT") is None and opened == []          # a changed archive is never parsed
    font_fetch._failed.clear()
    fake["served"] = archive
    assert font_fetch.fetch("ArialMT").name == "LiberationSans-Regular.ttf"


def test_prefetch_gives_up_after_its_time_limit(fake, monkeypatch):
    monkeypatch.setattr(font_fetch, "TIMEOUT", 0.2)
    fake["gate"] = threading.Event()                 # the server doesn't answer until the test lets it
    started = time.monotonic()
    font_fetch.prefetch(["SFRM1095"])
    assert time.monotonic() - started < 1.0
    fake["gate"].set()                               # the download left running finishes before the next test
    assert fake["done"].wait(5)


def _serve(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(font_fetch.httpx, "stream", client.stream)


def test_downloads_follow_no_redirect_but_the_one_allowed_and_stop_at_the_size_cap(monkeypatch):
    def handler(request):
        if request.url.host == "files.example":
            return httpx.Response(302, headers={"location": "https://storage.example/x"})
        if request.url.host == "mirror.example":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, content=b"f" * 100)
    _serve(monkeypatch, handler)
    assert REAL_DOWNLOAD("https://files.example/a", 1000, "storage.example") == b"f" * 100
    with pytest.raises(font_fetch.FetchError):
        REAL_DOWNLOAD("https://mirror.example/a", 1000, "storage.example")    # a redirect elsewhere
    with pytest.raises(font_fetch.FetchError):
        REAL_DOWNLOAD("https://files.example/a", 1000)                        # no redirect allowed at all
    with pytest.raises(font_fetch.FetchError):
        REAL_DOWNLOAD("https://storage.example/a", 50)                        # bigger than the cap
    with pytest.raises(font_fetch.FetchError):
        REAL_DOWNLOAD("http://storage.example/a", 1000)                       # not HTTPS


def _sfrm_pdf(path, text, name="SFRM1095"):
    """A page in a font subset named `name` with only the letters of `text` (as LaTeX's T1 fonts come out)."""
    from fontTools.ttLib import TTFont
    from backend.library.tiny_font import tiny_font
    font = TTFont(io.BytesIO(tiny_font(CMR10, text)))
    for record in font["name"].names:
        if record.nameID in (1, 4, 6):
            record.string = name
        elif record.nameID == 2:
            record.string = "Regular"
    buf = io.BytesIO()
    font.save(buf)
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    tw = pymupdf.TextWriter(page.rect)
    tw.append((72, 100), text, font=pymupdf.Font(fontbuffer=buf.getvalue()), fontsize=11)
    tw.write_text(page)
    d.save(str(path), garbage=4, deflate=True)


def test_an_edit_needing_a_downloaded_font_works_and_without_it_is_refused_as_before(fake, tmp_path):
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _sfrm_pdf(src, "Profile text that will change")
    fake["served"] = b"offline"                                          # the download fails
    pdf_edit.prefetch_fonts(src)
    with pytest.raises(pdf_edit.MissingLetters):
        pdf_edit.replace(src, out, re.compile(r"text"), "jest", False)
    font_fetch._failed.clear()
    fake["served"] = FONT
    pdf_edit.prefetch_fonts(src)
    pdf_edit.replace(src, out, re.compile(r"text"), "jest", False)
    assert "Profile jest that will" in " ".join(pymupdf.open(out)[0].get_text().split())


def test_the_user_is_told_when_a_look_alike_font_draws_the_new_text(fake, tmp_path):
    m = fake["manifest"]
    m["sources"]["liberation"] = {"url": "https://x.example/{file}", "stand_in": True}
    m["files"]["StandIn-Regular.pfb"] = {"source": "liberation", "sha256": _sha(FONT), "size": len(FONT)}
    m["names"]["wobble"] = "StandIn-Regular.pfb"
    src, out, info = tmp_path / "a.pdf", tmp_path / "b.pdf", {}
    _sfrm_pdf(src, "Profile text that will change", name="Wobble")
    pdf_edit.prefetch_fonts(src)
    pdf_edit.replace(src, out, re.compile(r"text"), "jest", False, info)
    assert info["stand_in"] == {"Wobble": pymupdf.Font(fontfile=str(CMR10)).name}
    from backend.library import agent
    reply = agent.compose_reply([{"status": "edited", "title": "cv", "operation": "replace", "changes": 1,
                                  "previews": [], "stand_in": info["stand_in"]}])
    assert "Wobble isn't available here" in reply


def test_the_download_happens_outside_the_library_lock(monkeypatch):
    seen = {}

    def free():         # another thread can take the lock: nobody holds it
        got = library.WRITE_LOCK.acquire(timeout=0.5)
        if got:
            library.WRITE_LOCK.release()
        return got

    def prefetch(path):
        t = threading.Thread(target=lambda: seen.setdefault("prefetch", free()))
        t.start(), t.join()

    def locked(*a, **k):
        t = threading.Thread(target=lambda: seen.setdefault("edit", free()))
        t.start(), t.join()
        return {"status": "edited"}
    monkeypatch.setattr(editing.catalog, "get_document", lambda _id: {"ext": ".pdf", "file_name": "x.pdf"})
    monkeypatch.setattr(editing.pdf_edit, "prefetch_fonts", prefetch)
    monkeypatch.setattr(editing, "_edit_locked", library._exclusive(locked))
    editing.edit("doc", "replace", find="a", text="b")
    assert seen == {"prefetch": True, "edit": False}
