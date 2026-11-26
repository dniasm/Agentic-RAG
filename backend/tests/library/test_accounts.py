"""Accounts: sign-up rules, logins, and every way one user might reach another's library."""
import pytest
from fastapi.testclient import TestClient

from backend.library import agent, auth, batch, catalog, config, library
from services.document import retrieval
from test_agent import Scripted

INVITE = "let-me-in-42"


@pytest.fixture
def invite(monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_CODE", INVITE)


def browser():
    from backend.app import app
    return TestClient(app)


def sign_up(client, name, invite_code=""):
    return client.post("/api/auth/signup", json={"username": name, "password": "correct horse", "invite": invite_code})


@pytest.fixture
def two_users(library_docs, signed_in, invite, tmp_path, user_db):
    """tester (the first account: owns the four DOCS) and mallory, with one document of her own."""
    other = browser()
    assert sign_up(other, "mallory", INVITE).status_code == 200
    mallory = dict(user_db.connection().execute("SELECT * FROM users WHERE username = ?", ("mallory",)).fetchone())
    path = tmp_path / "secret.txt"
    path.write_text("Mallory's secret plan\nThe plan is to rename everything about kernels.", encoding="utf-8")
    doc, _ = library.ingest_path(path)          # made as the legacy owner, then given to her: row and chunks both
    library._set_chunk_fields(doc["id"], config.OWNER, user_id=mallory["id"])
    with catalog.tx() as c:
        c.execute("UPDATE documents SET owner = ? WHERE id = ?", (mallory["id"], doc["id"]))
    return signed_in, other, dict(user_db.connection().execute("SELECT * FROM users WHERE id = ?", (signed_in.user["id"],)).fetchone()), mallory


# ---------------------------------------------------------------- signing up and in
def test_the_first_account_takes_the_existing_library(library_docs, signed_in):
    user = signed_in.user
    docs = catalog.list_documents()
    assert len(docs) == 4 and all(d["owner"] == user["id"] for d in docs)
    assert all(d["file_name"].startswith(user["id"] + "/") for d in docs)                 # moved into their folder
    assert all((config.LIBRARY_DIR / d["file_name"]).exists() for d in docs)
    hits = retrieval.retrieve(["maximum margin hyperplane"], user["id"], k=3, min_score=0.0)
    assert hits and hits[0]["doc_id"] == library_docs["svm.txt"]["id"]                 # their chunks are theirs now
    assert len(signed_in.get("/api/library/documents").json()) == 4


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


def test_passwords_and_logins_are_never_stored_as_given(signed_in, user_db):
    user = dict(user_db.connection().execute("SELECT * FROM users WHERE id = ?", (signed_in.user["id"],)).fetchone())
    assert "correct horse" not in user["password_hash"] and user["password_hash"].startswith("pbkdf2_sha256$")
    token = signed_in.cookies.get(auth.COOKIE)
    with user_db.tx() as c:
        stored = [r[0] for r in c.execute("SELECT token_hash FROM auth_tokens")]
    assert token not in stored and len(stored) == 1


def test_log_in_log_out_and_wrong_passwords(signed_in):
    c = browser()
    assert c.get("/api/library/documents").status_code == 401
    assert c.post("/api/auth/login", json={"username": "tester", "password": "nope nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "Tester", "password": "correct horse"}).status_code == 200
    assert c.get("/api/library/documents").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/library/documents").status_code == 401


def test_repeated_wrong_passwords_are_throttled(signed_in):
    c = browser()
    for _ in range(auth.MAX_FAILURES):
        c.post("/api/auth/login", json={"username": "tester", "password": "guess guess"})
    r = c.post("/api/auth/login", json={"username": "tester", "password": "correct horse"})
    assert r.status_code == 429                                           # even the right password waits now


def test_every_page_api_needs_a_login():
    c = browser()
    for path in ("/api/library/documents", "/api/library/queue", "/api/library/limits", "/api/library/documents/x/file"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/library/documents/x/rename", json={"title": "y"}).status_code == 401


# ---------------------------------------------------------------- one user can't reach another's library
def test_the_api_shows_and_serves_only_your_own_documents(two_users):
    tester, mallory_browser, _, mallory = two_users
    theirs = catalog.list_documents(owner=mallory["id"])[0]
    assert theirs["id"] not in {d["id"] for d in tester.get("/api/library/documents").json()}
    for path in (f"/api/library/documents/{theirs['id']}/file", f"/api/library/documents/{theirs['id']}/pdf"):
        assert tester.get(path).status_code == 404
    assert tester.delete(f"/api/library/documents/{theirs['id']}").status_code == 404
    assert catalog.get_document(theirs["id"]) is not None
    assert [d["title"] for d in mallory_browser.get("/api/library/documents").json()] == [theirs["title"]]


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


def test_queues_are_private(two_users):
    tester_browser, mallory_browser, tester, mallory = two_users
    mine = catalog.add_batch("s", ["a"], owner=tester["id"])
    assert mallory_browser.get("/api/library/queue").json()["batches"] == []
    assert mallory_browser.delete(f"/api/library/batches/{mine['id']}").status_code == 404
    assert [b["id"] for b in tester_browser.get("/api/library/queue").json()["batches"]] == [mine["id"]]
    assert mallory_browser.get("/api/library/limits").json()["queued"] == 0
    catalog.cancel_batch(mine["id"])


def test_the_same_file_uploaded_by_two_users_is_two_documents(two_users):
    tester_browser, mallory_browser, tester, mallory = two_users
    svm = catalog.list_documents(owner=tester["id"])[-1]
    data = (config.LIBRARY_DIR / svm["file_name"]).read_bytes()
    r = mallory_browser.post("/api/library/documents/upload", files={"files": ("svm.txt", data)}).json()[0]
    assert r["status"] == "queued" and r["document"]["id"] != svm["id"]   # not "already in the library": not hers


# ---------------------------------------------------------------- one login for the whole app (stage 4)
def test_a_logged_out_token_is_dead_even_if_kept(signed_in):
    c = browser()
    assert c.post("/api/auth/login", json={"username": "tester", "password": "correct horse"}).status_code == 200
    token = c.cookies.get(auth.COOKIE)
    c.post("/api/auth/logout")
    stolen = browser()
    stolen.cookies.set(auth.COOKIE, token)
    assert stolen.get("/api/auth/me").status_code == 401
    assert stolen.get("/api/conversations").status_code == 401


def test_an_expired_token_is_refused(signed_in, user_db):
    with user_db.tx() as c:
        c.execute("UPDATE auth_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert signed_in.get("/api/auth/me").status_code == 401
    assert signed_in.get("/api/documents").status_code == 401


def test_the_cookie_signs_in_day7s_routes_too(signed_in):
    assert signed_in.get("/api/auth/me").json()["username"] == "tester"
    assert signed_in.get("/api/conversations").status_code == 200
    assert signed_in.get("/api/documents").status_code == 200
    assert browser().get("/api/conversations").status_code == 401


def test_one_user_cannot_read_anothers_chats_or_documents(two_users):
    tester, mallory_browser, _, mallory = two_users
    from backend.models import database as db
    theirs = db.create_conversation(mallory["id"], "private")
    db.add_message(theirs["id"], "user", "my secret")
    assert tester.get(f"/api/conversations/{theirs['id']}/messages").status_code == 404
    assert all(c["id"] != theirs["id"] for c in tester.get("/api/conversations").json())
    assert mallory_browser.get(f"/api/conversations/{theirs['id']}/messages").status_code == 200
    their_doc = catalog.list_documents(owner=mallory["id"])[0]
    assert all(d["id"] != their_doc["id"] for d in tester.get("/api/documents").json())
    assert tester.delete(f"/api/documents/{their_doc['id']}").status_code == 404
    assert tester.get(f"/api/library/documents/{their_doc['id']}/preview").status_code == 404


def test_wrong_passwords_lock_a_name_only_for_the_guessing_address(signed_in):
    guesser, owner = browser(), TestClient(app_for_tests(), client=("10.0.0.2", 50000))
    for _ in range(auth.MAX_FAILURES):
        guesser.post("/api/auth/login", json={"username": "tester", "password": "guess guess"})
    assert guesser.post("/api/auth/login", json={"username": "tester", "password": "correct horse"}).status_code == 429
    assert owner.post("/api/auth/login", json={"username": "tester", "password": "correct horse"}).status_code == 200


def test_an_address_guessing_many_names_is_stopped(signed_in):
    guesser = browser()
    for i in range(auth.MAX_ADDRESS_FAILURES):
        guesser.post("/api/auth/login", json={"username": f"name{i}", "password": "guess guess"})
    assert guesser.post("/api/auth/login", json={"username": "tester", "password": "correct horse"}).status_code == 429


def app_for_tests():
    from backend.app import app
    return app


def test_wrong_invite_codes_are_limited_per_address(signed_in, invite):
    guesser = browser()
    for i in range(auth.MAX_ADDRESS_FAILURES):
        assert sign_up(guesser, f"guess{i}", "wrong-code").status_code == 403
    assert sign_up(guesser, "late_comer", INVITE).status_code == 429
