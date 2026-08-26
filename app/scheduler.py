"""
APScheduler configuration for Ticker Tracker.

Three scheduled jobs:
  1. Intraday snapshot  — every N minutes (default 5), all enabled tickers
  2. Daily OHLCV        — every day at 00:05 UTC, fetch yesterday's data
  3. Historical backfill— run once on startup per enabled ticker,
                          from HISTORICAL_START_DATE (or last recorded date)
                          up to today.
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import crud, data_fetcher
from app.database import get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheduler instance (created once, started/stopped by lifespan)
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler(timezone="UTC")


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

    for ticker in tickers:
        await _fetch_and_store_intraday(ticker.id, ticker.symbol)


async def _fetch_and_store_intraday(ticker_id: int, symbol: str) -> None:
    result = await asyncio.get_event_loop().run_in_executor(
        None, data_fetcher.fetch_current_price, symbol
    )
    if result is None:
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
    yesterday = date.today() - timedelta(days=1)
    logger.info("Running daily OHLCV job for %s.", yesterday)

    async with get_session() as session:
        tickers = await crud.list_tickers(session, enabled_only=True)

    for ticker in tickers:
        await _fetch_and_store_daily(ticker.id, ticker.symbol, yesterday, yesterday)


async def _fetch_and_store_daily(
    ticker_id: int,
    symbol: str,
    start: date,
    end: date,
) -> None:
    rows = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: data_fetcher.fetch_historical(symbol, start, end),
    )
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
    today = date.today()

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
        await _fetch_and_store_daily(ticker.id, ticker.symbol, start, today)


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


async def start_scheduler() -> None:
    """Start the APScheduler and trigger the one-shot historical backfill."""
    setup_scheduler()
    scheduler.start()
    logger.info("Scheduler started.")

    # Trigger historical backfill asynchronously (non-blocking)
    asyncio.create_task(
        job_historical_backfill(),
        # Python 3.7+: give the task a name for easier debugging
    )
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
