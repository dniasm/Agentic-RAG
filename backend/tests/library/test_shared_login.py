"""One users table, two hash formats: day7's sign-up made bcrypt hashes before the merge, the library makes PBKDF2."""
import base64
import hashlib

import bcrypt
from fastapi.testclient import TestClient

from backend.app import app
from backend.models import database as db
from services.user import accounts


def day7_hash(password: str) -> str:
    """As day7's sign-up did: bcrypt over the password's SHA-256."""
    return bcrypt.hashpw(base64.b64encode(hashlib.sha256(password.encode()).digest()), bcrypt.gensalt()).decode()


def test_a_day7_bcrypt_user_logs_in():
    db.create_user("bcrypt_person", day7_hash("correct horse 1"))
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "bcrypt_person", "password": "wrong one"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "bcrypt_person", "password": "correct horse 1"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "bcrypt_person"


def test_a_pbkdf2_user_logs_in():
    db.create_user("pbkdf2_person", accounts.hash_password("correct horse 2"))
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "pbkdf2_person", "password": "nope nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "pbkdf2_person", "password": "correct horse 2"})
    assert r.status_code == 200 and c.get("/api/auth/me").json()["username"] == "pbkdf2_person"
