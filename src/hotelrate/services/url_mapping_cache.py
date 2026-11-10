from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hotelrate.schemas import Platform


@dataclass(slots=True)
class UrlMappingRecord:
    platform: Platform
    hotel_name: str
    city: str
    url: str
    structured_offer_count: int
    offer_count: int
    diagnosis: str


class UrlMappingCache:
    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.logger = logging.getLogger(self.__class__.__name__)
        self._cache: dict[str, dict[str, Any]] | None = None

    def get(self, *, platform: Platform, hotel_name: str, city: str) -> str | None:
        data = self._load()
        key = self._key(platform=platform, hotel_name=hotel_name, city=city)
        item = data.get(key)
        if not isinstance(item, dict):
            return None
        value = item.get("url")
        if isinstance(value, str) and value.startswith("http"):
            return value
        return None

    def set(self, record: UrlMappingRecord) -> None:
        if not record.url.startswith("http"):
            return
        data = self._load()
        key = self._key(platform=record.platform, hotel_name=record.hotel_name, city=record.city)
        previous = data.get(key, {})
        prev_structured = int(previous.get("structured_offer_count", 0)) if isinstance(previous, dict) else 0
        prev_offer_count = int(previous.get("offer_count", 0)) if isinstance(previous, dict) else 0

        better = (
            record.structured_offer_count > prev_structured
            or (
                record.structured_offer_count == prev_structured
                and record.offer_count >= prev_offer_count
            )
        )
        if not better and previous:
            return

        data[key] = {
            "platform": record.platform.value,
            "hotel_name": record.hotel_name,
            "city": record.city,
            "url": record.url,
            "structured_offer_count": record.structured_offer_count,
            "offer_count": record.offer_count,
            "diagnosis": record.diagnosis,
        }
        self._save(data)

    def _key(self, *, platform: Platform, hotel_name: str, city: str) -> str:
        hotel = self._normalize_text(hotel_name)
        city_text = self._normalize_text(city)
        return f"{platform.value}|{hotel}|{city_text}"

    def _normalize_text(self, text: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return "-".join(tokens[:14]) if tokens else "na"

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache

        if not self.path.exists():
            self._cache = {}
            return self._cache

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("failed to load url mapping cache: %s", exc)
            self._cache = {}
            return self._cache

        if not isinstance(raw, dict):
            self._cache = {}
            return self._cache

        cleaned: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            cleaned[key] = value
        self._cache = cleaned
        return self._cache

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
