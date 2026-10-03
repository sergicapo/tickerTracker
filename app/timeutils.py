"""
Shared time helpers.

All timestamps in the database are *naive* UTC. `utcnow()` returns the current
UTC time without timezone info, keeping existing rows and new ones consistent.
(Replacing `datetime.utcnow()` — deprecated in Python 3.12+.)
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time, naive (no tzinfo), matching the database format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
