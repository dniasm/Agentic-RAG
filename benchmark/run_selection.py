"""Passage-selection benchmark: which way of choosing the final passages works best for each answer shape?

Phase 1 (--collect, needs the backend STOPPED and ~1 embedding request per new question):
    For every question, run the real hybrid search + cross-encoder and save the 20 reranked candidates
    with their stored dense vectors to results/selection_pool.json / .npz.
Phase 2 (--compare, no network, no quota, no Qdrant):
    Run every selection method and parameter setting on the saved pools and score them.

Methods
    A  relevance only           top-k by cross-encoder relevance
    B  MMR                      lambda*rel - (1-lambda)*max_sim (raw scores, today's code)
    C  normalised MMR           same, but relevance and similarity rescaled to 0-1 inside each pool
                                (raw cross-encoder scores are tiny, e.g. 0.002-0.9, so in plain MMR the
                                similarity term usually dominates)
    D  merge                    relevance gate -> merge document neighbours (max 2 chunks per passage,
                                the merged chunk frees its slot) -> sentence de-duplication -> token budget
    E  D + normalised MMR order
    F  DPP                      greedy determinant maximisation: quality x diversity in one kernel
    G  D + DPP order

Scores (per question; out-of-scope questions are skipped: the relevance zones handle them)
    coverage   share of the question's aspects (required facts) present in the selected text
    precision  share of selected passages that are relevant (from a gold source and containing a gold phrase)
    F1         harmonic mean of coverage and precision: a complete answer from as little noise as possible.
               Ties are broken by the larger sum, then by fewer tokens.
    alpha-nDCG novelty-aware ranking quality (a passage only earns credit for aspects not seen before)
    diversity  1 - mean pairwise cosine of the selected passages (diagnostic)
    tokens     context size sent to the large model (o200k tokenizer, the gpt-oss family)

    python benchmark/run_selection.py --collect
    python benchmark/run_selection.py --compare
"""
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BENCH_DIR, _norm, load_questions  # noqa: E402

RESULTS = BENCH_DIR / "results"
POOL_JSON, POOL_NPZ = RESULTS / "selection_pool.json", RESULTS / "selection_pool.npz"


# ================================================================ phase 1: collect
def collect(cached_only: bool = False) -> None:
    """cached_only: don't index missing documents or embed new questions (for when the daily quota is spent);
    only questions whose embedding is already cached are collected."""
    from common import bench_user, ensure_corpus

    from backend.config.settings import settings
    from backend.rag import embedding_cache, rerank, vectorstore
    from backend.rag.retrieval import retrieve

    user = bench_user()
    if not cached_only:
        ensure_corpus(user)
    if not rerank.ensure_loaded():
        raise RuntimeError("cross-encoder could not be loaded")
    before = embedding_cache.stats["misses"]
    pools, vectors = {}, {}
    for q in load_questions():
        if cached_only and not embedding_cache._db().execute(
                "SELECT 1 FROM query_embeddings WHERE key = ?", (embedding_cache._key(q["question"]),)).fetchone():
            print(f"  {q['id']:<24} skipped (question not embedded yet)")
            continue
        hits = retrieve([q["question"]], user["id"], k=settings.rerank_candidates - 2, min_score=0.0, mode="hybrid")
        hits = rerank.score(q["question"], hits)
        vecs = vectorstore.get_dense_vectors([h["id"] for h in hits])
        pools[q["id"]] = [{k: h.get(k) for k in ("id", "source", "page", "doc_id", "chunk_index", "content",
                                                  "relevance", "rrf")} for h in hits]
        vectors[q["id"]] = np.asarray([v if v is not None else [0.0] * settings.embedding_dim for v in vecs], dtype=np.float32)
        best = hits[0]["relevance"] if hits else 0
        print(f"  {q['id']:<24} {len(hits):>2} candidates, best relevance {best:.4f}")
    RESULTS.mkdir(exist_ok=True)
    POOL_JSON.write_text(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"), "pools": pools},
                                    ensure_ascii=False), encoding="utf-8")
    np.savez_compressed(POOL_NPZ, **{k.replace("-", "_"): v for k, v in vectors.items()})
    print(f"saved {len(pools)} pools; embedding requests: {embedding_cache.stats['misses'] - before}")


# ================================================================ text helpers
_enc = None


