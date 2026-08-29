"""
Pydantic v2 schemas for request/response validation.
"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.sources import DataSource, DEFAULT_DATA_SOURCE


# ---------------------------------------------------------------------------
# Ticker schemas
# ---------------------------------------------------------------------------

class TickerBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32, examples=["AAPL"])
    name: Optional[str] = Field(None, max_length=256, examples=["Apple Inc."])


class TickerCreate(TickerBase):
    data_source: DataSource = Field(
        default=DEFAULT_DATA_SOURCE,
        examples=["yahoo_finance"],
        description="Data source used to fetch prices for this ticker.",
    )
    source_config: Optional[dict] = Field(
        default=None,
        examples=[{"mic": "MOTX"}],
        description=(
            "Source-specific configuration, e.g. {\"mic\": \"MOTX\"} for euronext "
            "or {\"url\": \"https://...\"} for finanzen_ch."
        ),
    )


class TickerUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    enabled: Optional[bool] = None
    data_source: Optional[DataSource] = None
    source_config: Optional[dict] = None


class TickerResponse(TickerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    enabled: bool
    data_source: DataSource
    source_config: Optional[dict]
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
