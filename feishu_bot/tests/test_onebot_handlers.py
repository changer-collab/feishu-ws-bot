import pytest

from feishu_bot.onebot.handlers import (
    QqEventHandler,
    extract_file_segment,
    extract_image_segments,
    extract_text_segments,
)


class FakeSettings:
    qq_group_id = 123
    download_dir = "downloads"
    aistock_api_url = "http://backend"
    internal_token = "token"
    classify_keywords = ("风口研报",)
    classify_subdir = "风口研报"


class FakeState:
    def __init__(self):
        self.marked = []

    @property
    def ids(self):
        return set()

    def is_duplicate(self, file_id):
        return False

    def is_duplicate_name_size(self, name, size):
        return False

    def mark(self, file_id, file_name=None, size=None):
        self.marked.append((file_id, file_name, size))


class FakeDownloader:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def download_pdf(self, server, group_id, file_id, file_name, save_dir,
                           busid=None, url=None, retries=2):
        self.calls.append((file_id, file_name, busid))
        return self.result


class FakeImageDownloader:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def download_image(self, server, image_file, file_name, save_dir,
                             url=None, retries=2):
        self.calls.append((image_file, file_name, url))
        return self.result


def test_extract_file_segment():
    msg = [{"type": "file", "data": {"file_id": "abc", "name": "a.pdf", "size": 10}}]
    seg = extract_file_segment(msg)
    assert seg["file_id"] == "abc"


def test_extract_text_and_image_segments():
    message = [
        {"type": "text", "data": {"text": "正文"}},
        {"type": "image", "data": {"file": "img-1", "url": "https://image"}},
        {"type": "text", "data": {"text": "补充"}},
    ]
    assert extract_text_segments(message) == "正文\n补充"
    assert extract_image_segments(message) == [
        {"file": "img-1", "url": "https://image"},
    ]


@pytest.mark.asyncio
async def test_on_message_filters_and_processes():
    state = FakeState()
    downloader = FakeDownloader(result=None)
    handler = QqEventHandler(FakeSettings(), state, None)
    handler.downloader = downloader  # 注入 fake
    await handler.on_message({"post_type": "message", "message_type": "group",
                              "group_id": 123,
                              "message": [{"type": "file",
                                           "data": {"file_id": "f1", "name": "a.pdf",
                                                    "size": 10}}]})
    assert downloader.calls == [("f1", "a.pdf", None)]
    # 非目标群被过滤
    await handler.on_message({"post_type": "message", "message_type": "group",
                              "group_id": 999,
                              "message": [{"type": "file", "data": {}}]})
    assert len(downloader.calls) == 1


@pytest.mark.asyncio
async def test_on_message_ignores_non_pdf_file():
    state = FakeState()
    downloader = FakeDownloader(result=None)
    handler = QqEventHandler(FakeSettings(), state, None)
    handler.downloader = downloader
    await handler.on_message({"post_type": "message", "message_type": "group",
                              "group_id": 123,
                              "message": [{"type": "file",
                                           "data": {"file_id": "f2", "name": "a.docx"}}]})
    assert downloader.calls == []


@pytest.mark.asyncio
async def test_on_notice_group_upload():
    state = FakeState()
    downloader = FakeDownloader(result=None)
    handler = QqEventHandler(FakeSettings(), state, None)
    handler.downloader = downloader
    await handler.on_notice({"post_type": "notice", "notice_type": "group_upload",
                             "group_id": 123,
                             "file": {"id": "g1", "name": "b.pdf", "busid": 3}})
    assert downloader.calls == [("g1", "b.pdf", 3)]


@pytest.mark.asyncio
async def test_push_payload(monkeypatch):
    """payload 构造：source/message_id/字段裁剪对齐 spec D5。"""
    sent = {}
    monkeypatch.setattr("feishu_bot.handlers._load_stock_name_map",
                        lambda url: {"中际旭创": "300308"})
    monkeypatch.setattr("feishu_bot.handlers._extract_stock_codes",
                        lambda text, m: ["300308"])
    monkeypatch.setattr("feishu_bot.handlers._extract_keywords",
                        lambda text: [{"keyword": "涨价", "dimension": "price_change"}])

    def fake_push(url, token, payload):
        sent.update(payload)
        return True

    monkeypatch.setattr("feishu_bot.handlers._push_to_backend", fake_push)
    handler = QqEventHandler(FakeSettings(), FakeState(), None)
    await handler._push("测试文本 300308", "file-1", ocr_text="OCR 文本")
    assert sent["source"] == "qq"
    assert sent["message_id"] == "qq_file-1"
    assert sent["chat_id"] == "123"
    assert sent["text"] == "测试文本 300308"
    assert sent["ocr_text"] == "OCR 文本"
    assert sent["stock_codes"] == ["300308"]
    assert sent["keywords"] == [{"keyword": "涨价", "dimension": "price_change"}]


@pytest.mark.asyncio
async def test_text_message_is_stored_in_text_field(monkeypatch):
    sent = {}
    monkeypatch.setattr("feishu_bot.handlers._load_stock_name_map", lambda _: {})
    monkeypatch.setattr("feishu_bot.handlers._extract_stock_codes", lambda *_: [])
    monkeypatch.setattr("feishu_bot.handlers._extract_keywords", lambda _: [])
    monkeypatch.setattr("feishu_bot.handlers._push_to_backend",
                        lambda _url, _token, payload: sent.update(payload) or True)
    handler = QqEventHandler(FakeSettings(), FakeState(), None)

    await handler.on_message({"post_type": "message", "message_type": "group",
                              "group_id": 123, "message_id": 88,
                              "message": [{"type": "text", "data": {"text": "关注中际旭创"}}]})

    assert sent["message_id"] == "qq_message_88"
    assert sent["message_type"] == "text"
    assert sent["text"] == "关注中际旭创"
    assert sent["ocr_text"] == ""


@pytest.mark.asyncio
async def test_image_ocr_is_stored_in_ocr_text(monkeypatch, tmp_path):
    image_path = tmp_path / "report.jpg"
    image_path.write_bytes(b"image")
    sent = {}
    monkeypatch.setattr("feishu_bot.handlers._load_stock_name_map", lambda _: {})
    monkeypatch.setattr("feishu_bot.handlers._extract_stock_codes", lambda *_: ["300308"])
    monkeypatch.setattr("feishu_bot.handlers._extract_keywords", lambda _: [])
    monkeypatch.setattr("feishu_bot.handlers._push_to_backend",
                        lambda _url, _token, payload: sent.update(payload) or True)
    monkeypatch.setattr("feishu_bot.analyzer.extract_text_from_image", lambda _: "图片识别文本")
    handler = QqEventHandler(FakeSettings(), FakeState(), None)
    handler.image_downloader = FakeImageDownloader(image_path)

    await handler.on_message({"post_type": "message", "message_type": "group",
                              "group_id": 123, "message_id": 89,
                              "message": [
                                  {"type": "text", "data": {"text": "图片说明"}},
                                  {"type": "image", "data": {"file": "img-1", "url": "https://image"}},
                              ]})

    assert sent["message_type"] == "mixed"
    assert sent["text"] == "图片说明"
    assert sent["ocr_text"] == "图片识别文本"


@pytest.mark.asyncio
async def test_on_meta_event_writes_heartbeat(monkeypatch):
    calls = []
    monkeypatch.setattr("feishu_bot.handlers._write_heartbeat",
                        lambda: calls.append(1))
    handler = QqEventHandler(FakeSettings(), FakeState(), None)
    await handler.on_meta_event({"post_type": "meta_event",
                                 "meta_event_type": "heartbeat"})
    assert calls == [1]
