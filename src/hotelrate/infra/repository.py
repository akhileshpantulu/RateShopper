from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hotelrate.infra.models import CrawlJobLog, HotelMapping, HotelMaster, RateSnapshot
from hotelrate.schemas import NormalizedSnapshot, Platform


def _ensure_date(v: date | str) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


class RateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_hotel_mappings(
        self,
        *,
        platform: Platform | None = None,
        city: str | None = None,
        limit: int | None = None,
    ) -> list[HotelMapping]:
        stmt: Select[tuple[HotelMapping]] = select(HotelMapping)

        if city:
            stmt = stmt.join(HotelMaster, HotelMaster.id == HotelMapping.hotel_id).where(HotelMaster.city == city)
        if platform:
            stmt = stmt.where(HotelMapping.platform == platform)
        if limit:
            stmt = stmt.limit(limit)

        rows = await self.session.execute(stmt)
        return list(rows.scalars().all())

    async def insert_snapshots(self, snapshots: list[NormalizedSnapshot]) -> int:
        if not snapshots:
            return 0

        rows = [
            RateSnapshot(
                mapping_id=item.mapping_id,
                platform=item.platform,
                room_name=item.room_name,
                meal_plan=item.meal_plan,
                cancellation_policy=item.cancellation_policy,
                currency=item.currency,
                base_price=item.base_price,
                tax_fee=item.tax_fee,
                final_price=item.final_price,
                checkin=item.checkin,
                checkout=item.checkout,
                adults=item.adults,
                children=item.children,
                source_url=item.source_url,
                raw_payload=item.raw_payload,
                scraped_at=item.scraped_at,
                rate_hash=item.rate_hash,
            )
            for item in snapshots
        ]
        self.session.add_all(rows)
        await self.session.commit()
        return len(rows)

    async def create_job_log(self, platform: Platform, target_count: int) -> CrawlJobLog:
        job = CrawlJobLog(
            platform=platform,
            target_count=target_count,
            success_count=0,
            failed_count=0,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def finish_job_log(
        self,
        *,
        job: CrawlJobLog,
        success_count: int,
        failed_count: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        ended_at = datetime.now(timezone.utc)
        duration = int((ended_at - job.started_at).total_seconds() * 1000)

        job.success_count = success_count
        job.failed_count = failed_count
        job.status = status
        job.error_message = error_message
        job.ended_at = ended_at
        job.duration_ms = duration

        await self.session.commit()

    async def fetch_latest_rates(
        self,
        *,
        city: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query = text(
            """
            WITH ranked AS (
                SELECT
                    rs.id,
                    rs.mapping_id,
                    rs.platform,
                    rs.room_name,
                    rs.meal_plan,
                    rs.cancellation_policy,
                    rs.currency,
                    rs.base_price,
                    rs.tax_fee,
                    rs.final_price,
                    rs.checkin,
                    rs.checkout,
                    rs.adults,
                    rs.children,
                    rs.scraped_at,
                    rs.source_url,
                    hm.id AS hotel_id,
                    hm.name AS hotel_name,
                    hm.city,
                    hm.country,
                    hm.latitude,
                    hm.longitude,
                    hm.id::text || '-' || rs.platform::text || '-' || rs.room_name || '-' || rs.checkin::text || '-' || rs.checkout::text AS rate_key,
                    ROW_NUMBER() OVER (
                        PARTITION BY rs.mapping_id, rs.room_name, rs.checkin, rs.checkout, rs.adults, rs.children
                        ORDER BY rs.scraped_at DESC
                    ) AS rn
                FROM rate_snapshot rs
                JOIN hotel_mapping mp ON mp.id = rs.mapping_id
                JOIN hotel_master hm ON hm.id = mp.hotel_id
                WHERE hm.city = :city
                  AND rs.checkin = :checkin
                  AND rs.checkout = :checkout
                  AND rs.adults = :adults
                  AND rs.children = :children
            )
            SELECT *
            FROM ranked
            WHERE rn = 1
            ORDER BY final_price ASC, scraped_at DESC
            LIMIT :limit
            """
        )

        rows = await self.session.execute(
            query,
            {
                "city": city,
                "checkin": _ensure_date(checkin),
                "checkout": _ensure_date(checkout),
                "adults": adults,
                "children": children,
                "limit": limit,
            },
        )
        return [dict(row) for row in rows.mappings().all()]

    async def fetch_best_rates(
        self,
        *,
        city: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = text(
            """
            WITH latest AS (
                SELECT
                    rs.*,
                    hm.id AS hotel_id,
                    hm.name AS hotel_name,
                    hm.city,
                    hm.country,
                    ROW_NUMBER() OVER (
                        PARTITION BY rs.mapping_id, rs.room_name, rs.checkin, rs.checkout, rs.adults, rs.children
                        ORDER BY rs.scraped_at DESC
                    ) AS rn_latest
                FROM rate_snapshot rs
                JOIN hotel_mapping mp ON mp.id = rs.mapping_id
                JOIN hotel_master hm ON hm.id = mp.hotel_id
                WHERE hm.city = :city
                  AND rs.checkin = :checkin
                  AND rs.checkout = :checkout
                  AND rs.adults = :adults
                  AND rs.children = :children
            ), ranked AS (
                SELECT
                    latest.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY latest.hotel_id
                        ORDER BY latest.final_price ASC, latest.scraped_at DESC
                    ) AS rn_best
                FROM latest
                WHERE latest.rn_latest = 1
            )
            SELECT *
            FROM ranked
            WHERE rn_best = 1
            ORDER BY final_price ASC
            LIMIT :limit
            """
        )

        rows = await self.session.execute(
            query,
            {
                "city": city,
                "checkin": _ensure_date(checkin),
                "checkout": _ensure_date(checkout),
                "adults": adults,
                "children": children,
                "limit": limit,
            },
        )
        return [dict(row) for row in rows.mappings().all()]

    async def fetch_rate_history(
        self,
        *,
        hotel_id: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        platform: Platform | None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if platform is None:
            query = text(
                """
                SELECT
                    rs.platform,
                    rs.room_name,
                    rs.currency,
                    rs.final_price,
                    rs.base_price,
                    rs.tax_fee,
                    rs.scraped_at,
                    rs.source_url
                FROM rate_snapshot rs
                JOIN hotel_mapping mp ON mp.id = rs.mapping_id
                WHERE mp.hotel_id = :hotel_id
                  AND rs.checkin = :checkin
                  AND rs.checkout = :checkout
                  AND rs.adults = :adults
                  AND rs.children = :children
                ORDER BY rs.scraped_at DESC
                LIMIT :limit
                """
            )
            params: dict[str, Any] = {
                "hotel_id": hotel_id,
                "checkin": _ensure_date(checkin),
                "checkout": _ensure_date(checkout),
                "adults": adults,
                "children": children,
                "limit": limit,
            }
        else:
            query = text(
                """
                SELECT
                    rs.platform,
                    rs.room_name,
                    rs.currency,
                    rs.final_price,
                    rs.base_price,
                    rs.tax_fee,
                    rs.scraped_at,
                    rs.source_url
                FROM rate_snapshot rs
                JOIN hotel_mapping mp ON mp.id = rs.mapping_id
                WHERE mp.hotel_id = :hotel_id
                  AND rs.platform = :platform
                  AND rs.checkin = :checkin
                  AND rs.checkout = :checkout
                  AND rs.adults = :adults
                  AND rs.children = :children
                ORDER BY rs.scraped_at DESC
                LIMIT :limit
                """
            )
            params = {
                "hotel_id": hotel_id,
                "platform": platform.value,
                "checkin": _ensure_date(checkin),
                "checkout": _ensure_date(checkout),
                "adults": adults,
                "children": children,
                "limit": limit,
            }

        rows = await self.session.execute(query, params)
        return [dict(row) for row in rows.mappings().all()]
