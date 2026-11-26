"""The User service through its real gRPC server (in this process, its own database in a temporary folder), the
orchestrator's failing closed when it is unreachable, and scripts/migrate_users.py."""
import base64
import hashlib
import importlib.util
import logging
import sqlite3
import sys
import threading
from pathlib import Path

import bcrypt
import grpc
import pytest

from agentic_rag_proto import user_pb2 as pb
from agentic_rag_proto import user_pb2_grpc as pb_grpc
from services.common.inprocess import RunningService
from services.user import accounts
from services.user import db as store
from services.user.server import build_service

ROOT = Path(__file__).resolve().parents[3]
PASSWORD = "correct horse battery"


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_users", ROOT / "scripts" / "migrate_users.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


migrate = _load_migrate()


@pytest.fixture
def running(tmp_path):
    service = RunningService(lambda: build_service(store.open_db(tmp_path / "user.db"))).start()
    yield service
    service.stop()


@pytest.fixture
def stub(running):
    with grpc.insecure_channel(running.address) as channel:
        yield pb_grpc.UserServiceStub(channel)


def sign_up(stub, name="alice", password=PASSWORD):
    return stub.SignUp(pb.SignUpRequest(username=name, password=password), timeout=20)


def code_of(call, request):
    with pytest.raises(grpc.RpcError) as e:
        call(request, timeout=20)
    return e.value.code()


def who(stub, token):
    return stub.ValidateToken(pb.ValidateTokenRequest(token=token), timeout=5)


def test_sign_up_logs_in_and_the_token_validates(stub):
    first = sign_up(stub)
    assert first.first_account and first.user.username == "alice" and first.max_age_seconds == 30 * 86400
    seen = who(stub, first.token)
    assert seen.found and seen.user.id == first.user.id
    second = sign_up(stub, "bob")
    assert not second.first_account and second.user.id != first.user.id
    assert stub.CountUsers(pb.CountUsersRequest(), timeout=5).count == 2


def test_nothing_secret_is_stored_or_returned_as_given(stub, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    reply = sign_up(stub)
    with sqlite3.connect(tmp_path / "user.db") as con:
        (stored,) = con.execute("SELECT password_hash FROM users").fetchone()
        tokens = [r[0] for r in con.execute("SELECT token_hash FROM auth_tokens")]
    assert PASSWORD not in stored and stored.startswith("pbkdf2_sha256$")
    assert reply.token not in tokens and tokens == [hashlib.sha256(reply.token.encode()).hexdigest()]
    assert "password_hash" not in str(reply) and stored not in str(reply)
    assert PASSWORD not in caplog.text and reply.token not in caplog.text   # the request log never has bodies


def test_the_shape_of_a_name_and_password_is_checked_by_the_service(stub):
    assert code_of(stub.SignUp, pb.SignUpRequest(username="a", password=PASSWORD)) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.SignUp, pb.SignUpRequest(username="bad name!", password=PASSWORD)) \
        == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.SignUp, pb.SignUpRequest(username="okname", password="short")) \
        == grpc.StatusCode.INVALID_ARGUMENT
    assert stub.CountUsers(pb.CountUsersRequest(), timeout=5).count == 0


def test_a_username_is_taken_without_regard_to_case(stub):
    sign_up(stub, "Alice")
    assert code_of(stub.SignUp, pb.SignUpRequest(username="alice", password=PASSWORD)) \
        == grpc.StatusCode.ALREADY_EXISTS
    assert code_of(stub.SignUp, pb.SignUpRequest(username="ALICE", password=PASSWORD)) \
        == grpc.StatusCode.ALREADY_EXISTS
    assert stub.LogIn(pb.LogInRequest(username="aLiCe", password=PASSWORD), timeout=20).user.username == "Alice"


def test_log_in_and_the_same_refusal_for_a_wrong_password_and_an_unknown_name(stub):
    sign_up(stub)
    ok = stub.LogIn(pb.LogInRequest(username="alice", password=PASSWORD), timeout=20)
    assert who(stub, ok.token).found
    assert code_of(stub.LogIn, pb.LogInRequest(username="alice", password="wrong password")) \
        == grpc.StatusCode.UNAUTHENTICATED
    assert code_of(stub.LogIn, pb.LogInRequest(username="nobody", password=PASSWORD)) \
        == grpc.StatusCode.UNAUTHENTICATED


