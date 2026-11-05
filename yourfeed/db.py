from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .config import database_url

WORKER_LOCK = 7011001


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)