def tokens(text: str) -> int:
    global _enc
    if _enc is None:
        import tiktoken
        _enc = tiktoken.get_encoding("o200k_base")
    return len(_enc.encode(text))


def join_overlapping(a: str, b: str, max_overlap: int = 400) -> str:
    """Join two neighbouring chunks, removing the text they share (chunks overlap by up to 200 chars)."""
    for n in range(min(max_overlap, len(a), len(b)), 20, -1):
        if a.endswith(b[:n]):
            return a + b[n:]
    return a.rstrip() + " " + b.lstrip()


_SENT = re.compile(r"(?<=[.!?])\s+")


def dedup_sentences(passages: list[dict]) -> None:
    """Drop sentences that already appeared in an earlier passage (in place)."""
    seen = set()
    for p in passages:
        kept = []
        for s in _SENT.split(p["text"]):
            key = _norm(s).strip()
            if len(key) > 30 and key in seen:
                continue
            seen.add(key)
            kept.append(s)
        p["text"] = " ".join(kept)


# ================================================================ selection methods
# Every method returns a list of passages: {"text", "members": [pool indices], "source"}.
def _passages(pool, order):
    return [{"text": pool[i]["content"], "members": [i], "source": pool[i]["source"]} for i in order]


def _unit_sim(vecs):
    n = np.linalg.norm(vecs, axis=1, keepdims=True)
    u = vecs / np.where(n == 0, 1, n)
    return u @ u.T


def order_relevance(pool, vecs, n):
    return list(range(min(n, len(pool))))  # pools are already sorted by relevance


def order_mmr(pool, vecs, n, lam=0.7, top_n=12, normalise=False):
    m = min(top_n, len(pool))
    if m == 0:
        return []
    rel = np.array([p["relevance"] for p in pool[:m]], dtype=float)
    sim = _unit_sim(vecs[:m])
    if normalise:
        span = rel.max() - rel.min()
        rel = (rel - rel.min()) / span if span > 0 else np.ones_like(rel)
        off = sim[~np.eye(m, dtype=bool)] if m > 1 else np.array([0.0])
        lo, hi = off.min(), off.max()
        sim = (sim - lo) / (hi - lo) if hi > lo else np.zeros_like(sim)
    chosen = [0]
    while len(chosen) < min(n, m):
        rest = [i for i in range(m) if i not in chosen]
        chosen.append(max(rest, key=lambda i: lam * rel[i] - (1 - lam) * max(sim[i][j] for j in chosen)))
    return chosen


def order_dpp(pool, vecs, n, theta=4.0, top_n=12):
    """Greedy MAP inference for a DPP with kernel L = diag(q) S diag(q), q = exp(theta * normalised relevance).
    Larger theta = relevance matters more; similar passages shrink the determinant, so they're avoided."""
    m = min(top_n, len(pool))
    if m == 0:
        return []
    rel = np.array([p["relevance"] for p in pool[:m]], dtype=float)
    span = rel.max() - rel.min()
    rel = (rel - rel.min()) / span if span > 0 else np.ones_like(rel)
    q = np.exp(theta * rel)
    S = np.clip(_unit_sim(vecs[:m]), 0, 1)
    L = q[:, None] * S * q[None, :] + 1e-6 * np.eye(m)
    chosen = []
    while len(chosen) < min(n, m):
        best, best_val = None, -np.inf
        for i in range(m):
            if i in chosen:
                continue
            idx = chosen + [i]
            sign, logdet = np.linalg.slogdet(L[np.ix_(idx, idx)])
            val = logdet if sign > 0 else -np.inf
            if val > best_val:
                best, best_val = i, val
        if best is None:
            break
        chosen.append(best)
    return chosen


