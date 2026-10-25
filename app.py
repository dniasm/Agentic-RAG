"""Lets `uvicorn app:app --reload` work from the project root as well as from backend/.

The real application lives in backend/app.py; this file only re-exports it.
"""
from backend.app import app  # noqa: F401
