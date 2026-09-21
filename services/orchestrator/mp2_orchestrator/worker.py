"""MP2 worker process.

One process hosts one or more task queues. CPU, GPU, IO and semantic work are separate
queues so they can be scaled onto separate hosts without changing the workflow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import ALL_ACTIVITIES
from .workflow import AnalyzeMediaWorkflow

logging.basicConfig(
    level=os.getenv("MP2_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("mp2.worker")

# The workflow is hosted on the IO queue; every queue hosts every activity so that a
# single-worker development profile behaves like a multi-worker deployment.
WORKFLOW_QUEUE = "mp2-io"


async def main() -> None:
    address = os.getenv("MP2_TEMPORAL_ADDRESS", "temporal:7233")
    queues = [q.strip() for q in os.getenv(
        "MP2_TASK_QUEUES", "mp2-io,mp2-cpu,mp2-semantic,mp2-review").split(",") if q.strip()]

    log.info("connecting to temporal at %s", address)
    client = await Client.connect(address)
    log.info("hosting task queues: %s", ", ".join(queues))

    # MP2 activities are synchronous: they call blocking libraries (SQLAlchemy, OpenCV,
    # librosa, CTranslate2, ffmpeg). Temporal runs sync activities on this executor, which
    # keeps extraction off the event loop so one long activity cannot stall the others.
    # Sized against the WSL CPU allocation rather than the host's thread count.
    max_workers = int(os.getenv("MP2_ACTIVITY_THREADS", "4"))
    executor = ThreadPoolExecutor(max_workers=max_workers,
                                  thread_name_prefix="mp2-activity")
    log.info("activity executor: %d thread(s)", max_workers)

    workers = [
        Worker(
            client,
            task_queue=queue,
            workflows=[AnalyzeMediaWorkflow] if queue == WORKFLOW_QUEUE else [],
            activities=ALL_ACTIVITIES,
            activity_executor=executor,
            # Keep a constrained host from accepting more concurrent work than it can run.
            max_concurrent_activities=max_workers,
        )
        for queue in queues
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - platform dependent
            pass

    async with asyncio.TaskGroup() as group:
        for worker in workers:
            group.create_task(worker.run())
        group.create_task(_wait_then_shutdown(stop, workers))


async def _wait_then_shutdown(stop: asyncio.Event, workers: list[Worker]) -> None:
    await stop.wait()
    log.info("shutdown signal received; draining %d worker(s)", len(workers))
    for worker in workers:
        await worker.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover
        log.info("interrupted")