def merge_select(pool, vecs, order_fn, k=6, gate=0.1, budget=1500, merge_cap=2, expand=False, **order_kw):
    """D/E/G: gate weak candidates, walk them in `order_fn` order, merge document neighbours into the passage
    they touch (no slot used), de-duplicate sentences, stop at k passages or the token budget.
    expand: afterwards, a single-chunk passage also takes its next (else previous) chunk from the pool even if
    the gate dropped it: the cross-encoder under-scores continuation chunks (formulas, lists, tables)."""
    if not pool:
        return []
    best = pool[0]["relevance"]
    eligible = [i for i, p in enumerate(pool) if p["relevance"] >= gate * best]
    sub = [pool[i] for i in eligible]
    order = [eligible[i] for i in order_fn(sub, vecs[eligible], len(sub), **order_kw)] if sub else []
    order += [i for i in eligible if i not in order]  # methods with a top_n cut-off: append the rest
    passages = []
    for i in order:
        c = pool[i]
        target = None
        for p in passages:
            if len(p["members"]) >= merge_cap:
                continue
            idxs = [pool[j]["chunk_index"] for j in p["members"]]
            if pool[p["members"][0]]["doc_id"] == c["doc_id"] and c["chunk_index"] is not None and \
                    (c["chunk_index"] == min(idxs) - 1 or c["chunk_index"] == max(idxs) + 1):
                target = p
                break
        if target is not None:  # neighbour of a chosen passage: extend it in reading order
            members = sorted(target["members"] + [i], key=lambda j: pool[j]["chunk_index"])
            text = pool[members[0]]["content"]
            for j in members[1:]:
                text = join_overlapping(text, pool[j]["content"])
            target.update(members=members, text=text)
        elif len(passages) < k:
            passages.append({"text": c["content"], "members": [i], "source": c["source"]})
    if expand:
        used_idx = {j for p in passages for j in p["members"]}
        where = {(c["doc_id"], c["chunk_index"]): j for j, c in enumerate(pool)}
        for p in passages:
            if len(p["members"]) >= merge_cap:
                continue
            first = pool[p["members"][0]]
            for step in (1, -1):
                j = where.get((first["doc_id"], (first["chunk_index"] or 0) + step))
                if j is not None and j not in used_idx:
                    members = sorted(p["members"] + [j], key=lambda m: pool[m]["chunk_index"])
                    text = pool[members[0]]["content"]
                    for m in members[1:]:
                        text = join_overlapping(text, pool[m]["content"])
                    p.update(members=members, text=text)
                    used_idx.add(j)
                    break
    dedup_sentences(passages)
    out, used = [], 0
    for p in passages:  # passages are in selection order: keep them while they fit the budget
        t = tokens(p["text"])
        if out and used + t > budget:
            break
        out.append(p)
        used += t
    return out


def build_methods():
    """Every (method, params) setting to evaluate."""
    ms = []
    for k in (2, 3, 4, 6):
        ms.append(("A", {"k": k}))
        for lam in (0.5, 0.7, 0.85):
            for top_n in (8, 12):
                ms.append(("B", {"k": k, "lam": lam, "top_n": top_n}))
                ms.append(("C", {"k": k, "lam": lam, "top_n": top_n}))
        for theta in (2.0, 4.0, 8.0):
            ms.append(("F", {"k": k, "theta": theta}))
    for k in (2, 3, 4, 6):
        for gate in (0.0, 0.05, 0.2):
            for budget in (800, 1500):
                for expand in (False, True):
                    ms.append(("D", {"k": k, "gate": gate, "budget": budget, "expand": expand}))
                    ms.append(("E", {"k": k, "gate": gate, "budget": budget, "expand": expand, "lam": 0.7}))
                    ms.append(("G", {"k": k, "gate": gate, "budget": budget, "expand": expand, "theta": 4.0}))
    return ms


def run_method(name, params, pool, vecs):
    p = dict(params)
    k = p.pop("k")
    if name == "A":
        return _passages(pool, order_relevance(pool, vecs, k))
    if name in ("B", "C"):
        return _passages(pool, order_mmr(pool, vecs, k, normalise=(name == "C"), **p))
    if name == "F":
        return _passages(pool, order_dpp(pool, vecs, k, **p))
    gate, budget, expand = p.pop("gate"), p.pop("budget"), p.pop("expand", False)
    common = {"k": k, "gate": gate, "budget": budget, "expand": expand}
    if name == "D":
        return merge_select(pool, vecs, order_relevance, **common)
    if name == "E":
        return merge_select(pool, vecs, order_mmr, normalise=True, top_n=20, **common, **p)
    if name == "G":
        return merge_select(pool, vecs, order_dpp, top_n=20, **common, **p)
    raise ValueError(name)


def label(name, params):
    return name + "(" + ", ".join(f"{k}={v}" for k, v in params.items()) + ")"


