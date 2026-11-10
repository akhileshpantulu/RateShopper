from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from hotelrate.apps.worker.service import RateCrawlerService
from hotelrate.config import get_settings
from hotelrate.logging import configure_logging


async def run_worker() -> None:
    configure_logging()
    settings = get_settings()
    crawler = RateCrawlerService(settings)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        crawler.crawl_once,
        trigger="interval",
        minutes=settings.crawl_interval_minutes,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    await crawler.crawl_once()

    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    await stop_event.wait()
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(run_worker())
