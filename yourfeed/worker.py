"""Run updates on the configured interval, or once with --once."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone, tzinfo

from sqlalchemy import text
from sqlalchemy.engine import Engine

from yourfeed import config
from yourfeed.db import get_engine
from yourfeed.pipeline.run import run_cycle

POLL_SECONDS = 30


def slot_start(now: datetime, interval_minutes: int, tz: tzinfo) -> datetime:
    """Slots start at local midnight and repeat every interval."""
    local = now.astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = int((local - midnight).total_seconds() // 60)
    return (midnight + timedelta(minutes=minutes - minutes % interval_minutes)).astimezone(timezone.utc)


def is_due(now: datetime, interval_minutes: int, tz: tzinfo, last_started: datetime | None,
           refresh_requested: datetime | None) -> bool:
    if last_started is None:
        return True
    if refresh_requested is not None and refresh_requested > last_started:
        return True
    return last_started < slot_start(now, interval_minutes, tz)


def has_work(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(text("""
            SELECT EXISTS (SELECT 1 FROM genres WHERE weight > 0)
        """)).scalar_one())


def tick(engine: Engine, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    with engine.connect() as connection:
        interval, requested = connection.execute(text("""
            SELECT interval_minutes, refresh_requested_at FROM settings WHERE id = 1
        """)).one()
        last = connection.execute(text("SELECT max(started_at) FROM runs")).scalar_one()
    if not is_due(now, interval, config.timezone(), last, requested) or not has_work(engine):
        return None
    return run_cycle(engine)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="YourFeed updates")
    parser.add_argument("--once", action="store_true", help="run one update now and exit")
    args = parser.parse_args()
    engine = get_engine()
    if args.once:
        _print(run_cycle(engine) if has_work(engine) else {"status": "no_genres"})
        return
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    while not stopping.is_set():
        try:
            result = tick(engine)
            if result is not None:
                _print(result)
        except Exception as exc:  # keep the loop alive; the run row records the failure
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)[:300]}), file=sys.stderr, flush=True)
        stopping.wait(POLL_SECONDS)


if __name__ == "__main__":
    main()
