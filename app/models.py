"""
SQLAlchemy ORM models for Ticker Tracker.
"""
from datetime import datetime, date
from sqlalchemy import (
    Integer, String, Boolean, Float, Date, DateTime,
    ForeignKey, UniqueConstraint, BigInteger, JSON
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.sources import DEFAULT_DATA_SOURCE


class Base(DeclarativeBase):
    pass


class Ticker(Base):
    __tablename__ = "tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_source: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_DATA_SOURCE.value, nullable=False
    )
    source_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    daily_prices: Mapped[list["DailyPrice"]] = relationship(
        "DailyPrice", back_populates="ticker", cascade="all, delete-orphan"
    )
    intraday_prices: Mapped[list["IntradayPrice"]] = relationship(
        "IntradayPrice", back_populates="ticker", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Ticker(symbol={self.symbol!r}, enabled={self.enabled})>"


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("ticker_id", "date", name="uq_daily_ticker_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    ticker: Mapped["Ticker"] = relationship("Ticker", back_populates="daily_prices")

    def __repr__(self) -> str:
        return f"<DailyPrice(ticker_id={self.ticker_id}, date={self.date}, close={self.close})>"


class IntradayPrice(Base):
    __tablename__ = "intraday_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    ticker: Mapped["Ticker"] = relationship("Ticker", back_populates="intraday_prices")

    def __repr__(self) -> str:
        return f"<IntradayPrice(ticker_id={self.ticker_id}, timestamp={self.timestamp}, price={self.price})>"
