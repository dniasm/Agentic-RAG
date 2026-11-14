"""One users table, two hash formats: either login accepts a user made by the other side."""
from fastapi.testclient import TestClient

from backend.api import security
from backend.app import app
from backend.library import auth, catalog


def test_a_day7_bcrypt_user_logs_in_through_the_library():
    catalog.create_user("bcrypt_person", security.hash_password("correct horse 1"))
    c = TestClient(app)
    assert c.post("/api/library/auth/login", json={"username": "bcrypt_person", "password": "wrong one"}).status_code == 401
    r = c.post("/api/library/auth/login", json={"username": "bcrypt_person", "password": "correct horse 1"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "bcrypt_person"


def test_a_library_pbkdf2_user_logs_in_through_day7():
    from backend.models import database as db
    db.init_db()
    catalog.create_user("pbkdf2_person", auth.hash_password("correct horse 2"))
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "pbkdf2_person", "password": "nope nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "pbkdf2_person", "password": "correct horse 2"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "pbkdf2_person"
