from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://hotelrate:hotelrate@localhost:5432/hotelrate"

    crawl_interval_minutes: int = Field(default=10, ge=1, le=120)
    crawl_concurrency: int = Field(default=4, ge=1, le=20)
    request_timeout_seconds: int = Field(default=25, ge=5, le=120)
    google_request_timeout_seconds: int = Field(default=90, ge=5, le=180)
    crawl_insert_batch_size: int = Field(default=200, ge=20, le=2000)
    platform_max_concurrency: int = Field(default=2, ge=1, le=8)
    platform_request_stagger_ms: int = Field(default=250, ge=0, le=3000)
    batch_hotel_concurrency: int = Field(default=3, ge=1, le=10)
    batch_task_concurrency: int = Field(default=6, ge=1, le=30)

    headless: bool = True
    demo_mode: bool = True
    playwright_user_data_dir: str = ""
    playwright_user_data_dir_trip: str = ""
    playwright_user_data_dir_booking: str = ""
    playwright_user_data_dir_agoda: str = ""
    playwright_user_data_dir_google_hotels: str = ""
    playwright_browser_channel: str = "chromium"
    playwright_disable_automation_flags: bool = True

    default_checkin_offset_days: int = Field(default=7, ge=0, le=365)
    default_length_of_stay: int = Field(default=1, ge=1, le=30)
    default_adults: int = Field(default=2, ge=1, le=8)
    default_children: int = Field(default=0, ge=0, le=6)
    default_locale: str = Field(default="en-US", min_length=2, max_length=35)
    default_market: str = Field(
        default="",
        max_length=2,
        description="Optional ISO 3166-1 alpha-2; empty = infer from default_locale for Google gl.",
    )

    target_city: str = ""
    firecrawl_base_url: str = "http://localhost:3002"
    firecrawl_timeout_seconds: int = Field(default=20, ge=3, le=120)
    firecrawl_enabled: bool = True
    firecrawl_api_key: str = ""
    firecrawl_fallback_for_live_quote: bool = True
    # Skip Google Hotels if Playwright keeps failing (Google often blocks automation)
    google_skip: bool = True
    url_mapping_cache_path: str = "reports/url_mapping_cache.json"
    # Save page HTML to reports/ when Google only returns fallback results (for debugging selectors)
    google_debug_save_html: bool = False
    # Save Google runtime HTML + probe JSON (large files, enable only for troubleshooting)
    google_debug_save_runtime_artifacts: bool = False
    booking_debug_save_json: bool = False
    agoda_debug_save_json: bool = False
    trip_debug_save_json: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
