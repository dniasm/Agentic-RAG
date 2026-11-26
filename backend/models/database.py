"""SQLite persistence facade: users, conversations and messages, user facts (documents: backend/library/catalog.py, same file).

The code lives in connection.py (shared connection and transactions), users.py, conversations.py and memory.py;
this module re-exports their names so `from backend.models import database as db` keeps working everywhere.
"""
import sys
import types

from backend.models import connection as _connection
from backend.models import conversations as _conversations
from backend.models import memory as _memory
from backend.models import users as _users
from backend.models.connection import _all, _get_conn, _lock, _now, _one, tx  # noqa: F401
from backend.models.conversations import (  # noqa: F401
    _STAT_COLUMNS, _add_message_stat_columns, _stats_from_meta, add_message, create_conversation,
    delete_conversation, get_conversation, list_conversations, list_messages, list_tool_messages,
    rename_conversation, user_stats,
)
from backend.models.memory import (  # noqa: F401
    _decode_values, _migrate_memory_to_facts, add_fact, delete_fact, list_memories, set_fact,
)
from backend.models.users import create_user, get_user  # noqa: F401

_MIGRATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""
SCHEMA = _users.SCHEMA + _memory.SCHEMA + _MIGRATIONS_SCHEMA + _conversations.SCHEMA


class _Facade(types.ModuleType):
    """`db._conn = ...` (tests, merge_library) must reach the connection module that really holds it."""

    def __getattr__(self, name):
        if name == "_conn":
            return _connection._conn
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name == "_conn":
            _connection._conn = value
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Facade


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        _migrate_memory_to_facts(c)
        _add_message_stat_columns(c)
    from backend.library import catalog
    catalog.init()
