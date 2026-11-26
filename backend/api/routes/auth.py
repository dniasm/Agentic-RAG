"""Sign-up, log-in and log-out for the whole app: an HttpOnly cookie holding a random token (backend/library/auth.py
keeps only its hash), an invite code after the first account, and a limit on wrong passwords."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend import security
from backend.library import auth
from backend.models.schemas import UserOut
from backend.realtime.hub import hub

router = APIRouter(prefix="/auth", tags=["Auth"])


class SignUpIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    invite: str = Field(default="", max_length=128)


class LogInIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


def _address(request: Request) -> str:
    return request.client.host if request.client else ""


def _signed_in(request: Request, user: dict, token: str) -> JSONResponse:
    response = JSONResponse({"user": auth.public(user)})
    # Secure (HTTPS only) everywhere but local addresses: plain-HTTP development (backend/security.py)
    response.set_cookie(auth.COOKIE, token, max_age=auth.LOGIN_DAYS * 86400, path="/", httponly=True,
                        samesite="lax", secure=security.secure_cookie(request.headers.get("host", "")))
    return response


@router.get("/state")
def auth_state(request: Request):
    """Signed in or not, and whether a new account can be made ("first", "invite" or "closed")."""
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    return {"user": auth.public(user) if user else None, "signups": auth.signups_open()}


@router.post("/signup")
def sign_up(body: SignUpIn, request: Request):
    try:
        user, token = auth.sign_up(body.username, body.password, body.invite, _address(request))
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e)) from e
    return _signed_in(request, user, token)


@router.post("/login")
def log_in(body: LogInIn, request: Request):
    try:
        user, token = auth.log_in(body.username, body.password, _address(request))
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e)) from e
    return _signed_in(request, user, token)


@router.post("/logout")
def log_out(request: Request):
    token = request.cookies.get(auth.COOKIE)
    user = auth.user_for(token)   # who it was, so this login's open sockets can be closed
    auth.log_out(token)
    if user:
        hub.close_session(user["id"], token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="lax",
                           secure=security.secure_cookie(request.headers.get("host", "")))
    return response


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return UserOut(**user)
