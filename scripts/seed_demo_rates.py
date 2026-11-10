#!/usr/bin/env python3
"""插入几条 DEMO 价格快照，便于测试 /rates/search 和 /rates/best（无需 Playwright）。"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.infra.db import SessionLocal
from hotelrate.infra.repository import RateRepository
from hotelrate.schemas import NormalizedSnapshot, RawRateOffer


async def main() -> None:
    async with SessionLocal() as session:
        repo = RateRepository(session)
        mappings = await repo.list_hotel_mappings(city="Shanghai", limit=4)
        if not mappings:
            print("no Shanghai mappings, run init_db.py first")
            return

        checkin = date(2026, 3, 15)
        checkout = date(2026, 3, 16)
        now = datetime.now(timezone.utc)

        snapshots: list[NormalizedSnapshot] = []
        for m in mappings[:4]:
            price = 100 + hash(m.platform.value + m.platform_hotel_id) % 80
            from decimal import Decimal

            base = Decimal(str(price))
            tax = (base * Decimal("0.12")).quantize(Decimal("0.01"))
            final = (base + tax).quantize(Decimal("0.01"))
            raw = RawRateOffer(
                platform=m.platform,
                platform_hotel_id=m.platform_hotel_id,
                room_name="Standard Room",
                meal_plan="Room only",
                cancellation_policy="Free cancellation",
                currency="USD",
                base_price=base,
                tax_fee=tax,
                final_price=final,
                checkin=checkin,
                checkout=checkout,
                adults=2,
                children=0,
                source_url=m.platform_url,
                collected_at=now,
                raw_payload={"mode": "demo"},
            )
            snapshots.append(
                NormalizedSnapshot(
                    mapping_id=m.id,
                    platform=raw.platform,
                    room_name=raw.room_name,
                    meal_plan=raw.meal_plan,
                    cancellation_policy=raw.cancellation_policy,
                    currency=raw.currency,
                    base_price=raw.base_price,
                    tax_fee=raw.tax_fee,
                    final_price=raw.final_price,
                    checkin=raw.checkin,
                    checkout=raw.checkout,
                    adults=raw.adults,
                    children=raw.children,
                    source_url=raw.source_url,
                    raw_payload=raw.raw_payload,
                    scraped_at=raw.collected_at,
                    rate_hash=f"demo-{m.id}-{raw.final_price}",
                )
            )

        n = await repo.insert_snapshots(snapshots)
        print(f"inserted {n} demo rate snapshots for Shanghai 2026-03-15/16")


if __name__ == "__main__":
    asyncio.run(main())
