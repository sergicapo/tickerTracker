"""
Pydantic v2 schemas for request/response validation.
"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Ticker schemas
# ---------------------------------------------------------------------------

class TickerBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32, examples=["AAPL"])
    name: Optional[str] = Field(None, max_length=256, examples=["Apple Inc."])


class TickerCreate(TickerBase):
    pass


class TickerUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    enabled: Optional[bool] = None


class TickerResponse(TickerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    enabled: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Daily price schemas
# ---------------------------------------------------------------------------

class DailyPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker_id: int
    date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[int]


# ---------------------------------------------------------------------------
# Intraday price schemas
# ---------------------------------------------------------------------------

class IntradayPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker_id: int
    timestamp: datetime
    price: float
    volume: Optional[int]


class CurrentPriceResponse(BaseModel):
    ticker_id: int
    timestamp: datetime
    price: float
    volume: Optional[int]
    previous_close: Optional[float]
    previous_close_date: Optional[date]


# ---------------------------------------------------------------------------
# Health schema
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
