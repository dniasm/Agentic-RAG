import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.dependencies import get_current_user
from backend.api.security import create_access_token, hash_password, verify_password
from backend.models import database as db
from backend.models.schemas import Credentials, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["Auth"])


def _token_for(user: dict) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user["id"], user["username"]),
        user=UserOut(id=user["id"], username=user["username"]),
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: Credentials):
    if db.get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    try:
        user = db.create_user(body.username, hash_password(body.password))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Username already taken")
    return _token_for(user)


@router.post("/login", response_model=TokenOut)
def login(body: Credentials):
    user = db.get_user_by_username(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return _token_for(user)


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return UserOut(**user)
