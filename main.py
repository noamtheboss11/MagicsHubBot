from __future__ import annotations

import asyncio
import logging

import aiohttp

from dotenv import load_dotenv

from sales_bot.bot import SalesBot
from sales_bot.config import Settings
from sales_bot.logging_config import configure_logging


LOGGER = logging.getLogger(__name__)


async def self_ping_loop(settings: Settings) -> None:
    if not settings.self_ping_enabled:
        return

    target_url = f"{settings.public_base_url}/health"
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            await asyncio.sleep(settings.self_ping_interval_seconds)
            try:
                async with session.get(target_url) as response:
                    if response.status >= 400:
                        LOGGER.warning("Self-ping returned HTTP %s from %s", response.status, target_url)
                    else:
                        LOGGER.debug("Self-ping succeeded for %s", target_url)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Self-ping failed for %s", target_url)


async def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    ping_task = asyncio.create_task(self_ping_loop(settings), name="self-ping-loop")
    try:
        async with SalesBot(settings) as bot:
            await bot.start(settings.discord_token)
    finally:
        ping_task.cancel()
        await asyncio.gather(ping_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
