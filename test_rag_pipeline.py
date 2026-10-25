"""End-to-end smoke test. Start the API first:  uvicorn app:app --port 8000

Checks the source hierarchy (documents → web → model knowledge), the user-facing notices,
and the large-model review loop.
"""
import os
import sys
import uuid

import requests

BASE_URL = os.environ.get("API_URL", "http://localhost:8000/api")
PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else "./sample.pdf"

# Asked BEFORE any document is uploaded: expect a web answer with the "no documents" notice.
QUERIES_NO_DOCS = [
    ("no docs -> web", "What is retrieval-augmented generation?"),
]

# Asked AFTER uploading the PDF.
QUERIES_WITH_DOCS = [
    ("greeting", "Hey there! What can you do?"),
    ("docs answer it", "Can you summarize the key points from the uploaded document?"),
    ("vague, docs answer it", "main thing?"),
    ("not in docs -> web", "What is the capital of Australia?"),
    ("explicit model knowledge", "Without searching, from your own knowledge: what is the difference between a process and a thread?"),
]


def ask(headers, conv_id, label, q):
    r = requests.post(f"{BASE_URL}/chat", headers=headers, json={"message": q, "conversation_id": conv_id},
                      timeout=600)
    assert r.status_code == 200, r.text
    d = r.json()
    reviews = [s for s in d["trace"] if s["node"] == "review"]
    print(f"\n[{label}] {q}")
    print(f"  tool={d['tool']}  latency={d['latency_ms'] / 1000:.1f}s  reviews={len(reviews)}")
    for n in d.get("notices", []):
        print(f"  NOTICE: {n}")
    for step in d["trace"]:
        print(f"   · {step['node']}: {step['detail'][:140]}")
    print("  answer:", d["answer"][:220].replace("\n", " "))
    return d["conversation_id"]


def test_pipeline():
    creds = {"username": f"tester_{uuid.uuid4().hex[:6]}", "password": uuid.uuid4().hex}
    r = requests.post(f"{BASE_URL}/auth/register", json=creds)
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    print("Registered", creds["username"])

    print("\n--- 1. Before uploading anything ---")
    conv = None
    for label, q in QUERIES_NO_DOCS:
        conv = ask(headers, conv, label, q)

    print("\n--- 2. Document ingestion ---")
    if not os.path.exists(PDF_PATH):
        print(f"Error: Could not find test file at {PDF_PATH}")
        return
    with open(PDF_PATH, "rb") as f:
        r = requests.post(f"{BASE_URL}/documents/upload", headers=headers,
                          files={"file": (os.path.basename(PDF_PATH), f, "application/pdf")})
    print("Upload:", r.status_code, {k: v for k, v in r.json().items() if k != "document"})
    assert r.status_code == 200, "Document upload failed!"

    print("\n--- 3. With documents ---")
    conv = None
    for label, q in QUERIES_WITH_DOCS:
        conv = ask(headers, conv, label, q)


if __name__ == "__main__":
    test_pipeline()
