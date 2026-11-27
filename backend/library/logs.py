"""The library's major events, in the app-wide log system (services/common/log.py).

event("rename", doc="ab12", old="A", new="B") writes  `rename doc=ab12 old=A new="B C"`  to the audit file
(logs/audit/orchestrator-<date>.log) and, like every log line, to the service file (logs/orchestrator/<date>.log)
with the request id of the request that caused it. Setup is not here any more: backend/app.py calls setup_logging once.
"""
from services.common.log import audit


def event(name: str, **fields) -> None:
    """Record a major event: its name, then key=value pairs (a value with spaces is quoted, a long one is cut)."""
    audit(name, **fields)
