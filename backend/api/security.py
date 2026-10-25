"""Password hashing (bcrypt) and HS256 JWT access tokens (stdlib, no extra deps)."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

import bcrypt

from backend.config.settings import settings


# ---------------------------------------------------------------- passwords
def _prehash(password: str) -> bytes:
    # bcrypt only looks at the first 72 bytes; pre-hashing keeps long passphrases fully significant.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- JWT
def _load_secret() -> bytes:
    if settings.jwt_secret:
        return settings.jwt_secret.encode()
    # Persist a generated secret so tokens survive restarts.
    path = Path(settings.database_path).parent / ".jwt_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(64))
    return path.read_text().strip().encode()


_SECRET = _load_secret()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_access_token(user_id: str, username: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"sub": user_id, "name": username, "iat": now, "exp": now + settings.jwt_expire_minutes * 60}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    sig = hmac.new(_SECRET, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


def decode_access_token(token: str) -> dict | None:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        expected = hmac.new(_SECRET, f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return None
        header = json.loads(_unb64(header_b64))
        if header.get("alg") != "HS256":
            return None
        payload = json.loads(_unb64(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, json.JSONDecodeError):
        return None
