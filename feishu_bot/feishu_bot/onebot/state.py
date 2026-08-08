"""已处理 QQ 文件去重状态，JSON 持久化。

同一 PDF 可能经两条路径到达（聊天文件消息的 file_id 与群文件存储的 file_id 不同），
因此除 file_id 外，再用 文件名|大小 作为第二重去重键。
"""
import json
import logging
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)


class FileIdState:
    MAX_SIZE = 10000

    def __init__(self, path: str = "qq_processed_ids.json"):
        self._path = Path(path)
        self._ids: "OrderedDict[str, bool]" = OrderedDict()
        self._name_sizes: "OrderedDict[str, bool]" = OrderedDict()
        self._load()

    @property
    def ids(self) -> set:
        return set(self._ids.keys())

    def is_duplicate(self, file_id: str) -> bool:
        return file_id in self._ids

    def is_duplicate_name_size(self, file_name: str, size) -> bool:
        if not size:
            return False
        return f"{file_name}|{size}" in self._name_sizes

    def mark(self, file_id: str, file_name=None, size=None) -> None:
        if file_id:
            self._ids[file_id] = True
        if file_name and size:
            self._name_sizes[f"{file_name}|{size}"] = True
        while len(self._ids) > self.MAX_SIZE:
            self._ids.popitem(last=False)
        while len(self._name_sizes) > self.MAX_SIZE:
            self._name_sizes.popitem(last=False)
        self._save()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for fid in data.get("file_ids", []):
                self._ids[str(fid)] = True
            for key in data.get("name_sizes", []):
                self._name_sizes[str(key)] = True
            logger.info("加载 %d 条 file_id、%d 条 name|size 去重记录",
                        len(self._ids), len(self._name_sizes))
        except Exception:
            logger.warning("加载去重状态失败，从空开始: %s", self._path)

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps({"file_ids": list(self._ids.keys()),
                            "name_sizes": list(self._name_sizes.keys())},
                           ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            logger.warning("保存去重状态失败: %s", self._path)