# ================================================================ scoring
def aspects_of(q):
    return q.get("aspects") or ([q["gold"]] if q["gold"] else [])


def _has(text, phrases):
    t = _norm(text)
    return any(_norm(ph) in t for ph in phrases)


def score(q, passages, pool, vecs, alpha=0.5):
    aspects = aspects_of(q)
    gold_text = [p for p in passages if p["source"] in q["sources"]]
    if aspects:
        covered = [any(_has(p["text"], a) for p in gold_text) for a in aspects]
        coverage = sum(covered) / len(aspects)
        relevant = [p["source"] in q["sources"] and any(_has(p["text"], a) for a in aspects) for p in passages]
    else:  # summary questions without phrases: any chunk of the right document counts
        coverage = 1.0 if gold_text else 0.0
        relevant = [p["source"] in q["sources"] for p in passages]
    precision = sum(relevant) / len(passages) if passages else 0.0
    f1 = 2 * coverage * precision / (coverage + precision) if coverage + precision else 0.0

    # alpha-nDCG over aspects (ideal computed greedily from the whole pool)
    def a_dcg(texts):
        seen = defaultdict(int)
        total = 0.0
        for rank, (src, text) in enumerate(texts, 1):
            gain = 0.0
            for ai, a in enumerate(aspects or [[]]):
                hit = src in q["sources"] and (_has(text, a) if a else True)
                if hit:
                    gain += (1 - alpha) ** seen[ai]
                    seen[ai] += 1
            total += gain / math.log2(rank + 1)
        return total
    # Scored per chunk (a merged passage counts as its chunks in order), so merging can't beat the ideal.
    sel = [(pool[i]["source"], pool[i]["content"]) for p in passages for i in p["members"]]
    remaining = [(c["source"], c["content"]) for c in pool]
    ideal = []
    for _ in range(len(sel)):
        if not remaining:
            break
        best = max(remaining, key=lambda x: a_dcg(ideal + [x]))
        ideal.append(best)
        remaining.remove(best)
    ideal_v = a_dcg(ideal)
    andcg = a_dcg(sel) / ideal_v if ideal_v else 0.0

    members = [p["members"][0] for p in passages]
    if len(members) > 1:
        S = _unit_sim(vecs[members])
        diversity = 1 - float(S[~np.eye(len(members), dtype=bool)].mean())
    else:
        diversity = 0.0
    return {"coverage": coverage, "precision": precision, "f1": f1, "andcg": andcg, "diversity": diversity,
            "tokens": sum(tokens(p["text"]) for p in passages), "passages": len(passages),
            "chunks": sum(len(p["members"]) for p in passages)}


def split(qid: str) -> str:
    """Deterministic ~2/3 train, 1/3 held-out split."""
    return "heldout" if int(hashlib.md5(qid.encode()).hexdigest(), 16) % 3 == 0 else "train"


def mean_scores(rows):
    keys = ("coverage", "precision", "f1", "andcg", "diversity", "tokens", "passages", "chunks")
    return {k: round(statistics.mean(r[k] for r in rows), 3) for k in keys} if rows else {}


def rank_key(s):
    return (s["f1"], s["coverage"] + s["precision"], -s["tokens"])


