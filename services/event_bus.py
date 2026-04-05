"""
event_bus.py
────────────
Tiny in-process pub/sub used by the streaming extraction pipeline.

The extraction coordinator publishes events (document indexed, parameter
updated, pass complete, done) keyed by project_id. HTTP SSE handlers in
`routers/parameters.py` register asyncio queues as listeners and stream
events out to the browser.

Design notes
------------
• Single-process only. If/when the backend runs as multiple workers,
  this needs to move to Redis pub/sub — but today the pipeline runs in
  the same uvicorn process as the HTTP handlers so an asyncio queue is
  exactly right.
• Listeners use bounded queues so a slow/zombie client can't OOM the
  server. Overflow is dropped silently (the client will recover on its
  next snapshot fetch).
• Events are plain dicts — JSON-serializable by construction. Keys are:
    { "type": str, "payload": dict, "at": iso-timestamp }
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Max events a slow listener can buffer before we start dropping.
_LISTENER_QUEUE_SIZE = 200

# project_id (str) -> list of asyncio.Queue
_listeners: dict[str, list[asyncio.Queue]] = defaultdict(list)


def _key(project_id) -> str:
    return str(project_id)


def register_listener(project_id) -> asyncio.Queue:
    """Register a new SSE listener for a project. Returns its event queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=_LISTENER_QUEUE_SIZE)
    _listeners[_key(project_id)].append(q)
    logger.debug(f"[EVENT_BUS] +listener project={project_id} "
                 f"(now {len(_listeners[_key(project_id)])})")
    return q


def unregister_listener(project_id, queue: asyncio.Queue) -> None:
    """Remove a listener — safe to call multiple times."""
    key = _key(project_id)
    if key in _listeners:
        try:
            _listeners[key].remove(queue)
        except ValueError:
            pass
        if not _listeners[key]:
            _listeners.pop(key, None)
    logger.debug(f"[EVENT_BUS] -listener project={project_id}")


async def publish(project_id, event_type: str, payload: dict[str, Any]) -> None:
    """Broadcast an event to all listeners for a project.

    Listeners with a full queue get dropped (the client will reconcile on its
    next snapshot fetch anyway, so silent drop is acceptable).
    """
    key = _key(project_id)
    listeners = _listeners.get(key)
    if not listeners:
        return
    event = {
        "type": event_type,
        "payload": payload,
        "at": datetime.utcnow().isoformat() + "Z",
    }
    dead: list[asyncio.Queue] = []
    for q in list(listeners):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"[EVENT_BUS] Listener queue full — dropping event {event_type}")
            dead.append(q)
    for q in dead:
        try:
            listeners.remove(q)
        except ValueError:
            pass


def has_listeners(project_id) -> bool:
    return bool(_listeners.get(_key(project_id)))


def snapshot_listener_counts() -> dict[str, int]:
    """Return `{project_id: listener_count}` for health endpoints."""
    return {k: len(v) for k, v in _listeners.items() if v}
