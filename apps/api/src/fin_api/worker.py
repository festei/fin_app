"""Fast-loop worker (placeholder).

Phase 3 fills this with price polling, signal evaluation, sandbox execution and alert
dispatch. Exactly one instance may be active: a Postgres advisory lock is acquired in
phase 1 once the database layer exists. For now it only proves the image and the
SIGTERM-graceful-shutdown contract.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from fin_core.features import FeatureFlags

log = logging.getLogger("fin_worker")


async def run(stop: asyncio.Event, interval: float = 60.0) -> None:
    flags = FeatureFlags.load()
    log.info("worker started; features=%s", flags.as_dict())
    while not stop.is_set():
        log.info("heartbeat")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
    log.info("worker stopped cleanly")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
