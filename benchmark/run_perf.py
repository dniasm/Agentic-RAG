"""System performance check (needs no Groq or Google quota).

Measures, in a fresh process:
  startup        import time and memory of the backend, Qdrant open, cross-encoder load
  rerank         cross-encoder latency for 10 / 20 / 40 candidates
  mmr            MMR selection latency (12 candidates -> 6)
  keyword search BM25 search latency
  concurrency    3 simultaneous "users" (keyword search + rerank) vs one at a time
  indexing       local part of an upload (read PDF, chunk, BM25 vectors) for the largest PDF

    python benchmark/run_perf.py
Run with the backend STOPPED (single-process embedded Qdrant).
"""
import ctypes
import json
import statistics
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

T0 = time.perf_counter()
BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))


class _MemCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def rss_mb() -> float | None:
    """Current memory (working set) of this process in MB, read from Windows; None elsewhere."""
    try:
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # 64-bit handle: must not be truncated to int
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemCounters), wintypes.DWORD]
        counters = _MemCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return round(counters.WorkingSetSize / 2**20, 1)
    except Exception:
        return None


def timed(fn, repeat=5):
    xs = []
    for _ in range(repeat):
        s = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - s)
    return round(statistics.median(xs) * 1000)  # ms


def main():
    report: dict = {"startup": {}}
    st = report["startup"]
    st["python start, memory MB"] = rss_mb()

    s = time.perf_counter()
    import backend.app  # noqa: F401  (everything the API imports)
    st["import backend: seconds"] = round(time.perf_counter() - s, 1)
    st["after import, memory MB"] = rss_mb()

    from backend.rag import rerank, sparse, vectorstore
    from backend.rag.ingestion import load_file, split_documents
    s = time.perf_counter()
    vectorstore.get_client()
    st["open Qdrant: seconds"] = round(time.perf_counter() - s, 1)
    st["after Qdrant, memory MB"] = rss_mb()

    s = time.perf_counter()
    rerank.ensure_loaded()
    st["load cross-encoder (incl. torch import): seconds"] = round(time.perf_counter() - s, 1)
    st["after cross-encoder, memory MB"] = rss_mb()
    st["process start -> ready: seconds"] = round(time.perf_counter() - T0, 1)

    from common import all_chunks, bench_user
    user = bench_user()
    chunks = all_chunks(user)
    query = "Which four categories are ISP complaints classified into?"

    report["rerank latency (ms, median of 5)"] = {
        f"{n} candidates": timed(lambda n=n: rerank.score(query, [dict(c, id=str(i)) for i, c in enumerate(chunks[:n])]))
        for n in (10, 20, 40)
    }

    pool_points = vectorstore.search_sparse(sparse.query_vector(query), user["id"], limit=12)
    pool = [{"id": str(p.id), "content": p.payload["page_content"]} for p in pool_points]
    scored = rerank.score(query, pool)
    report["MMR 12 -> 6 (ms, incl. fetching stored vectors)"] = timed(lambda: rerank.mmr_select(scored, k=6))
    report["keyword search (ms)"] = timed(
        lambda: vectorstore.search_sparse(sparse.query_vector(query), user["id"], limit=20))

    # Concurrency: what the shared embedded database and cross-encoder do under 3 simultaneous users.
    questions = ["Which four categories are ISP complaints classified into?",
                 "Which smoothing techniques are described for n-gram models?",
                 "What value is usually chosen for the b parameter in BM25?"]

    def one_user(q, out, i):
        s = time.perf_counter()
        try:
            pts = vectorstore.search_sparse(sparse.query_vector(q), user["id"], limit=20)
            rerank.score(q, [{"id": str(p.id), "content": p.payload["page_content"]} for p in pts])
            out[i] = time.perf_counter() - s
        except Exception as e:
            out[i] = f"ERROR {e}"

    seq = [None] * 3
    for i, q in enumerate(questions):
        one_user(q, seq, i)
    par = [None] * 3
    threads = [threading.Thread(target=one_user, args=(q, par, i)) for i, q in enumerate(questions)]
    s = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - s
    report["concurrency (search + rerank of 20)"] = {
        "one at a time, each (ms)": [round(x * 1000) for x in seq],
        "3 simultaneous, each (ms)": [round(x * 1000) if isinstance(x, float) else x for x in par],
        "3 simultaneous, total wall time (ms)": round(wall * 1000),
        "errors": sum(isinstance(x, str) for x in par),
    }

    pdf = BENCH_DIR.parent / "nlp.pdf"
    s = time.perf_counter()
    pages, _ = load_file(pdf)
    parts = split_documents(pages)
    t_split = time.perf_counter() - s
    s = time.perf_counter()
    for c in parts:
        sparse.document_vector(c.page_content)
    t_sparse = time.perf_counter() - s
    batches = -(-len(parts) // 90)
    report["upload of nlp.pdf (local work only)"] = {
        "chunks": len(parts), "read + chunk (s)": round(t_split, 1), "BM25 vectors (s)": round(t_sparse, 2),
        "embedding: Google calls (1 per chunk)": len(parts),
        "embedding: estimated wait under the free tier's 100/min limit (s)": (batches - 1) * 60,
    }

    out = BENCH_DIR / "results" / "perf.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
