import os
from pathlib import Path

# The test client sends this Host header; the app does not accept it by default.
os.environ.setdefault("YOURFEED_ALLOWED_HOSTS", "testserver")

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine(monkeypatch):
    """A migrated, empty database. Uses YOURFEED_TEST_DATABASE_URL and drops everything in it."""
    url = os.environ.get("YOURFEED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("YOURFEED_TEST_DATABASE_URL is not set")
    monkeypatch.setenv("DATABASE_URL", url)
    from yourfeed.config import database_url
    engine = create_engine(database_url())
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield engine
    engine.dispose()
