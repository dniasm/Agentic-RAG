"""Accounts: sign-up rules, logins, and every way one user might reach another's library."""
import pytest
from fastapi.testclient import TestClient

from renamer import agent, auth, batch, catalog, config, library
from renamer.day7 import retrieval
from tests.test_agent import Scripted

INVITE = "let-me-in-42"


@pytest.fixture
def invite(monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_CODE", INVITE)


def browser():
    from renamer import api
    return TestClient(api.app)


def sign_up(client, name, invite_code=""):
    return client.post("/api/auth/signup", json={"username": name, "password": "correct horse", "invite": invite_code})


@pytest.fixture
def two_users(library_docs, signed_in, invite, tmp_path):
    """tester (the first account: owns the four DOCS) and mallory, with one document of her own."""
    other = browser()
    assert sign_up(other, "mallory", INVITE).status_code == 200
    mallory = catalog.user_by_name("mallory")
    path = tmp_path / "secret.txt"
    path.write_text("Mallory's secret plan\nThe plan is to rename everything about kernels.", encoding="utf-8")
    doc, _ = library.ingest_path(path)          # made as the legacy owner, then given to her: row and chunks both
    library._set_chunk_fields(doc["id"], config.OWNER, user_id=mallory["id"])
    with catalog.tx() as c:
        c.execute("UPDATE documents SET owner = ? WHERE id = ?", (mallory["id"], doc["id"]))
    return signed_in, other, catalog.get_user(signed_in.user["id"]), mallory


# ---------------------------------------------------------------- signing up and in
def test_the_first_account_takes_the_existing_library(library_docs, signed_in):
    user = signed_in.user
    docs = catalog.list_documents()
    assert len(docs) == 4 and all(d["owner"] == user["id"] for d in docs)
    assert all(d["file_name"].startswith(user["id"] + "/") for d in docs)                 # moved into their folder
    assert all((config.LIBRARY_DIR / d["file_name"]).exists() for d in docs)
    hits = retrieval.retrieve(["maximum margin hyperplane"], user["id"], k=3, min_score=0.0)
    assert hits and hits[0]["doc_id"] == library_docs["svm.txt"]["id"]                 # their chunks are theirs now
    assert len(signed_in.get("/api/documents").json()) == 4


def test_later_sign_ups_need_the_invite_code(library_docs, signed_in, invite):
    assert sign_up(browser(), "nobody").status_code == 403
    assert sign_up(browser(), "nobody", "wrong").status_code == 403
    assert sign_up(browser(), "alice", INVITE).status_code == 200
    assert sign_up(browser(), "ALICE", INVITE).status_code == 409                     # names are case-insensitive


def test_without_a_code_sign_ups_close_after_the_first(signed_in, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_CODE", "")
    monkeypatch.delenv("RENAMER_SIGNUP_CODE", raising=False)
    assert browser().get("/api/auth/state").json()["signups"] == "closed"
    assert sign_up(browser(), "late", "anything").status_code == 403


def test_bad_usernames_and_short_passwords_are_refused():
    c = browser()
    assert c.post("/api/auth/signup", json={"username": "a", "password": "correct horse"}).status_code == 400
    assert c.post("/api/auth/signup", json={"username": "ok_name", "password": "short"}).status_code == 400


def test_passwords_and_logins_are_never_stored_as_given(signed_in):
    user = catalog.get_user(signed_in.user["id"])
    assert "correct horse" not in user["password_hash"] and user["password_hash"].startswith("pbkdf2_sha256$")
    token = signed_in.cookies.get(auth.COOKIE)
    with catalog.tx() as c:
        stored = [r[0] for r in c.execute("SELECT token_hash FROM auth_tokens")]
    assert token not in stored and len(stored) == 1


def test_log_in_log_out_and_wrong_passwords(signed_in):
    c = browser()
    assert c.get("/api/documents").status_code == 401
    assert c.post("/api/auth/login", json={"username": "tester", "password": "nope nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "Tester", "password": "correct horse"}).status_code == 200
    assert c.get("/api/documents").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/documents").status_code == 401


def test_repeated_wrong_passwords_are_throttled(signed_in):
    c = browser()
    for _ in range(auth.MAX_FAILURES):
        c.post("/api/auth/login", json={"username": "tester", "password": "guess guess"})
    r = c.post("/api/auth/login", json={"username": "tester", "password": "correct horse"})
    assert r.status_code == 429                                           # even the right password waits now


def test_every_page_api_needs_a_login():
    c = browser()
    for path in ("/api/documents", "/api/queue", "/api/limits", "/api/sessions/abcdef", "/api/documents/x/file"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/chat", json={"session_id": "abcdef", "message": "hi"}).status_code == 401


# ---------------------------------------------------------------- one user can't reach another's library
def test_the_api_shows_and_serves_only_your_own_documents(two_users):
    tester, mallory_browser, _, mallory = two_users
    theirs = catalog.list_documents(owner=mallory["id"])[0]
    assert theirs["id"] not in {d["id"] for d in tester.get("/api/documents").json()}
    for path in (f"/api/documents/{theirs['id']}/file", f"/api/documents/{theirs['id']}/pdf"):
        assert tester.get(path).status_code == 404
    assert tester.delete(f"/api/documents/{theirs['id']}").status_code == 404
    assert catalog.get_document(theirs["id"]) is not None
    assert [d["title"] for d in mallory_browser.get("/api/documents").json()] == [theirs["title"]]


def test_the_agent_cannot_find_or_change_another_users_document(two_users):
    _, _, tester, mallory = two_users
    theirs = catalog.list_documents(owner=mallory["id"])[0]
    found = library.find("Mallory's secret plan", tester["id"])
    assert theirs["id"] not in [c["doc_id"] for c in found["candidates"]]
    guard = agent.Guard({}, 1)
    guard.owner = tester["id"]
    guard.resolved[theirs["id"]] = {"turn": 1, "by": "name"}             # even if the model claims it found it
    for name, args in (("document_details", {"doc_id": theirs["id"]}),
                       ("rename_document", {"doc_id": theirs["id"], "new_title": "Stolen"}),
                       ("edit_document", {"doc_id": theirs["id"], "operation": "append", "text": "x"}),
                       ("undo_edit", {"doc_id": theirs["id"]})):
        result = agent.run_tool(name, args, guard, [])
        assert "No document" in str(result), name
    assert catalog.get_document(theirs["id"])["title"] == theirs["title"]
    listed = agent.run_tool("list_documents", {}, guard, [])
    assert theirs["id"] not in [d["doc_id"] for d in listed["documents"]]


def test_a_queued_change_cannot_reach_another_users_document(two_users):
    _, _, tester, mallory = two_users
    theirs = catalog.list_documents(owner=mallory["id"])[0]
    change = {"action": "rename", "document": theirs["title"], "new_title": "Stolen", "doc_id": theirs["id"]}
    catalog.add_batch("s", [("Rename", change)], owner=tester["id"])
    batch.run_pending(model=Scripted())
    assert catalog.get_document(theirs["id"])["title"] == theirs["title"]


def test_chats_and_queues_are_private(two_users):
    tester_browser, mallory_browser, tester, mallory = two_users
    agent.chat("tester-chat", "hello", model=Scripted(), owner=tester["id"])
    assert mallory_browser.get("/api/sessions/tester-chat").status_code == 404
    assert mallory_browser.post("/api/chat", json={"session_id": "tester-chat", "message": "hi"}).status_code == 404
    mine = catalog.add_batch("s", ["a"], owner=tester["id"])
    assert mallory_browser.get("/api/queue").json()["batches"] == []
    assert mallory_browser.delete(f"/api/batches/{mine['id']}").status_code == 404
    assert [b["id"] for b in tester_browser.get("/api/queue").json()["batches"]] == [mine["id"]]
    assert mallory_browser.get("/api/limits").json()["queued"] == 0
    catalog.cancel_batch(mine["id"])


def test_the_same_file_uploaded_by_two_users_is_two_documents(two_users):
    tester_browser, mallory_browser, tester, mallory = two_users
    svm = catalog.list_documents(owner=tester["id"])[-1]
    data = (config.LIBRARY_DIR / svm["file_name"]).read_bytes()
    r = mallory_browser.post("/api/documents/upload", files={"files": ("svm.txt", data)}).json()[0]
    assert r["status"] == "queued" and r["document"]["id"] != svm["id"]   # not "already in the library": not hers
