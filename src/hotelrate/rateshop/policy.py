"""The shared collection rules every collector call goes through (docs/DESIGN.md, section 4).

- At most `max_in_flight` requests at once per process.
- A random pause between the starts of consecutive requests.
- At most `max_retries` retries per request, with growing, jittered back-off.
- On a block signal the whole process pauses before its next request, not only the call that was blocked.

No proxy, user-agent rotation or CAPTCHA handling lives here, by design.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from hotelrate.config import Settings

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class PolicyConfig:
    max_in_flight: int = 2
    pause_min_seconds: float = 3.0
    pause_max_seconds: float = 8.0
    max_retries: int = 3
    retry_base_seconds: float = 30.0
    block_pause_seconds: float = 300.0

    @classmethod
    def from_settings(cls, settings: Settings) -> PolicyConfig:
        return cls(
            max_in_flight=settings.rateshop_max_in_flight,
            pause_min_seconds=settings.rateshop_pause_min_seconds,
            pause_max_seconds=settings.rateshop_pause_max_seconds,
            max_retries=settings.rateshop_max_retries,
            retry_base_seconds=settings.rateshop_retry_base_seconds,
            block_pause_seconds=settings.rateshop_block_pause_seconds,
        )


class RequestPolicy:
    def __init__(
        self,
        config: PolicyConfig,
        *,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._slots = asyncio.Semaphore(config.max_in_flight)
        self._gate = asyncio.Lock()
        self._next_start = 0.0
        self._paused_until = 0.0
        self.requests_started = 0
        self.blocks_seen = 0

    @property
    def max_retries(self) -> int:
        return self.config.max_retries

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one of the in-flight slots, starting only once the pause and any block pause have passed."""
        async with self._slots:
            async with self._gate:
                wait = max(self._next_start, self._paused_until) - self._clock()
                if wait > 0:
                    await self._sleep(wait)
                self.requests_started += 1
                pause = self._rng.uniform(self.config.pause_min_seconds, self.config.pause_max_seconds)
                self._next_start = self._clock() + pause
            yield

    def note_block(self) -> None:
        self.blocks_seen += 1
        self._paused_until = max(self._paused_until, self._clock() + self.config.block_pause_seconds)

    def retry_delay(self, retry_number: int) -> float:
        """Delay before retry 1, 2, 3...: base, 3 x base, 9 x base, each +/- 20%."""
        return self.config.retry_base_seconds * 3 ** (retry_number - 1) * self._rng.uniform(0.8, 1.2)

    async def wait(self, seconds: float) -> None:
        if seconds > 0:
            await self._sleep(seconds)
