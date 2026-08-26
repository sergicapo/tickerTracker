"""
CRUD operations against the database.
All functions receive an AsyncSession and return ORM objects or None.
"""
import logging
from datetime import date, datetime
from typing import Optional, Sequence

from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyPrice, IntradayPrice, Ticker
from app.schemas import TickerCreate, TickerUpdate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticker CRUD
# ---------------------------------------------------------------------------

async def get_ticker_by_symbol(session: AsyncSession, symbol: str) -> Optional[Ticker]:
    result = await session.execute(
        select(Ticker).where(Ticker.symbol == symbol.upper())
    )
    return result.scalar_one_or_none()


async def get_ticker_by_id(session: AsyncSession, ticker_id: int) -> Optional[Ticker]:
    result = await session.execute(select(Ticker).where(Ticker.id == ticker_id))
    return result.scalar_one_or_none()


async def list_tickers(session: AsyncSession, enabled_only: bool = False) -> Sequence[Ticker]:
    stmt = select(Ticker)
    if enabled_only:
        stmt = stmt.where(Ticker.enabled.is_(True))
    result = await session.execute(stmt.order_by(Ticker.symbol))
    return result.scalars().all()


async def create_ticker(session: AsyncSession, data: TickerCreate) -> Ticker:
    ticker = Ticker(
        symbol=data.symbol.upper(),
        name=data.name,
        enabled=True,
        created_at=datetime.utcnow(),
    )
    session.add(ticker)
    await session.flush()  # get the generated id without committing
    logger.info("Created ticker %s (id=%d)", ticker.symbol, ticker.id)
    return ticker


async def update_ticker(
    session: AsyncSession, ticker: Ticker, data: TickerUpdate
) -> Ticker:
    if data.name is not None:
        ticker.name = data.name
    if data.enabled is not None:
        ticker.enabled = data.enabled
    session.add(ticker)
    await session.flush()
    logger.info("Updated ticker %s", ticker.symbol)
    return ticker


async def delete_ticker(session: AsyncSession, ticker: Ticker) -> None:
    await session.delete(ticker)
    await session.flush()
    logger.info("Deleted ticker %s", ticker.symbol)


# ---------------------------------------------------------------------------
# Daily price CRUD
# ---------------------------------------------------------------------------

async def get_daily_price(
    session: AsyncSession, ticker_id: int, price_date: date
) -> Optional[DailyPrice]:
    result = await session.execute(
        select(DailyPrice).where(
            and_(DailyPrice.ticker_id == ticker_id, DailyPrice.date == price_date)
        )
    )
    return result.scalar_one_or_none()


async def get_daily_prices_range(
    session: AsyncSession,
    ticker_id: int,
    start_date: date,
    end_date: date,
) -> Sequence[DailyPrice]:
    result = await session.execute(
        select(DailyPrice)
        .where(
            and_(
                DailyPrice.ticker_id == ticker_id,
                DailyPrice.date >= start_date,
                DailyPrice.date <= end_date,
            )
        )
        .order_by(DailyPrice.date)
    )
    return result.scalars().all()


async def get_latest_daily_date(
    session: AsyncSession, ticker_id: int
) -> Optional[date]:
    """Return the most recent date we have daily data for, or None."""
    result = await session.execute(
        select(DailyPrice.date)
        .where(DailyPrice.ticker_id == ticker_id)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


async def get_latest_daily_price(
    session: AsyncSession, ticker_id: int
) -> Optional[DailyPrice]:
    """Return the most recent daily price row (last closed session)."""
    result = await session.execute(
        select(DailyPrice)
        .where(DailyPrice.ticker_id == ticker_id)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_daily_price_before(
    session: AsyncSession, ticker_id: int, before_date: date
) -> Optional[DailyPrice]:
    """Return the most recent daily price row strictly before `before_date`."""
    result = await session.execute(
        select(DailyPrice)
        .where(
            and_(
                DailyPrice.ticker_id == ticker_id,
                DailyPrice.date < before_date,
            )
        )
        .order_by(DailyPrice.date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_daily_price(
    session: AsyncSession,
    ticker_id: int,
    price_date: date,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    volume: Optional[int],
) -> DailyPrice:
    """Insert or update a single daily price row."""
    existing = await get_daily_price(session, ticker_id, price_date)
    if existing:
        existing.open = open_
        existing.high = high
        existing.low = low
        existing.close = close
        existing.volume = volume
        session.add(existing)
        return existing

    row = DailyPrice(
        ticker_id=ticker_id,
        date=price_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
    session.add(row)
    return row


async def bulk_upsert_daily_prices(
    session: AsyncSession,
    ticker_id: int,
    rows: list[dict],
) -> int:
    """
    Upsert a list of daily price dicts.
    Each dict must have keys: date, open, high, low, close, volume.
    Returns the number of rows processed.
    """
    count = 0
    for row in rows:
        await upsert_daily_price(
            session,
            ticker_id=ticker_id,
            price_date=row["date"],
            open_=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
        )
        count += 1
    await session.flush()
    return count


# ---------------------------------------------------------------------------
# Intraday price CRUD
# ---------------------------------------------------------------------------

async def get_latest_intraday(
    session: AsyncSession, ticker_id: int
) -> Optional[IntradayPrice]:
    result = await session.execute(
        select(IntradayPrice)
        .where(IntradayPrice.ticker_id == ticker_id)
        .order_by(IntradayPrice.timestamp.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_intraday_for_date(
    session: AsyncSession, ticker_id: int, price_date: date
) -> Sequence[IntradayPrice]:
    start_dt = datetime.combine(price_date, datetime.min.time())
    end_dt = datetime.combine(price_date, datetime.max.time())
    result = await session.execute(
        select(IntradayPrice)
        .where(
            and_(
                IntradayPrice.ticker_id == ticker_id,
                IntradayPrice.timestamp >= start_dt,
                IntradayPrice.timestamp <= end_dt,
            )
        )
        .order_by(IntradayPrice.timestamp)
    )
    return result.scalars().all()


async def get_latest_intraday_date_before(
    session: AsyncSession, ticker_id: int, before_date: date
) -> Optional[date]:
    """Return the most recent date with intraday data strictly before `before_date`."""
    cutoff = datetime.combine(before_date, datetime.min.time())
    result = await session.execute(
        select(IntradayPrice.timestamp)
        .where(
            and_(
                IntradayPrice.ticker_id == ticker_id,
                IntradayPrice.timestamp < cutoff,
            )
        )
        .order_by(IntradayPrice.timestamp.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row.date() if row else None


async def insert_intraday_price(
    session: AsyncSession,
    ticker_id: int,
    timestamp: datetime,
    price: float,
    volume: Optional[int] = None,
) -> IntradayPrice:
    row = IntradayPrice(
        ticker_id=ticker_id,
        timestamp=timestamp,
        price=price,
        volume=volume,
    )
    session.add(row)
    await session.flush()
    return row


async def delete_intraday_before(
    session: AsyncSession, ticker_id: int, before_dt: datetime
) -> int:
    """Delete intraday rows older than `before_dt`. Returns deleted row count."""
    result = await session.execute(
        delete(IntradayPrice).where(
            and_(
                IntradayPrice.ticker_id == ticker_id,
                IntradayPrice.timestamp < before_dt,
            )
        )
    )
    return result.rowcount
