from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.infra.db import SessionLocal, engine
from hotelrate.infra.models import Base, HotelMapping, HotelMaster
from hotelrate.schemas import Platform


async def init_db(seed: bool = True) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if not seed:
        return

    async with SessionLocal() as session:
        existing = await session.execute(select(HotelMaster.id).limit(1))
        if existing.first() is not None:
            print("seed skipped: hotel_master already has data")
            return

        now = datetime.now(timezone.utc)
        hotels = [
            HotelMaster(
                name="CitizenM Times Square",
                city="New York",
                country="USA",
                latitude=40.758,
                longitude=-73.9855,
                created_at=now,
            ),
            HotelMaster(
                name="Moxy Shanghai Hongqiao",
                city="Shanghai",
                country="China",
                latitude=31.201,
                longitude=121.315,
                created_at=now,
            ),
            HotelMaster(
                name="Swissotel The Stamford",
                city="Singapore",
                country="Singapore",
                latitude=1.2931,
                longitude=103.8533,
                created_at=now,
            ),
        ]
        session.add_all(hotels)
        await session.flush()

        mappings: list[HotelMapping] = []
        for idx, hotel in enumerate(hotels, start=1):
            mappings.extend(
                [
                    HotelMapping(
                        hotel_id=hotel.id,
                        platform=Platform.TRIP,
                        platform_hotel_id=f"trip-hotel-{idx}",
                        platform_hotel_name=hotel.name,
                        platform_url=f"https://www.trip.com/hotels/detail/?hotelId=trip-hotel-{idx}",
                        created_at=now,
                    ),
                    HotelMapping(
                        hotel_id=hotel.id,
                        platform=Platform.BOOKING,
                        platform_hotel_id=f"booking-hotel-{idx}",
                        platform_hotel_name=hotel.name,
                        platform_url=f"https://www.booking.com/hotel/booking-hotel-{idx}.html",
                        created_at=now,
                    ),
                    HotelMapping(
                        hotel_id=hotel.id,
                        platform=Platform.GOOGLE_HOTELS,
                        platform_hotel_id=f"google-hotel-{idx}",
                        platform_hotel_name=hotel.name,
                        platform_url=f"https://www.google.com/travel/hotels?q=google-hotel-{idx}",
                        created_at=now,
                    ),
                    HotelMapping(
                        hotel_id=hotel.id,
                        platform=Platform.AGODA,
                        platform_hotel_id=f"agoda-hotel-{idx}",
                        platform_hotel_name=hotel.name,
                        platform_url=f"https://www.agoda.com/agoda-hotel-{idx}/hotel.html",
                        created_at=now,
                    ),
                ]
            )

        session.add_all(mappings)
        await session.commit()
        print(f"seed completed: hotels={len(hotels)} mappings={len(mappings)}")


if __name__ == "__main__":
    asyncio.run(init_db(seed=True))
