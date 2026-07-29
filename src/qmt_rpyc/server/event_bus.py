import threading
import uuid
import logging
from collections import deque

logger = logging.getLogger(__name__)

_MAX_EVENTS_PER_SUB = 1000


class Subscription:
    def __init__(self, sub_id, event_types, account_id=None, max_events=_MAX_EVENTS_PER_SUB):
        self.sub_id = sub_id
        self.event_types = set(event_types)
        self.account_id = account_id
        self._queue = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._dropped = 0

    def push(self, event):
        if event["type"] not in self.event_types:
            return
        if self.account_id and event.get("account_id") != self.account_id:
            return
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self._dropped += 1
            self._queue.append(event)

    def drain(self, max_count=100):
        with self._lock:
            events = []
            while len(events) < max_count and self._queue:
                events.append(self._queue.popleft())
            dropped = self._dropped
            self._dropped = 0
        return events, dropped


class EventBus:
    def __init__(self):
        self._subs = {}
        self._lock = threading.Lock()

    def subscribe(self, event_types, account_id=None):
        sub_id = str(uuid.uuid4())[:8]
        sub = Subscription(sub_id, event_types, account_id)
        with self._lock:
            self._subs[sub_id] = sub
        return sub_id

    def unsubscribe(self, sub_id):
        with self._lock:
            return self._subs.pop(sub_id, None) is not None

    def get_subscription(self, sub_id):
        with self._lock:
            return self._subs.get(sub_id)

    def publish(self, event):
        with self._lock:
            subs = list(self._subs.values())
        for sub in subs:
            sub.push(event)


event_bus = EventBus()
