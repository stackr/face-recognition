"""Single API process, bounded ID queues, and a durable SQL journal tail."""

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EventChange


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue
    overflow: asyncio.Event = field(default_factory=asyncio.Event)


class EventBroker:
    def __init__(self, store):
        self.store = store
        self.clients = set()
        self.wake = asyncio.Event()
        self.task = None
        self.cursor = None
        self.loop = None

    def notify(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.wake.set)

    def subscribe(self):
        if len(self.clients) >= self.store.settings.event_ws_max_clients:
            raise OverflowError("Event client limit reached")
        client = Subscriber(asyncio.Queue(maxsize=self.store.settings.event_ws_queue_size))
        self.clients.add(client)
        return client

    def publish(self, event_id):
        for client in tuple(self.clients):
            if client.overflow.is_set():
                continue
            try:
                client.queue.put_nowait(event_id)
            except asyncio.QueueFull:
                client.overflow.set()

    def read_changes(self):
        with Session(self.store.engine) as db:
            if self.cursor is None:
                return self.store.state(db).revision, []
            rows = list(
                db.execute(
                    select(EventChange.id, EventChange.event_id)
                    .where(EventChange.id > self.cursor)
                    .order_by(EventChange.id)
                    .limit(100)
                )
            )
            return rows[-1][0] if rows else self.cursor, [row[1] for row in rows]

    async def run(self):
        while True:
            self.wake.clear()
            try:
                cursor, events = await asyncio.to_thread(self.read_changes)
                self.cursor = cursor
                for event_id in events:
                    self.publish(event_id)
                if len(events) == 100:
                    await asyncio.sleep(0)
                    continue
            except Exception as exc:
                logging.getLogger("cctv.events").warning(
                    "Event journal unavailable; type=%s", type(exc).__name__
                )
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    async def start(self):
        self.loop = asyncio.get_running_loop()
        try:
            self.cursor, _ = await asyncio.to_thread(self.read_changes)
        except Exception as exc:
            logging.getLogger("cctv.events").error(
                "Event startup unavailable; type=%s", type(exc).__name__
            )
            raise RuntimeError("Event storage unavailable; verify database and migration") from None
        self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.clients.clear()
