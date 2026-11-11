"""Message queue interface. Locally an in-process queue; SQS in AWS (planned)."""

import asyncio
from typing import Protocol


class MessageQueue(Protocol):
    async def publish(self, message: dict) -> None:
        """Add a message. In AWS, the IoT rule does this, not our code."""

    async def receive(self) -> dict:
        """Wait for the next message."""

    def ack(self, message: dict) -> None:
        """Mark a message as handled. With SQS this deletes it; unacked messages
        come back and, after repeated failures, go to the dead-letter queue."""


class InProcessQueue:
    """Queue shared by the simulator and worker running in one process."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    async def publish(self, message: dict) -> None:
        await self._queue.put(message)

    async def receive(self) -> dict:
        return await self._queue.get()

    def ack(self, message: dict) -> None:
        self._queue.task_done()

    async def drain(self) -> None:
        """Wait until every published message has been acked."""
        await self._queue.join()
