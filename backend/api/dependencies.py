"""Shared FastAPI dependencies."""
from fastapi import HTTPException, Request, status

from backend.library import auth


def get_current_user(request: Request) -> dict:
    """The signed-in user, from the HttpOnly login cookie (backend/library/auth.py). Every route but health and
    sign-in uses it, the chat stream and the library's routes included."""
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in.")
    return auth.public(user)
