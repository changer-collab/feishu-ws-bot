"""OneBot v11 反向 WebSocket 服务端。

NapCat 作为客户端主动连接本服务；事件直接推送，API 调用带 echo 关联响应。
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from websockets.server import serve

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]


class OneBotServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8081):
        self.host = host
        self.port = port
        self._server = None
        self._connections: set = set()
        self._handlers: dict[str, list[EventCallback]] = {}
        self._echo_counter = 0
        self._pending: dict[str, asyncio.Future] = {}
        # Event handlers may make OneBot API calls themselves.  Keep them out
        # of the receive loop, otherwise an API call waits for an echo that the
        # blocked receive loop can no longer read.
        self._handler_tasks: set[asyncio.Task] = set()
        self._connected = asyncio.Event()

    def on(self, post_type: str, handler: EventCallback) -> None:
        self._handlers.setdefault(post_type, []).append(handler)

    async def wait_connected(self) -> None:
        await self._connected.wait()

    async def call(self, action: str, params: Optional[dict] = None,
                   timeout: float = 30.0) -> dict:
        if not self._connections:
            raise RuntimeError("OneBot 尚未连接")
        self._echo_counter += 1
        echo = str(self._echo_counter)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            for conn in list(self._connections):
                await conn.send(json.dumps(payload, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(echo, None)

    def _dispatch_event(self, handler: EventCallback, event: dict,
                        post_type: str) -> None:
        """Run a handler independently so this WebSocket can keep reading."""
        task = asyncio.create_task(
            self._run_handler(handler, event, post_type),
            name=f"onebot-{post_type}-handler",
        )
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _run_handler(self, handler: EventCallback, event: dict,
                           post_type: str) -> None:
        try:
            await handler(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("OneBot 事件处理失败: %s", post_type)

    async def _handle(self, ws) -> None:
        self._connections.add(ws)
        self._connected.set()
        logger.info("OneBot 已连接: %s", ws.remote_address)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "echo" in msg:
                    echo = str(msg.get("echo"))
                    fut = self._pending.get(echo)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    continue
                # 诊断日志：打印收到的消息事件，便于排查事件是否到达
                if msg.get("post_type") == "message":
                    logger.info(
                        "收到消息事件: message_type=%s group_id=%s segments=%s",
                        msg.get("message_type"),
                        msg.get("group_id"),
                        [s.get("type") for s in (msg.get("message") or [])
                         if isinstance(s, dict)],
                    )
                for handler in self._handlers.get(str(msg.get("post_type") or ""), []):
                    self._dispatch_event(handler, msg, str(msg.get("post_type") or ""))
        finally:
            self._connections.discard(ws)
            logger.info("OneBot 连接断开: %s", ws.remote_address)

    async def serve_forever(self) -> None:
        async with serve(self._handle, self.host, self.port) as server:
            self._server = server
            logger.info("反向 WS 服务端监听 %s:%s，等待 NapCat 连接...",
                        self.host, self.port)
            await asyncio.Future()  # 阻塞运行
