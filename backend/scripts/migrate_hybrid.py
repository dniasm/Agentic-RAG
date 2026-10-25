"""Copy the dense-only collection into the hybrid (dense + BM25) collection.

The existing dense vectors are copied as-is (no new embedding requests, so no Gemini quota is used);
only the BM25 keyword vectors are computed, locally, from each chunk's stored text.
The old collection is left untouched, so this is safe to re-run and easy to roll back
(set COLLECTION_NAME back to the old name).

    python -m backend.scripts.migrate_hybrid [--source agentic_rag_docs]
Run with the backend STOPPED (single-process embedded Qdrant).
"""
import argparse

from qdrant_client import models

from backend.config.settings import settings
from backend.rag import sparse, vectorstore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="agentic_rag_docs", help="old dense-only collection")
    args = ap.parse_args()

    client = vectorstore.get_client()  # also creates the hybrid collection if needed
    target = settings.collection_name
    if not client.collection_exists(args.source):
        print(f"Nothing to migrate: collection '{args.source}' does not exist.")
        return

    copied, offset = 0, None
    while True:
        points, offset = client.scroll(args.source, limit=128, offset=offset, with_payload=True, with_vectors=True)
        batch = []
        for p in points:
            dense = p.vector if not isinstance(p.vector, dict) else p.vector.get(vectorstore.DENSE)
            text = (p.payload or {}).get("page_content", "")
            batch.append(models.PointStruct(
                id=p.id, payload=p.payload,
                vector={vectorstore.DENSE: dense, vectorstore.SPARSE: sparse.document_vector(text)},
            ))
        if batch:
            vectorstore.upsert(batch)
            copied += len(batch)
        if offset is None:
            break
    total = client.count(target, exact=True).count
    print(f"Copied {copied} chunks from '{args.source}' into '{target}' (now {total} chunks).")


if __name__ == "__main__":
    main()
