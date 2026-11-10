from __future__ import annotations

from datetime import date

import pytest

from hotelrate.core.market_locale import (
    accept_language_header,
    agoda_should_prepend_locale_prefix,
    google_hl_gl,
    normalize_bcp47_locale,
)
from hotelrate.schemas import SearchRequest


def test_normalize_bcp47_locale() -> None:
    assert normalize_bcp47_locale("zh_CN") == "zh-CN"
    assert normalize_bcp47_locale("  en-us  ") == "en-US"
    assert normalize_bcp47_locale("") == "en-US"


def test_accept_language_header() -> None:
    h = accept_language_header("ja-JP")
    assert "ja-JP" in h
    assert "ja;" in h or "ja;q" in h


@pytest.mark.parametrize(
    ("locale", "market", "expected_gl"),
    [
        ("en-US", None, "us"),
        ("zh-CN", None, "cn"),
        ("ja-JP", "US", "us"),
        ("en-GB", None, "gb"),
        ("en-GB", "UK", "gb"),
    ],
)
def test_google_hl_gl(locale: str, market: str | None, expected_gl: str) -> None:
    hl, gl = google_hl_gl(locale, market)
    assert gl == expected_gl
    assert hl == normalize_bcp47_locale(locale)


def test_agoda_locale_prefix_detection() -> None:
    assert agoda_should_prepend_locale_prefix("singapore/some-hotel/hotel.html")
    assert not agoda_should_prepend_locale_prefix("zh-cn/singapore/some-hotel/hotel.html")


def test_search_request_market_optional() -> None:
    r = SearchRequest(
        platform_hotel_id="x",
        checkin=date(2026, 1, 1),
        checkout=date(2026, 1, 2),
        market=None,
    )
    assert r.market is None
    r2 = SearchRequest.model_validate(
        {
            "platform_hotel_id": "x",
            "checkin": "2026-01-01",
            "checkout": "2026-01-02",
            "market": "",
        }
    )
    assert r2.market is None
