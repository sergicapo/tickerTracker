"""
APScheduler configuration for Ticker Tracker.

Scheduled jobs:
  1. Intraday snapshot  — every N minutes (default 5), all enabled tickers
  2. Daily OHLCV        — every day at 00:05 UTC, fetch yesterday's data
  3. Intraday retention — every day at 01:00 UTC, delete intraday snapshots
                          older than INTRADAY_RETENTION_DAYS (default 30)
  4. Historical backfill— run once on startup per enabled ticker,
                          from HISTORICAL_START_DATE (or last recorded date)
                          up to today.
"""
import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Coroutine, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import crud, data_fetcher
from app.database import get_session
from app.sources import DataSource
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheduler instance (created once, started/stopped by lifespan)
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler(timezone="UTC")

# Maximum number of tickers fetched concurrently by the intraday snapshot job.
INTRADAY_MAX_CONCURRENT: int = 4

# ---------------------------------------------------------------------------
# Background task tracking
#
# asyncio.create_task() results are only weakly referenced by the event loop;
# keeping a strong reference in a set prevents the task from being garbage
# collected mid-flight and lets us log unhandled exceptions.
# ---------------------------------------------------------------------------
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _background_task_done(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background task %s failed: %s", task.get_name(), task.exception())


def spawn_background(coro: Coroutine, name: Optional[str] = None) -> asyncio.Task:
    """Create a tracked background task that survives GC and logs failures."""
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_background_task_done)
    return task


# ---------------------------------------------------------------------------
# Job: intraday snapshot
# ---------------------------------------------------------------------------

async def job_fetch_intraday() -> None:
    """
    Fetch the current price for every enabled ticker and persist an
    IntradayPrice row. Runs every FETCH_INTERVAL_MINUTES minutes.
    """
    logger.debug("Running intraday price snapshot job.")
    async with get_session() as session:
        tickers = await crud.list_tickers(session, enabled_only=True)

    if not tickers:
        logger.debug("No enabled tickers — skipping intraday snapshot.")
        return

    semaphore = asyncio.Semaphore(INTRADAY_MAX_CONCURRENT)

    async def _fetch_one(ticker) -> None:
        async with semaphore:
            await _fetch_and_store_intraday(
                ticker.id, ticker.symbol, DataSource(ticker.data_source), ticker.source_config
            )

    await asyncio.gather(
        *(_fetch_one(t) for t in tickers),
        return_exceptions=False,  # _fetch_and_store_intraday never raises; it logs errors
    )


async def _fetch_and_store_intraday(
    ticker_id: int,
    symbol: str,
    data_source: DataSource,
    source_config: Optional[dict],
) -> None:
    result = await asyncio.to_thread(
        data_fetcher.fetch_current_price, symbol, data_source, source_config
    )
    if result is None or result.get("price") is None:
        logger.warning("Could not fetch current price for %s — skipping.", symbol)
        return

    async with get_session() as session:
        await crud.insert_intraday_price(
            session,
            ticker_id=ticker_id,
            timestamp=result["timestamp"],
            price=result["price"],
            volume=result.get("volume"),
        )
    logger.info(
        "Stored intraday price for %s: %.4f @ %s",
        symbol,
        result["price"],
        result["timestamp"].isoformat(),
    )


# ---------------------------------------------------------------------------
# Job: daily OHLCV
# ---------------------------------------------------------------------------

async def job_fetch_daily() -> None:
    """
    Fetch yesterday's daily OHLCV for every enabled ticker.
    Runs at 00:05 UTC so the previous day's data is fully settled.
    """
    yesterday = utcnow().date() - timedelta(days=1)
    logger.info("Running daily OHLCV job for %s.", yesterday)

    async with get_session() as session:
        tickers = await crud.list_tickers(session, enabled_only=True)

    for ticker in tickers:
        await _fetch_and_store_daily(
            ticker.id,
            ticker.symbol,
            yesterday,
            yesterday,
            DataSource(ticker.data_source),
            ticker.source_config,
        )


async def _fetch_and_store_daily(
    ticker_id: int,
    symbol: str,
    start: date,
    end: date,
    data_source: DataSource,
    source_config: Optional[dict],
) -> None:
    rows = await asyncio.to_thread(
        data_fetcher.fetch_historical, symbol, start, end, data_source, source_config
    )

    covered_dates = {row["date"] for row in rows}
    missing_dates = [
        start + timedelta(days=n)
        for n in range((end - start).days + 1)
        if start + timedelta(days=n) not in covered_dates
    ]
    if missing_dates:
        async with get_session() as session:
            for missing_date in missing_dates:
                derived = await crud.derive_daily_bar_from_intraday(session, ticker_id, missing_date)
                if derived is not None:
                    rows.append(derived)

    if not rows:
        logger.warning("No daily data returned for %s (%s–%s).", symbol, start, end)
        return

    async with get_session() as session:
        count = await crud.bulk_upsert_daily_prices(session, ticker_id, rows)
    logger.info("Stored %d daily price row(s) for %s.", count, symbol)


