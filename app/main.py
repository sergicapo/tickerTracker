"""
Ticker Tracker — FastAPI application entry point.

Startup sequence:
  1. Configure logging
  2. Load .env (python-dotenv)
  3. Initialise the SQLite database (create tables if needed)
  4. Seed default tickers if the tickers table is empty
  5. Start APScheduler (which also triggers historical backfill)

Shutdown sequence:
  1. Stop APScheduler
  2. Dispose the SQLAlchemy engine
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse

# Load .env before anything else so env vars are available to other modules
load_dotenv()

# ---------------------------------------------------------------------------
# Logging configuration — must happen before other imports that use logging
# ---------------------------------------------------------------------------
log_level_name: str = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level_name, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application imports (after logging is configured)
# ---------------------------------------------------------------------------
from app import crud, scheduler as sched
from app.auth import verify_credentials
from app.database import close_db, get_db, init_db
from app.schemas import (
    CurrentPriceResponse,
    DailyPriceResponse,
    HealthResponse,
    IntradayPriceResponse,
    TickerCreate,
    TickerResponse,
    TickerUpdate,
)

# ---------------------------------------------------------------------------
# Default tickers to seed when the database is empty
# ---------------------------------------------------------------------------
DEFAULT_TICKERS = [
    {"symbol": "AAPL",    "name": "Apple Inc."},
    {"symbol": "MSFT",    "name": "Microsoft Corporation"},
    {"symbol": "GOOGL",   "name": "Alphabet Inc."},
    {"symbol": "AMZN",    "name": "Amazon.com Inc."},
    {"symbol": "BTC-USD", "name": "Bitcoin USD"},
]


# ---------------------------------------------------------------------------
# Lifespan context manager (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown logic."""
    logger.info("=== Ticker Tracker starting up ===")

    # 1. Initialise database tables
    await init_db()

    # 2. Seed default tickers if the table is empty
    from app.database import get_session as _get_session
    async with _get_session() as session:
        existing = await crud.list_tickers(session)
        if not existing:
            logger.info("Seeding default tickers: %s", [t["symbol"] for t in DEFAULT_TICKERS])
            for t in DEFAULT_TICKERS:
                await crud.create_ticker(session, TickerCreate(**t))

    # 3. Start the scheduler (triggers backfill in background)
    await sched.start_scheduler()

    logger.info("=== Ticker Tracker ready ===")
    yield  # --- application is running ---

    logger.info("=== Ticker Tracker shutting down ===")
    await sched.stop_scheduler()
    await close_db()
    logger.info("=== Shutdown complete ===")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Ticker Tracker",
    description="Financial ticker tracking API backed by Yahoo Finance and SQLite.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health endpoint (no auth)
# ---------------------------------------------------------------------------
@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="Health check (public)",
)
async def health_check():
    return HealthResponse(status="ok", timestamp=datetime.utcnow())


