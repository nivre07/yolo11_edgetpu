"""Tiny in-process pub/sub for Server-Sent Events.

Each /events connection registers a Queue; publish() fans a JSON-ready
dict out to every registered subscriber. No external broker needed for
a single-process, single-Pi appliance.
"""

import json
import queue
import threading

_lock = threading.Lock()
_subscribers: set = set()


def subscribe() -> "queue.Queue":
    q: "queue.Queue" = queue.Queue(maxsize=100)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "queue.Queue") -> None:
    with _lock:
        _subscribers.discard(q)


def publish(event: str, data: dict) -> None:
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # slow consumer — drop rather than block the producer
