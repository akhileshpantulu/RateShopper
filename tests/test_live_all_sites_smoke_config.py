from __future__ import annotations

import json
from pathlib import Path

from hotelrate.schemas import BatchQuoteRequest, Platform


def test_live_all_sites_smoke_config_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "live_all_sites_smoke.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    req = BatchQuoteRequest.model_validate(payload)

    assert req.concurrency == 2
    assert len(req.hotels) == 4

    platforms = [hotel.platforms[0] for hotel in req.hotels if hotel.platforms]
    assert platforms == [
        Platform.TRIP,
        Platform.BOOKING,
        Platform.AGODA,
        Platform.GOOGLE_HOTELS,
    ]
