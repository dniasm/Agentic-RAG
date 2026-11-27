"""Facade over the thin clients the orchestrator uses instead of a database: the Conversation service's conversations
and messages, the Memory service's facts and (for tests and scripts) the User service's bulk-import calls.

The orchestrator holds no database of its own: the library catalog is the Document service's (backend/library/catalog.py
calls it). This module re-exports the names so `from backend.models import database as db` keeps working everywhere.
"""
from backend.models.conversations import (  # noqa: F401
    add_message, create_conversation, delete_conversation, get_conversation, list_conversations, list_messages,
    list_tool_messages, rename_conversation, user_stats,
)
from backend.models.memory import add_fact, delete_fact, list_memories, set_fact  # noqa: F401
from backend.models.users import create_user, get_user  # noqa: F401
