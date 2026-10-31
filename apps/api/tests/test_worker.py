import asyncio

from fin_api.worker import run


async def test_worker_stops_cleanly_on_event():
    stop = asyncio.Event()
    task = asyncio.create_task(run(stop, interval=0.05))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
