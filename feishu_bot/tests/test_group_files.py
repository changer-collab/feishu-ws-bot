import time

import pytest

from feishu_bot.onebot import group_files


class FakeServer:
    def __init__(self, root=None, folder=None):
        self._root = root or {"status": "ok", "data": {
            "files": [{"file_id": "r1", "file_name": "root.pdf",
                       "upload_time": int(time.time()) - 100, "busid": 1, "file_size": 10}],
            "folders": [{"folder_id": "d1", "folder_name": "sub"}]}}
        self._folder = folder or {"status": "ok", "data": {
            "files": [{"file_id": "f1", "file_name": "sub.pdf",
                       "upload_time": int(time.time()) - 50, "busid": 2, "file_size": 20}],
            "folders": []}}

    async def call(self, action, params=None, timeout=30.0):
        if action == "get_group_root_files":
            return self._root
        if action == "get_group_files_by_folder":
            return self._folder
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_collect_group_files_walks_folders():
    server = FakeServer()
    entries = await group_files.collect_group_files(server, 123)
    assert len(entries) == 2
    ids = {e["file_id"] for e in entries}
    assert ids == {"r1", "f1"}


def test_filter_new_files():
    entries = [
        {"file_id": "a", "file_name": "a.pdf", "upload_time": 1000, "busid": 1},
        {"file_id": "b", "file_name": "b.pdf", "upload_time": 500, "busid": 1},
        {"file_id": "c", "file_name": "c.txt", "upload_time": 2000, "busid": 1},
        {"file_id": "d", "file_name": "d.pdf", "upload_time": 3000, "busid": 1},
    ]
    # 排除已处理 + 排除非.pdf + 时间窗口过滤
    new = group_files.filter_new_files(entries, {"a"}, min_upload_time=1000)
    assert [e["file_id"] for e in new] == ["d"]


@pytest.mark.asyncio
async def test_run_backfill_calls_process_pdf():
    server = FakeServer()

    class FakeSettings:
        qq_group_id = 123
        qq_history_days = 7

    class FakeState:
        @property
        def ids(self):
            return set()

    class FakeHandler:
        def __init__(self):
            self.processed = []

        async def process_pdf(self, file_id, file_name, busid=None, url=None, size=None):
            self.processed.append(file_id)

    handler = FakeHandler()
    n = await group_files.run_backfill(server, FakeSettings(), FakeState(), handler)
    assert n == 2
    assert set(handler.processed) == {"r1", "f1"}