# ---------------------------------------------------------------------------
# Ticker management endpoints
# ---------------------------------------------------------------------------
@app.get(
    "/api/tickers",
    response_model=List[TickerResponse],
    tags=["Tickers"],
    summary="List all tickers",
)
async def list_tickers(
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    tickers = await crud.list_tickers(db)
    return tickers


@app.post(
    "/api/tickers",
    response_model=TickerResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Tickers"],
    summary="Add a new ticker",
)
async def add_ticker(
    body: TickerCreate,
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    existing = await crud.get_ticker_by_symbol(db, body.symbol)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ticker '{body.symbol.upper()}' already exists.",
        )
    ticker = await crud.create_ticker(db, body)

    # Trigger immediate historical backfill for the new ticker (background task)
    import asyncio
    asyncio.create_task(sched.job_historical_backfill(ticker_id=ticker.id))
    logger.info("Historical backfill triggered for new ticker %s.", ticker.symbol)

    return ticker


@app.put(
    "/api/tickers/{symbol}",
    response_model=TickerResponse,
    tags=["Tickers"],
    summary="Update ticker name or enabled state",
)
async def update_ticker(
    symbol: str,
    body: TickerUpdate,
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    ticker = await crud.get_ticker_by_symbol(db, symbol)
    if not ticker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker '{symbol.upper()}' not found.")
    updated = await crud.update_ticker(db, ticker, body)
    return updated


@app.delete(
    "/api/tickers/{symbol}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Tickers"],
    summary="Delete a ticker and all its price data",
)
async def delete_ticker(
    symbol: str,
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    ticker = await crud.get_ticker_by_symbol(db, symbol)
    if not ticker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker '{symbol.upper()}' not found.")
    await crud.delete_ticker(db, ticker)


# ---------------------------------------------------------------------------
# Quote endpoints
# ---------------------------------------------------------------------------
@app.get(
    "/api/quotes/{symbol}/current",
    response_model=CurrentPriceResponse,
    tags=["Quotes"],
    summary="Get current price with previous session close for a symbol",
)
async def get_current_quote(
    symbol: str,
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    ticker = await crud.get_ticker_by_symbol(db, symbol)
    if not ticker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker '{symbol.upper()}' not found.")

    intraday = await crud.get_latest_intraday(db, ticker.id)
    if not intraday:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No current price available for '{symbol.upper()}' yet.",
        )

    previous = await crud.get_latest_daily_price_before(db, ticker.id, intraday.timestamp.date())

    return CurrentPriceResponse(
        ticker_id=ticker.id,
        timestamp=intraday.timestamp,
        price=intraday.price,
        volume=intraday.volume,
        previous_close=previous.close if previous else None,
        previous_close_date=previous.date if previous else None,
    )


@app.get(
    "/api/quotes/{symbol}/date/{price_date}",
    response_model=DailyPriceResponse,
    tags=["Quotes"],
    summary="Get daily price for a symbol on a specific date (YYYY-MM-DD)",
)
async def get_quote_by_date(
    symbol: str,
    price_date: date,
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    ticker = await crud.get_ticker_by_symbol(db, symbol)
    if not ticker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker '{symbol.upper()}' not found.")
    price = await crud.get_daily_price(db, ticker.id, price_date)
    if not price:
        price = await crud.get_latest_daily_price_before(db, ticker.id, price_date)
    if not price:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No daily price for '{symbol.upper()}' on or before {price_date}.",
        )
    return price


@app.get(
    "/api/quotes/{symbol}/history",
    response_model=List[DailyPriceResponse],
    tags=["Quotes"],
    summary="Get daily price history for a date range",
)
async def get_quote_history(
    symbol: str,
    start: date = Query(..., description="Start date (YYYY-MM-DD, inclusive)"),
    end: date = Query(..., description="End date (YYYY-MM-DD, inclusive)"),
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'end' date must be >= 'start' date.",
        )
    ticker = await crud.get_ticker_by_symbol(db, symbol)
    if not ticker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker '{symbol.upper()}' not found.")
    prices = await crud.get_daily_prices_range(db, ticker.id, start, end)
    return prices


@app.get(
    "/api/quotes/{symbol}/intraday",
    response_model=List[IntradayPriceResponse],
    tags=["Quotes"],
    summary="Get all intraday snapshots for a symbol on a given date",
)
async def get_intraday(
    symbol: str,
    date: date = Query(..., description="Date to retrieve intraday data for (YYYY-MM-DD)"),
    db=Depends(get_db),
    _user: str = Depends(verify_credentials),
):
    ticker = await crud.get_ticker_by_symbol(db, symbol)
    if not ticker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker '{symbol.upper()}' not found.")
    prices = await crud.get_intraday_for_date(db, ticker.id, date)
    if not prices:
        fallback_date = await crud.get_latest_intraday_date_before(db, ticker.id, date)
        if fallback_date:
            prices = await crud.get_intraday_for_date(db, ticker.id, fallback_date)
    return prices
