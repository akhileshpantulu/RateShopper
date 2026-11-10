"""Locale / market helpers for multi-language sites and Google region (gl)."""

from __future__ import annotations

import re

# ISO 3166-1 alpha-2 region subtag (BCP 47) -> Google travel `gl` parameter
_REGION_SUBTAG_TO_GL: dict[str, str] = {
    "us": "us",
    "gb": "gb",
    "uk": "gb",
    "cn": "cn",
    "tw": "tw",
    "hk": "hk",
    "mo": "mo",
    "jp": "jp",
    "kr": "kr",
    "sg": "sg",
    "au": "au",
    "nz": "nz",
    "de": "de",
    "fr": "fr",
    "es": "es",
    "it": "it",
    "nl": "nl",
    "in": "in",
    "th": "th",
    "vn": "vn",
    "id": "id",
    "my": "my",
    "ph": "ph",
    "ca": "ca",
    "mx": "mx",
    "br": "br",
}

_AGODA_LOCALE_PREFIX_RE = re.compile(r"^[a-z]{2}-[a-z]{2}/", re.IGNORECASE)


def normalize_bcp47_locale(locale: str) -> str:
    """Normalize user input to a BCP 47 style tag (e.g. zh_CN -> zh-CN)."""
    s = (locale or "").strip().replace("_", "-")
    if not s:
        return "en-US"
    parts = [p for p in s.split("-") if p]
    if not parts:
        return "en-US"
    primary = parts[0].lower()
    if len(parts) == 1:
        return primary
    # Region/script/extensions: keep region upper, rest lower (common case: lang-REGION)
    rest = [parts[1].upper() if len(parts[1]) == 2 else parts[1].lower()]
    for p in parts[2:]:
        rest.append(p.lower())
    return "-".join([primary, *rest])


def accept_language_header(locale: str) -> str:
    norm = normalize_bcp47_locale(locale)
    primary = norm.split("-", 1)[0]
    return f"{norm},{primary};q=0.9,en;q=0.8"


def google_hl_gl(locale: str, market: str | None) -> tuple[str, str]:
    """Google Travel `hl` (UI language) and `gl` (country bias)."""
    norm = normalize_bcp47_locale(locale)
    hl = norm
    gl = (market or "").strip().lower()
    if gl == "uk":
        gl = "gb"
    if not gl and "-" in norm:
        region = norm.rsplit("-", 1)[-1].lower()
        if len(region) == 2:
            gl = _REGION_SUBTAG_TO_GL.get(region, "us")
    if not gl:
        gl = "us"
    return hl, gl


def trip_locale_query_value(locale: str) -> str:
    return normalize_bcp47_locale(locale)


def booking_lang_param(locale: str) -> str:
    return normalize_bcp47_locale(locale).lower()


def agoda_should_prepend_locale_prefix(path: str) -> bool:
    p = (path or "").strip().lstrip("/")
    return not _AGODA_LOCALE_PREFIX_RE.match(p)


def agoda_locale_path_prefix(locale: str) -> str:
    return normalize_bcp47_locale(locale).lower()
