"""
事件广播器：将后端事件实时推送到前端 WebSocket 连接
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class EventBroadcaster:
    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def emit(self, event_type: str, data: Any, level: str = "info"):
        msg = {
            "type": event_type,
            "level": level,
            "data": data,
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        payload = json.dumps(msg, ensure_ascii=False)
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    def log(self, message: str, level: str = "info"):
        self.emit("log", {"message": message}, level=level)


broadcaster = EventBroadcaster()
