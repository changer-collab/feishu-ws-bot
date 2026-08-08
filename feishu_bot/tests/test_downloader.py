import asyncio

import pytest

from feishu_bot.onebot import downloader


class FakeServer:
    """模拟 OneBotServer.call，按 action 返回固定结果。"""

    def __init__(self, get_file=None, group_url=None):
        self._get_file = get_file or {"status": "ok", "data": {"url": "http://fake/x.pdf"}}
        self._group_url = group_url or {"status": "ok", "data": {"url": "http://fake/y.pdf"}}

    async def call(self, action, params=None, timeout=30.0):
        if action == "get_file":
            return self._get_file
        if action == "get_group_file_url":
            return self._group_url
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_download_via_url(tmp_path, monkeypatch):
    # 拦截真实 HTTP 请求（避免测试发出网络调用）
    monkeypatch.setattr(downloader, "_http_get", lambda url: b"%PDF-test")
    server = FakeServer()
    saved = await downloader.download_pdf(
        server, 123, "f1", "研报.pdf", str(tmp_path), busid=1)
    assert saved is not None
    assert saved.name.endswith(".pdf")
    assert saved.exists()
    assert saved.read_bytes() == b"%PDF-test"


@pytest.mark.asyncio
async def test_download_base64(tmp_path):
    import base64
    content = base64.b64encode(b"%PDF-test").decode()
    server = FakeServer(get_file={"status": "ok", "data": {"file": content}})
    saved = await downloader.download_pdf(
        server, 123, "f2", "a.pdf", str(tmp_path))
    assert saved is not None
    assert saved.read_bytes() == b"%PDF-test"


@pytest.mark.asyncio
async def test_download_failure_returns_none(tmp_path):
    server = FakeServer(get_file={"status": "failed", "data": {}})
    saved = await downloader.download_pdf(
        server, 123, "f3", "a.pdf", str(tmp_path), retries=0)
    assert saved is None


@pytest.mark.asyncio
async def test_download_falls_back_to_segment_url(tmp_path, monkeypatch):
    """get_file 超时/失败时，回退到消息段自带的 url 直连下载。"""
    monkeypatch.setattr(downloader, "_http_get", lambda url: b"%PDF-fallback")

    class TimeoutServer(FakeServer):
        async def call(self, action, params=None, timeout=30.0):
            if action == "get_file":
                raise asyncio.TimeoutError()
            return {"status": "ok", "data": {}}

    saved = await downloader.download_pdf(
        TimeoutServer(), 123, "f4", "a.pdf", str(tmp_path),
        url="http://fake/seg.pdf", retries=0)
    assert saved is not None
    assert saved.read_bytes() == b"%PDF-fallback"
