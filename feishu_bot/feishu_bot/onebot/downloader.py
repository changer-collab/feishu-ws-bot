"""经 OneBot API 下载 QQ 群 PDF 和图片。

- 群文件上传通知（含 busid）→ get_group_file_url 取直链下载
- 聊天文件消息 → get_file 返回 base64 或 url
- 失败重试（指数退避），仍失败返回 None
"""
import asyncio
import base64
import logging
from pathlib import Path
from typing import Optional

import requests

from ..downloader import _safe_filename, _unique_path  # 复用飞书下载器文件名/唯一路径逻辑

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def safe_pdf_filename(name: str) -> str:
    """在复用 _safe_filename 基础上保证 .pdf 后缀。"""
    base = _safe_filename(name, default_ext="pdf")
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


def _save_bytes(data: bytes, file_name: str, save_dir: str) -> Path:
    out_dir = Path(save_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _unique_path(out_dir / safe_pdf_filename(file_name))
    out_path.write_bytes(data)
    logger.info("PDF 已下载: %s (%d bytes)", out_path, len(data))
    return out_path


def safe_image_filename(name: str) -> str:
    """生成可保存的图片文件名；图片事件通常不包含原始扩展名。"""
    base = _safe_filename(name, default_ext="jpg")
    if not Path(base).suffix:
        base += ".jpg"
    return base


def _save_image_bytes(data: bytes, file_name: str, save_dir: str) -> Path:
    out_dir = Path(save_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _unique_path(out_dir / safe_image_filename(file_name))
    out_path.write_bytes(data)
    logger.info("图片已下载: %s (%d bytes)", out_path, len(data))
    return out_path


async def download_pdf(server, group_id: int, file_id: str, file_name: str,
                       save_dir: str, busid: Optional[int] = None,
                       url: Optional[str] = None,
                       retries: int = 2) -> Optional[Path]:
    """下载群 PDF 文件，成功返回保存路径，失败返回 None。"""
    for attempt in range(retries + 1):
        try:
            if url and not file_id:
                return _save_bytes(await asyncio.to_thread(_http_get, url), file_name, save_dir)
            data: Optional[bytes] = None
            target_url = url
            if busid is not None:
                resp = await server.call(
                    "get_group_file_url",
                    {"group_id": group_id, "file_id": file_id, "busid": busid},
                    timeout=15)
                target_url = (resp.get("data") or {}).get("url")
            else:
                # 聊天文件消息：优先 get_file；失败/超时则回退到消息段自带的 url 直连
                # （get_file 依赖 packet 后端，QQ 版本不匹配时可能卡住）
                try:
                    resp = await server.call("get_file", {"file_id": file_id}, timeout=15)
                    d = resp.get("data") or {}
                    if d.get("file"):
                        data = base64.b64decode(d["file"])
                    target_url = d.get("url") or url
                except Exception:
                    if not url:
                        raise  # 无 url 可回退，交给外层重试
                    logger.warning("get_file 调用失败，回退消息段 url 直连: file_id=%s", file_id)
            if data is not None:
                return _save_bytes(data, file_name, save_dir)
            if target_url:
                return _save_bytes(
                    await asyncio.to_thread(_http_get, target_url), file_name, save_dir)
            logger.warning("OneBot 未返回下载内容: file_id=%s", file_id)
            return None
        except Exception:
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))
            else:
                logger.exception("PDF 下载失败: file_id=%s file_name=%s", file_id, file_name)
    return None


async def download_image(server, image_file: str, file_name: str, save_dir: str,
                         url: Optional[str] = None,
                         retries: int = 2) -> Optional[Path]:
    """下载 OneBot 图片消息，优先使用消息中的直链并兼容 get_image 回退。"""
    for attempt in range(retries + 1):
        try:
            target_url = url
            data: Optional[bytes] = None
            if not target_url:
                response = await server.call("get_image", {"file": image_file}, timeout=15)
                payload = response.get("data") or {}
                target_url = payload.get("url")
                local_path = payload.get("file")
                if local_path:
                    path = Path(str(local_path))
                    if path.is_file():
                        data = await asyncio.to_thread(path.read_bytes)
            if data is not None:
                return _save_image_bytes(data, file_name, save_dir)
            if target_url:
                return _save_image_bytes(
                    await asyncio.to_thread(_http_get, target_url), file_name, save_dir)
            logger.warning("OneBot 未返回图片下载内容: file=%s", image_file)
            return None
        except Exception:
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))
            else:
                logger.exception("图片下载失败: file=%s file_name=%s", image_file, file_name)
    return None


def _http_get(url: str) -> bytes:
    resp = requests.get(url, timeout=60, headers=_UA)
    resp.raise_for_status()
    return resp.content
