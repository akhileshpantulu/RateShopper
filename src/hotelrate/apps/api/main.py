from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hotelrate.infra.db import get_session
from hotelrate.infra.repository import RateRepository
from hotelrate.logging import configure_logging
from hotelrate.schemas import BatchQuoteRequest, BatchQuoteResponse, LiveQuoteRequest, Platform
from hotelrate.services import LiveQuoteService

configure_logging()

app = FastAPI(
    title="Hotel Rate API",
    version="0.1.0",
    description="Multi-source hotel price snapshots and query API",
)
live_quote_service = LiveQuoteService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/rates/search")
async def search_rates(
    city: str = Query(..., min_length=1),
    checkin: date = Query(...),
    checkout: date = Query(...),
    adults: int = Query(2, ge=1, le=8),
    children: int = Query(0, ge=0, le=6),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    repo = RateRepository(session)
    items = await repo.fetch_latest_rates(
        city=city,
        checkin=checkin.isoformat(),
        checkout=checkout.isoformat(),
        adults=adults,
        children=children,
        limit=limit,
    )
    return {"count": len(items), "items": items}


@app.get("/rates/best")
async def best_rates(
    city: str = Query(..., min_length=1),
    checkin: date = Query(...),
    checkout: date = Query(...),
    adults: int = Query(2, ge=1, le=8),
    children: int = Query(0, ge=0, le=6),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    repo = RateRepository(session)
    items = await repo.fetch_best_rates(
        city=city,
        checkin=checkin.isoformat(),
        checkout=checkout.isoformat(),
        adults=adults,
        children=children,
        limit=limit,
    )
    return {"count": len(items), "items": items}


@app.get("/rates/history/{hotel_id}")
async def rate_history(
    hotel_id: UUID,
    checkin: date = Query(...),
    checkout: date = Query(...),
    adults: int = Query(2, ge=1, le=8),
    children: int = Query(0, ge=0, le=6),
    platform: Platform | None = Query(default=None),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    repo = RateRepository(session)
    items = await repo.fetch_rate_history(
        hotel_id=str(hotel_id),
        checkin=checkin.isoformat(),
        checkout=checkout.isoformat(),
        adults=adults,
        children=children,
        platform=platform,
        limit=limit,
    )
    return {"count": len(items), "items": items}


@app.post("/meta/quote")
async def meta_quote(payload: LiveQuoteRequest) -> dict:
    response = await live_quote_service.quote(payload)
    return response.model_dump()


@app.post("/meta/quote/batch")
async def meta_quote_batch(payload: BatchQuoteRequest) -> BatchQuoteResponse:
    """Batch-crawl multiple hotels concurrently."""
    response = await live_quote_service.batch_quote(payload)
    return response