def compare() -> None:
    data = json.loads(POOL_JSON.read_text(encoding="utf-8"))["pools"]
    npz = np.load(POOL_NPZ)
    qs = [q for q in load_questions() if q["shape"] != "out_of_scope" and q["id"] in data]
    for q in qs:  # balance the split inside every shape so each shape has held-out questions
        q["split"] = split(q["id"])
    by_shape = defaultdict(list)
    for q in qs:
        by_shape[q["shape"]].append(q)
    for shape, group in by_shape.items():
        if not any(q["split"] == "heldout" for q in group):
            group[-1]["split"] = "heldout"
        if not any(q["split"] == "train" for q in group):
            group[0]["split"] = "train"

    methods = build_methods()
    print(f"{len(qs)} questions x {len(methods)} settings ...")
    per = {}  # (label) -> {qid: scores}
    for name, params in methods:
        lab = label(name, params)
        per[lab] = {}
        for q in qs:
            pool, vecs = data[q["id"]], npz[q["id"].replace("-", "_")]
            per[lab][q["id"]] = score(q, run_method(name, params, pool, vecs), pool, vecs)

    def table(subset):
        return {lab: mean_scores([per[lab][q["id"]] for q in subset]) for lab in per}

    train = [q for q in qs if q["split"] == "train"]
    held = [q for q in qs if q["split"] == "heldout"]
    overall_train = table(train)
    best_global = max(overall_train, key=lambda lab: rank_key(overall_train[lab]))
    best_per_method = {}
    for lab, s in overall_train.items():
        m = lab[0]
        if m not in best_per_method or rank_key(s) > rank_key(overall_train[best_per_method[m]]):
            best_per_method[m] = lab

    shape_choice = {}
    for shape, group in by_shape.items():
        t = table([q for q in group if q["split"] == "train"])
        shape_choice[shape] = max(t, key=lambda lab: rank_key(t[lab]))

    def routed_scores(subset):
        return mean_scores([per[shape_choice[q["shape"]]][q["id"]] for q in subset])

    held_global = mean_scores([per[best_global][q["id"]] for q in held])
    held_routed = routed_scores(held)
    baseline = label("B", {"k": 6, "lam": 0.7, "top_n": 12})  # what the app does today
    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "questions": {"train": len(train), "heldout": len(held)},
        "baseline_today": {"label": baseline, "train": overall_train[baseline],
                           "heldout": mean_scores([per[baseline][q["id"]] for q in held])},
        "best_per_method_train": {m: {"label": lab, "train": overall_train[lab],
                                      "heldout": mean_scores([per[lab][q["id"]] for q in held])}
                                  for m, lab in sorted(best_per_method.items())},
        "best_single": {"label": best_global, "train": overall_train[best_global], "heldout": held_global},
        "per_shape_choice": shape_choice,
        "per_shape_routed": {"train": routed_scores(train), "heldout": held_routed},
        "per_shape_detail": {},
        "per_question": {q["id"]: {"shape": q["shape"], "split": q["split"],
                                   "routed": per[shape_choice[q["shape"]]][q["id"]],
                                   "single": per[best_global][q["id"]], "baseline": per[baseline][q["id"]]}
                         for q in qs},
    }
    for shape, group in sorted(by_shape.items()):
        h = [q for q in group if q["split"] == "heldout"]
        result["per_shape_detail"][shape] = {
            "n_train": len(group) - len(h), "n_heldout": len(h), "choice": shape_choice[shape],
            "heldout_routed": mean_scores([per[shape_choice[shape]][q["id"]] for q in h]),
            "heldout_single": mean_scores([per[best_global][q["id"]] for q in h]),
            "heldout_baseline": mean_scores([per[baseline][q["id"]] for q in h]),
            "all_baseline": mean_scores([per[baseline][q["id"]] for q in group]),
            "all_routed": mean_scores([per[shape_choice[shape]][q["id"]] for q in group]),
        }
    out = RESULTS / "selection_compare.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    def row(name, s):
        return (f"  {name:<44} F1 {s['f1']:.3f}  cov {s['coverage']:.3f}  prec {s['precision']:.3f}  "
                f"aNDCG {s['andcg']:.3f}  div {s['diversity']:.3f}  tok {s['tokens']:>6.0f}  passages {s['passages']:.1f}")
    print(f"\ntrain {len(train)} / held-out {len(held)} questions")
    print("\nBest setting of each method (chosen on train), scores on TRAIN | HELD-OUT")
    print(row("today: " + baseline, result["baseline_today"]["train"]))
    print(row("", result["baseline_today"]["heldout"]))
    for m, v in result["best_per_method_train"].items():
        print(row(v["label"], v["train"]))
        print(row("", v["heldout"]))
    print("\nPer-shape choice (chosen on train):")
    for shape, d in result["per_shape_detail"].items():
        print(f"  {shape:<13} -> {d['choice']}")
    print("\nHELD-OUT comparison")
    print(row("today's MMR", result["baseline_today"]["heldout"]))
    print(row("best single method", held_global))
    print(row("per-shape routing", held_routed))
    print(f"\nsaved to {out.relative_to(BENCH_DIR.parent)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--cached-only", action="store_true", help="collect without spending embedding quota")
    args = ap.parse_args()
    if args.collect:
        collect(args.cached_only)
    if args.compare:
        compare()


if __name__ == "__main__":
    main()
