"""In-process SSE broadcaster: one asyncio queue per subscriber.

Publishing is thread safe (sync endpoints run in a thread pool). It is only ever
called after the database commit, see services/events.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

log = logging.getLogger("walnut.sse")


class Broadcaster:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subs.discard(q)

    def reset(self) -> None:
        with self._lock:
            self._subs.clear()

    def _deliver(self, msg: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Slow client: drop the oldest message. Clients also poll as a fallback.
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:  # pragma: no cover
                    log.warning("dropping SSE message for a stuck subscriber")

    def publish(self, msg: dict[str, Any]) -> None:
        """Safe to call from any thread."""
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                self._deliver(msg)
            else:
                loop.call_soon_threadsafe(self._deliver, msg)
        else:
            self._deliver(msg)


def sse_format(msg: dict[str, Any]) -> str:
    return "event: change\ndata: " + json.dumps(msg, separators=(",", ":")) + "\n\n"


broadcaster = Broadcaster()
