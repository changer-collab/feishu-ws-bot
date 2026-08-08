import asyncio
import json
import socket

import pytest
import websockets

from feishu_bot.onebot.onebot_client import OneBotServer


def _free_port() -> int:
    """探测一个空闲端口，避免固定端口冲突导致的 flaky。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_event_dispatch_and_api_call():
    server = OneBotServer("127.0.0.1", _free_port())
    received = []

    async def on_message(event):
        received.append(event)

    server.on("message", on_message)
    task = asyncio.create_task(server.serve_forever())
    # 等待服务端真正开始监听（最多重试 20 次）
    for _ in range(20):
        try:
            async with websockets.connect(f"ws://127.0.0.1:{server.port}") as ws:
                # 模拟 NapCat 上报事件
                await ws.send(json.dumps({"post_type": "message", "group_id": 1}))
                await asyncio.sleep(0.2)

                async def request_api():
                    return await server.call("get_login_info")

                api_task = asyncio.create_task(request_api())
                await asyncio.sleep(0.2)
                # 回复带 echo 的 API 响应
                await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                          "data": {"user_id": 9}, "echo": "1"}))
                result = await asyncio.wait_for(api_task, 3)
                assert result["data"]["user_id"] == 9
            break
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.1)
    else:
        raise AssertionError("OneBotServer 未能启动监听")

    task.cancel()  # serve_forever 常驻，断言完成后取消
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    assert received == [{"post_type": "message", "group_id": 1}]


@pytest.mark.asyncio
async def test_event_handler_can_call_api_without_blocking_receive_loop():
    """An event handler's API call must not prevent its echo from being read."""
    server = OneBotServer("127.0.0.1", _free_port())
    completed = asyncio.Event()
    result = {}

    async def on_notice(event):
        result.update(await server.call("get_group_file_url", {"file_id": "f1"}))
        completed.set()

    server.on("notice", on_notice)
    task = asyncio.create_task(server.serve_forever())
    try:
        for _ in range(20):
            try:
                async with websockets.connect(f"ws://127.0.0.1:{server.port}") as ws:
                    await ws.send(json.dumps({"post_type": "notice", "notice_type": "group_upload"}))
                    request = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    assert request["action"] == "get_group_file_url"
                    await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                              "data": {"url": "http://example.test/a.pdf"},
                                              "echo": request["echo"]}))
                    await asyncio.wait_for(completed.wait(), 3)
                    break
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.1)
        else:
            raise AssertionError("OneBotServer 未能启动监听")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert result["data"]["url"] == "http://example.test/a.pdf"