def test_log_out_ends_only_that_login(stub):
    sign_up(stub)
    one = stub.LogIn(pb.LogInRequest(username="alice", password=PASSWORD), timeout=20)
    two = stub.LogIn(pb.LogInRequest(username="alice", password=PASSWORD), timeout=20)
    stub.LogOut(pb.LogOutRequest(token=one.token), timeout=5)
    assert not who(stub, one.token).found and who(stub, two.token).found
    stub.LogOut(pb.LogOutRequest(token="never-issued"), timeout=5)   # nothing happens
    stub.LogOut(pb.LogOutRequest(token=""), timeout=5)
    assert not who(stub, "").found and not who(stub, "never-issued").found


def test_an_expired_token_is_refused_and_swept(stub, tmp_path):
    reply = sign_up(stub)
    with sqlite3.connect(tmp_path / "user.db") as con:
        con.execute("UPDATE auth_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert not who(stub, reply.token).found
    stub.LogIn(pb.LogInRequest(username="alice", password=PASSWORD), timeout=20)   # making a login sweeps old ones
    with sqlite3.connect(tmp_path / "user.db") as con:
        assert con.execute("SELECT COUNT(*) FROM auth_tokens").fetchone()[0] == 1


def day7_hash(password: str) -> str:
    return bcrypt.hashpw(base64.b64encode(hashlib.sha256(password.encode()).digest()), bcrypt.gensalt()).decode()


def test_legacy_bcrypt_and_pbkdf2_hashes_both_log_in(stub):
    stub.ImportUsers(pb.ImportUsersRequest(users=[
        pb.ImportedUser(id="old1", username="bcrypt_person", password_hash=day7_hash("correct horse 1"),
                        created_at="2026-01-01T00:00:00+00:00"),
        pb.ImportedUser(id="old2", username="pbkdf2_person", password_hash=accounts.hash_password("correct horse 2"),
                        created_at="2026-01-01T00:00:00+00:00")]), timeout=20)
    for name, good, bad in (("bcrypt_person", "correct horse 1", "correct horse 2"),
                            ("pbkdf2_person", "correct horse 2", "correct horse 1")):
        assert stub.LogIn(pb.LogInRequest(username=name, password=good), timeout=20).user.username == name
        assert code_of(stub.LogIn, pb.LogInRequest(username=name, password=bad)) == grpc.StatusCode.UNAUTHENTICATED
    assert code_of(stub.LogIn, pb.LogInRequest(username="bcrypt_person", password="x" * 90)) \
        == grpc.StatusCode.UNAUTHENTICATED   # a long passphrase doesn't crash bcrypt


def test_list_users_has_no_credentials(stub):
    sign_up(stub, "alice")
    sign_up(stub, "bob")
    users = stub.ListUsers(pb.ListUsersRequest(), timeout=5).users
    assert [u.username for u in users] == ["alice", "bob"] and all(u.id and u.created_at for u in users)


def test_embed_usage_adds_atomically_under_concurrency(stub):
    def add(user, n):
        stub.AddEmbedUsage(pb.AddEmbedUsageRequest(day="2026-11-26", user_id=user, chunks=n), timeout=20)
    threads = [threading.Thread(target=add, args=("u1" if i % 2 else "u2", 3)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    one = stub.GetEmbedUsage(pb.GetEmbedUsageRequest(day="2026-11-26", user_id="u1"), timeout=5).chunks
    two = stub.GetEmbedUsage(pb.GetEmbedUsageRequest(day="2026-11-26", user_id="u2"), timeout=5).chunks
    site = stub.GetEmbedUsage(pb.GetEmbedUsageRequest(day="2026-11-26"), timeout=5).chunks
    assert (one, two, site) == (60, 60, 120)
    assert stub.AddEmbedUsage(pb.AddEmbedUsageRequest(day="2026-11-26", user_id="u1", chunks=5), timeout=5).chunks == 65
    assert stub.AddEmbedUsage(pb.AddEmbedUsageRequest(day="2026-11-26", user_id="u1", chunks=0), timeout=5).chunks == 65
    assert stub.GetEmbedUsage(pb.GetEmbedUsageRequest(day="2026-11-27", user_id="u1"), timeout=5).chunks == 0
    rows = stub.ListEmbedUsage(pb.ListEmbedUsageRequest(day="2026-11-26"), timeout=5).rows
    assert {r.user_id: r.chunks for r in rows} == {"u1": 65, "u2": 60}


def test_api_usage_adds_per_hour_provider_key_and_model(stub):
    def row(hour, key=0, calls=1, tokens=0, limited=0, provider="groq", model="m"):
        return pb.ApiUsageRow(hour=hour, provider=provider, key_index=key, model=model, calls=calls, tokens=tokens,
                              limited=limited)
    stub.AddApiUsage(pb.AddApiUsageRequest(rows=[row("2026-11-26T10", calls=2, tokens=50), row("2026-11-26T11"),
                                                 row("2026-11-26T11", key=1, provider="gemini")]), timeout=5)
    stub.AddApiUsage(pb.AddApiUsageRequest(rows=[row("2026-11-26T10", calls=3, tokens=5, limited=1)]), timeout=5)
    rows = stub.ListApiUsage(pb.ListApiUsageRequest(since_hour="2026-11-26T10"), timeout=5).rows
    first = rows[0]
    assert (first.hour, first.calls, first.tokens, first.limited) == ("2026-11-26T10", 5, 55, 1) and len(rows) == 3
    assert len(stub.ListApiUsage(pb.ListApiUsageRequest(since_hour="2026-11-26T11"), timeout=5).rows) == 2
    by_key = stub.CallsByKey(pb.CallsByKeyRequest(provider="groq", since_hour="2026-11-26T00"), timeout=5).calls
    assert dict(by_key) == {0: 6}


def test_import_is_repeatable_and_reports_a_name_clash(stub):
    users = [pb.ImportedUser(id="a1", username="Alice", password_hash="x", created_at="t1"),
             pb.ImportedUser(id="b1", username="bob", password_hash="x", created_at="t2")]
    first = stub.ImportUsers(pb.ImportUsersRequest(users=users), timeout=20)
    again = stub.ImportUsers(pb.ImportUsersRequest(users=users), timeout=20)
    assert first.users_created == 2 and again.users_created == 0 and not again.skipped_users
    clash = stub.ImportUsers(pb.ImportUsersRequest(users=[
        pb.ImportedUser(id="a2", username="ALICE", password_hash="x", created_at="t3")]), timeout=20)
    assert clash.users_created == 0 and clash.skipped_users[0].startswith("a2:")


# ---------------------------------------------------------------- the orchestrator when the service is down
@pytest.fixture
def service_down(monkeypatch):
    """The orchestrator's User service address points at nothing: the real service of the session is untouched."""
    from backend import clients
    from backend.config.settings import settings
    monkeypatch.setattr(settings, "user_service_addr", "localhost:1")
    clients.reset_clients()
    yield
    monkeypatch.undo()
    clients.reset_clients()


def test_with_the_service_down_nobody_is_logged_in(signed_in_cookie_factory):
    from fastapi.testclient import TestClient

    from backend.app import app
    token = signed_in_cookie_factory()
    browser = TestClient(app)
    browser.cookies.set("login", token)
    assert browser.get("/api/auth/me").status_code == 200
    with signed_in_cookie_factory.down():
        for path in ("/api/auth/me", "/api/auth/state", "/api/conversations", "/api/documents", "/api/admin/usage"):
            assert browser.get(path).status_code == 503, path
        browser.cookies.clear()
        browser.cookies.set("login", "a-token-nobody-issued")
        assert browser.get("/api/auth/me").status_code == 503   # can't tell: refused, never let through
        with pytest.raises(Exception) as closed:
            with browser.websocket_connect("/api/ws"):
                pass
        assert getattr(closed.value, "code", None) == 1013
    browser.cookies.set("login", token)
    assert browser.get("/api/auth/me").status_code == 200   # and back when it returns


@pytest.fixture
def signed_in_cookie_factory(monkeypatch):
    """A login token from the session's real service, plus a context manager that points the orchestrator at
    nothing for a while."""
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from backend import clients
    from backend.app import app
    from backend.config.settings import settings
    from backend.library import auth
    name = f"down_{threading.get_ident()}"
    reply = TestClient(app).post("/api/auth/signup", json={"username": name, "password": PASSWORD, "invite": "let-in"})
    if reply.status_code != 200:   # the first account is open; later ones need the invite code
        monkeypatch.setattr("backend.library.config.SIGNUP_CODE", "let-in")
        reply = TestClient(app).post("/api/auth/signup", json={"username": name, "password": PASSWORD,
                                                               "invite": "let-in"})
    assert reply.status_code == 200, reply.text
    token = reply.cookies.get(auth.COOKIE)
    real = settings.user_service_addr

    @contextmanager
    def down():
        settings.user_service_addr = "localhost:1"
        clients.reset_clients()
        try:
            yield
        finally:
            settings.user_service_addr = real
            clients.reset_clients()

    def make():
        return token
    make.down = down
    yield make
    settings.user_service_addr = real
    clients.reset_clients()


def test_user_for_raises_instead_of_answering_none_when_the_service_is_down(service_down):
    from backend.library import auth
    assert auth.user_for(None) is None            # no cookie needs no service: not logged in
    with pytest.raises(auth.UserServiceDown):
        auth.user_for("some-token")
    with pytest.raises(auth.UserServiceDown):
        auth.log_in("alice", PASSWORD)
    from backend.library import embed_budget
    with pytest.raises(grpc.RpcError):
        embed_budget.used("u1")


# ---------------------------------------------------------------- migration
def make_source(path: Path, with_usage: bool = True) -> Path:
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE auth_tokens (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL);
            CREATE TABLE embed_usage (day TEXT NOT NULL, user_id TEXT NOT NULL, chunks INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, user_id));
            CREATE TABLE api_usage (hour TEXT NOT NULL, provider TEXT NOT NULL, key_index INTEGER NOT NULL,
                model TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
                limited INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (hour, provider, key_index, model));
        """)
        con.execute("INSERT INTO users VALUES ('u1', 'alice', ?, '2026-01-01T00:00:00+00:00')", (day7_hash(PASSWORD),))
        con.execute("INSERT INTO users VALUES ('u2', 'bob', ?, '2026-01-02T00:00:00+00:00')",
                    (accounts.hash_password(PASSWORD),))
        con.execute("INSERT INTO auth_tokens VALUES (?, 'u1', '2026-11-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00')",
                    (hashlib.sha256(b"alice-cookie").hexdigest(),))
        con.execute("INSERT INTO auth_tokens VALUES ('dead', 'ghost', 't', '2099-01-01T00:00:00+00:00')")
        if with_usage:
            con.execute("INSERT INTO embed_usage VALUES ('2026-11-25', 'u1', 40)")
            con.execute("INSERT INTO api_usage VALUES ('2026-11-25T09', 'groq', 0, 'llama', 7, 900, 1)")
    return path


def test_migration_copies_verifies_and_is_safe_to_repeat(running, stub, tmp_path):
    source = make_source(tmp_path / "app.db")
    report = migrate.migrate(source, running.address, tmp_path / "backups")
    assert report.ok, report.checks
    assert report.created == {"users": 2, "tokens": 1, "embed_usage": 1, "api_usage": 1}
    assert report.backup and (report.backup / "app.db").is_file()
    assert any("ghost" in line or "logins of accounts" in line for line in report.skipped)
    # the old cookie still works, the old passwords still log in, usage is there
    assert who(stub, "alice-cookie").user.id == "u1"
    assert stub.LogIn(pb.LogInRequest(username="alice", password=PASSWORD), timeout=20).user.id == "u1"
    assert stub.LogIn(pb.LogInRequest(username="bob", password=PASSWORD), timeout=20).user.id == "u2"
    assert stub.GetEmbedUsage(pb.GetEmbedUsageRequest(day="2026-11-25", user_id="u1"), timeout=5).chunks == 40
    assert stub.CallsByKey(pb.CallsByKeyRequest(provider="groq", since_hour="2026-11-25T00"), timeout=5).calls[0] == 7
    again = migrate.migrate(source, running.address, tmp_path / "backups2")   # a second run copies nothing
    assert again.ok and again.created == {"users": 0, "tokens": 0, "embed_usage": 0, "api_usage": 0}
    assert stub.GetEmbedUsage(pb.GetEmbedUsageRequest(day="2026-11-25", user_id="u1"), timeout=5).chunks == 40
    with sqlite3.connect(source) as con:   # the source is untouched
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_migration_fails_when_a_name_is_already_another_account(running, stub, tmp_path):
    sign_up(stub, "Alice")   # made in the service before the migration, under another id
    report = migrate.migrate(make_source(tmp_path / "app.db"), running.address, None)
    assert not report.ok and any("u1" in line for line in report.skipped)
    assert report.created["users"] == 1   # bob still came over


def test_migration_works_without_usage_tables_and_refuses_what_it_cannot_use(running, tmp_path):
    only_users = tmp_path / "users-only.db"
    with sqlite3.connect(only_users) as con:
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, "
                    "password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        con.execute("INSERT INTO users VALUES ('u1', 'alice', 'x', 't')")
    assert migrate.migrate(only_users, running.address, None).ok
    with pytest.raises(migrate.MigrationError, match="no database"):
        migrate.migrate(tmp_path / "missing.db", running.address, None)
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(migrate.MigrationError, match="nothing to migrate"):
        migrate.migrate(empty, running.address, None)
    with pytest.raises(migrate.MigrationError, match="User service"):
        migrate.migrate(only_users, "localhost:1", None)