# ---------------------------------------------------------------------------
# Job: historical backfill (run-once on startup)
# ---------------------------------------------------------------------------

async def job_historical_backfill(ticker_id: Optional[int] = None) -> None:
    """
    For each enabled ticker (or a specific ticker_id), fetch all daily bars
    from the configured HISTORICAL_START_DATE (or last recorded date + 1 day)
    up to today.

    Pass ticker_id to backfill a single newly added ticker.
    """
    default_start = _parse_env_date(
        "HISTORICAL_START_DATE", date(2020, 1, 1)
    )
    today = utcnow().date()

    async with get_session() as session:
        if ticker_id is not None:
            ticker = await crud.get_ticker_by_id(session, ticker_id)
            tickers = [ticker] if ticker else []
        else:
            tickers = list(await crud.list_tickers(session, enabled_only=True))

        # Resolve last-recorded dates while session is open
        ticker_start_map: dict[int, date] = {}
        for t in tickers:
            last_date = await crud.get_latest_daily_date(session, t.id)
            if last_date is not None:
                # Resume from the day after the last recorded date
                start = last_date + timedelta(days=1)
            else:
                start = default_start
            ticker_start_map[t.id] = start

    for ticker in tickers:
        start = ticker_start_map[ticker.id]
        if start > today:
            logger.info(
                "Ticker %s is already up to date (last date=%s).",
                ticker.symbol,
                start - timedelta(days=1),
            )
            continue
        logger.info(
            "Backfilling %s from %s to %s.", ticker.symbol, start, today
        )
        await _fetch_and_store_daily(
            ticker.id,
            ticker.symbol,
            start,
            today,
            DataSource(ticker.data_source),
            ticker.source_config,
        )


# ---------------------------------------------------------------------------
# Job: intraday retention (daily cleanup)
# ---------------------------------------------------------------------------

async def job_cleanup_intraday() -> None:
    """
    Delete intraday snapshots older than INTRADAY_RETENTION_DAYS (default 30).
    Prevents the intraday_prices table from growing without bound.
    """
    try:
        retention_days = int(os.getenv("INTRADAY_RETENTION_DAYS", "30"))
    except ValueError:
        logger.warning("Invalid INTRADAY_RETENTION_DAYS value — using default 30.")
        retention_days = 30
    if retention_days <= 0:
        logger.debug("INTRADAY_RETENTION_DAYS=%d — retention disabled.", retention_days)
        return

    cutoff = utcnow() - timedelta(days=retention_days)
    async with get_session() as session:
        tickers = list(await crud.list_tickers(session))
        total_deleted = 0
        for ticker in tickers:
            total_deleted += await crud.delete_intraday_before(session, ticker.id, cutoff)

    if total_deleted:
        logger.info(
            "Intraday retention: deleted %d snapshot(s) older than %d day(s).",
            total_deleted, retention_days,
        )


# ---------------------------------------------------------------------------
# Scheduler lifecycle helpers
# ---------------------------------------------------------------------------

def setup_scheduler() -> None:
    """Register all jobs on the scheduler (does not start it)."""
    interval_minutes = int(os.getenv("FETCH_INTERVAL_MINUTES", "5"))

    # Job 1: intraday snapshot
    scheduler.add_job(
        job_fetch_intraday,
        trigger=IntervalTrigger(minutes=interval_minutes, timezone="UTC"),
        id="intraday_snapshot",
        name="Intraday price snapshot",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled intraday snapshot every %d minute(s).", interval_minutes)

    # Job 2: daily OHLCV at 00:05 UTC
    scheduler.add_job(
        job_fetch_daily,
        trigger=CronTrigger(hour=0, minute=5, timezone="UTC"),
        id="daily_ohlcv",
        name="Daily OHLCV fetch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled daily OHLCV fetch at 00:05 UTC.")

    # Job 3: intraday retention cleanup at 01:00 UTC
    scheduler.add_job(
        job_cleanup_intraday,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="intraday_retention",
        name="Intraday snapshot retention cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled intraday retention cleanup at 01:00 UTC.")


async def start_scheduler() -> None:
    """Start the APScheduler and trigger the one-shot historical backfill."""
    setup_scheduler()
    scheduler.start()
    logger.info("Scheduler started.")

    # Trigger historical backfill asynchronously (non-blocking)
    spawn_background(job_historical_backfill(), name="historical_backfill")
    logger.info("Historical backfill task launched in background.")


async def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_env_date(env_var: str, default: date) -> date:
    raw = os.getenv(env_var, "")
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "Invalid date format for %s=%r — using default %s.", env_var, raw, default
        )
        return default
