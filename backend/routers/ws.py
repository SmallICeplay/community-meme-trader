"""
WebSocket 日志推送接口 - 将后端事件实时推送到前端
"""
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.broadcaster import broadcaster

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    queue = broadcaster.subscribe()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(msg)
            except asyncio.TimeoutError:
                await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        broadcaster.unsubscribe(queue)
