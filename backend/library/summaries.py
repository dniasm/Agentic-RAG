"""A document's summary and keywords written again after a change, in the background and outside the library lock.

The summary is a model call (about 2,200 Groq tokens) that can also wait on the shared tokens-per-minute limiter, so
done under the lock it made every account's changes wait (measured: 4.0 s for a one-line PDF edit that refreshed it,
2.1 s for one that didn't). Now an edit, undo, redo or whole-text save only schedules it:
  * its own single worker, so uploads (the indexing queue) never wait behind a summary;
  * one pending refresh per document: a second change before the first refresh runs replaces it;
  * the model is called with no lock held; the lock is taken only to compare the document's hash with the one the
    summary was written from and to store it, so a later change wins;
  * a failed refresh keeps the old summary and logs one event; pending refreshes are lost on a restart.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from backend.library import catalog, config, library, titles
from backend.library.logs import event
from services.common.log import in_context

log = logging.getLogger(__name__)

_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="summary")
_pending: dict[str, str] = {}          # doc_id -> the file hash the summary is to be written from
_guard = threading.Lock()
_idle = threading.Event()
_idle.set()
_running = 0


def schedule(doc_id: str, sha256: str) -> None:
    """Write doc_id's summary again from its file as it is at sha256 (a later schedule for it replaces this one)."""
    global _running
    with _guard:
        queued = doc_id in _pending
        _pending[doc_id] = sha256
        if queued:
            return                     # its job is already waiting: it will use the newest hash
        _running += 1
        _idle.clear()
    _worker.submit(in_context(_run), doc_id)


def _run(doc_id: str) -> None:
    global _running
    try:
        with _guard:
            sha = _pending.pop(doc_id, None)
        if sha is not None:
            _refresh(doc_id, sha)
    except Exception:
        log.exception("Summary refresh of %s failed", doc_id)
        event("summary_refresh_failed", doc=doc_id)
    finally:
        with _guard:
            _running -= 1
            if _running == 0:
                _idle.set()


def _refresh(doc_id: str, sha: str) -> None:
    doc = catalog.get_document(doc_id)
    if not doc or doc["sha256"] != sha:
        return                         # changed (or removed) since: a newer refresh has its own job
    from backend.library import editing   # (imported here: editing imports this module)
    text = editing._plain_text(config.LIBRARY_DIR / doc["file_name"])
    others = [d["title"] for d in catalog.list_documents(owner=doc["owner"])
              if d["id"] != doc_id and d["status"] != "failed"]
    profile, written = titles.profile_document(doc["original_name"], text[: config.TITLE_SOURCE_CHARS], others)
    if not written:                    # the model couldn't be reached: keep the summary it had
        event("summary_refresh_failed", doc=doc_id, why="no answer from the model")
        return
    with library.WRITE_LOCK:           # only to check and store: the model call above held no lock
        now = catalog.get_document(doc_id)
        if now and now["sha256"] == sha:
            catalog.update_document(doc_id, summary=profile.summary, keywords=profile.keywords)
            event("summary_refreshed", doc=doc_id)


def wait_idle(timeout: float = 30.0) -> bool:
    """For tests: wait until no refresh is pending or running."""
    return _idle.wait(timeout)
