from __future__ import annotations

import hashlib
from decimal import Decimal
from uuid import UUID

from hotelrate.schemas import NormalizedSnapshot, RawRateOffer


def normalize_offer(mapping_id: UUID, raw: RawRateOffer) -> NormalizedSnapshot:
    base_price = raw.base_price.quantize(Decimal("0.01"))
    tax_fee = raw.tax_fee.quantize(Decimal("0.01"))
    final_price = raw.final_price.quantize(Decimal("0.01"))

    if final_price <= Decimal("0"):
        final_price = (base_price + tax_fee).quantize(Decimal("0.01"))

    rate_hash = _build_rate_hash(raw)
    return NormalizedSnapshot(
        mapping_id=mapping_id,
        platform=raw.platform,
        room_name=raw.room_name,
        meal_plan=raw.meal_plan,
        cancellation_policy=raw.cancellation_policy,
        currency=raw.currency.upper(),
        base_price=base_price,
        tax_fee=tax_fee,
        final_price=final_price,
        checkin=raw.checkin,
        checkout=raw.checkout,
        adults=raw.adults,
        children=raw.children,
        source_url=raw.source_url,
        raw_payload=raw.raw_payload,
        scraped_at=raw.collected_at,
        rate_hash=rate_hash,
    )


def _build_rate_hash(raw: RawRateOffer) -> str:
    source = "|".join(
        [
            raw.platform.value,
            raw.platform_hotel_id,
            raw.room_name,
            raw.meal_plan or "",
            raw.cancellation_policy or "",
            raw.checkin.isoformat(),
            raw.checkout.isoformat(),
            str(raw.adults),
            str(raw.children),
            str(raw.final_price),
            raw.currency,
        ]
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()
