"""OneBot 群事件处理：文字、图片、PDF → 提取 → 推送后端。"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from .. import analyzer
from .. import handlers as feishu_handlers
from . import downloader as _downloader

logger = logging.getLogger(__name__)


def extract_file_segment(message) -> Optional[dict]:
    """从 OneBot message 数组里提取 file 段。"""
    for seg in message or []:
        if isinstance(seg, dict) and seg.get("type") == "file":
            return seg.get("data") or {}
    return None


def extract_text_segments(message) -> str:
    """拼接 QQ 消息中的全部文字段。"""
    parts: list[str] = []
    for seg in message or []:
        if not isinstance(seg, dict) or seg.get("type") != "text":
            continue
        text = str((seg.get("data") or {}).get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def extract_image_segments(message) -> list[dict]:
    """提取 QQ 图片段；NapCat 通常以 file 作为图片标识。"""
    images: list[dict] = []
    for seg in message or []:
        if isinstance(seg, dict) and seg.get("type") == "image":
            data = seg.get("data") or {}
            if isinstance(data, dict):
                images.append(data)
    return images


class QqEventHandler:
    """处理目标群文字、图片和 PDF，并推送到后端。"""

    def __init__(self, settings, state, server):
        self.settings = settings
        self.state = state
        self.server = server
        self.downloader = _downloader.download_pdf  # 测试可注入
        self.image_downloader = _downloader.download_image  # 测试可注入
        self._stock_name_map: Optional[dict] = None
        self._inflight_file_ids: set[str] = set()

    async def on_message(self, event: dict) -> None:
        if event.get("message_type") != "group":
            return
        if int(event.get("group_id") or 0) != self.settings.qq_group_id:
            return
        message = event.get("message") or []
        seg = extract_file_segment(message)
        if seg:
            file_id = seg.get("file_id") or seg.get("file") or ""
            file_name = seg.get("name") or ""
            size = seg.get("size")
            url = seg.get("url")
        else:
            file_id = file_name = url = ""
            size = None
        if file_id and file_name.lower().endswith(".pdf"):
            feishu_handlers._write_heartbeat()
            logger.info("开始处理群消息 PDF: file_id=%s file_name=%s", file_id, file_name)
            await self.process_pdf(file_id, file_name, url=url, size=size)
        elif seg:
            logger.info("忽略非 PDF 或缺少文件信息的群消息: file_id=%s file_name=%s",
                        file_id, file_name)

        text = extract_text_segments(message)
        images = extract_image_segments(message)
        if text or images:
            await self.process_message_content(
                str(event.get("message_id") or ""), text, images)

    async def on_notice(self, event: dict) -> None:
        if event.get("notice_type") != "group_upload":
            return
        if int(event.get("group_id") or 0) != self.settings.qq_group_id:
            return
        f = event.get("file") or {}
        file_id = str(f.get("id") or "")
        file_name = f.get("name") or ""
        if file_id and file_name.lower().endswith(".pdf"):
            feishu_handlers._write_heartbeat()
            logger.info("开始处理群文件上传 PDF: file_id=%s file_name=%s", file_id, file_name)
            await self.process_pdf(file_id, file_name,
                                   busid=f.get("busid"), size=f.get("size"))
        else:
            logger.info("忽略非 PDF 或缺少文件信息的群文件上传: file_id=%s file_name=%s",
                        file_id, file_name)

    async def on_meta_event(self, event: dict) -> None:
        """NapCat 心跳：更新心跳文件，防止看门狗误杀。"""
        if event.get("meta_event_type") == "heartbeat":
            feishu_handlers._write_heartbeat()

    async def process_pdf(self, file_id: str, file_name: str,
                          busid: Optional[int] = None,
                          url: Optional[str] = None,
                          size: Optional[int] = None) -> None:
        if file_id in self._inflight_file_ids:
            logger.info("PDF 正在处理中，跳过重复事件: file_id=%s", file_id)
            return
        if self.state.is_duplicate(file_id):
            logger.info("PDF 已处理，跳过重复 file_id: file_id=%s", file_id)
            return
        if self.state.is_duplicate_name_size(file_name, size):
            logger.info("PDF 已处理，跳过同名同大小文件: file_name=%s size=%s",
                        file_name, size)
            return
        try:
            self._inflight_file_ids.add(file_id)
            # 兼容两种注入形态：默认是模块级 download_pdf 函数（可调用）；
            # 测试注入的 fake 是带 download_pdf 方法的实例（不可调用）。
            downloader = self.downloader
            if not callable(downloader):
                downloader = downloader.download_pdf
            saved = await downloader(
                self.server, self.settings.qq_group_id, file_id, file_name,
                self.settings.download_dir, busid=busid, url=url)
            if saved is None:
                logger.warning("PDF 下载失败，不标记去重（下轮可重试）: file_id=%s", file_id)
                return
            self.state.mark(file_id, file_name, size)
            # 每一页 PDF 都执行 OCR；原生文本与 OCR 文本分字段入库。
            pdf_content = await asyncio.to_thread(analyzer.extract_pdf_content, saved)
            text = pdf_content.text
            ocr_text = pdf_content.ocr_text
            analysis_text = pdf_content.combined_text
            # 按关键词归档（复用现有分类逻辑：匹配则移动到 downloads/风口研报/）
            try:
                await asyncio.to_thread(
                analyzer.classify_file, saved,
                keywords=list(self.settings.classify_keywords),
                target_subdir=self.settings.classify_subdir,
                base_dir=self.settings.download_dir,
                text=analysis_text)
            except Exception:
                logger.exception("PDF 分类归档失败: %s", saved.name)
            if not analysis_text:
                logger.warning("PDF OCR 与文本提取均为空，跳过推送: %s", saved.name)
                return
            await self._push(text, file_id, ocr_text=ocr_text)
        finally:
            self._inflight_file_ids.discard(file_id)

    async def process_message_content(self, message_id: str, text: str,
                                      images: list[dict]) -> None:
        """将同一条 QQ 图文消息合并入库，正文与 OCR 文本保持分字段。"""
        if not message_id:
            logger.warning("QQ 图文消息缺少 message_id，跳过入库")
            return
        state_id = f"message:{message_id}"
        if self.state.is_duplicate(state_id):
            logger.info("QQ 消息已处理，跳过重复 message_id=%s", message_id)
            return

        ocr_parts: list[str] = []
        for index, image in enumerate(images, 1):
            image_file = str(image.get("file") or image.get("file_id") or "")
            if not image_file:
                logger.warning("QQ 图片消息缺少 file，跳过第 %d 张图片: message_id=%s",
                               index, message_id)
                continue
            file_name = str(image.get("name") or image.get("file_name")
                            or f"qq_{message_id}_{index}.jpg")
            url = image.get("url")
            downloader = self.image_downloader
            if not callable(downloader):
                downloader = downloader.download_image
            saved = await downloader(
                self.server, image_file, file_name, self.settings.download_dir, url=url)
            if saved is None:
                continue
            image_text = await asyncio.to_thread(analyzer.extract_text_from_image, saved)
            if image_text.strip():
                ocr_parts.append(image_text.strip())
            try:
                await asyncio.to_thread(
                    analyzer.classify_file, saved,
                    keywords=list(self.settings.classify_keywords),
                    target_subdir=self.settings.classify_subdir,
                    base_dir=self.settings.download_dir,
                    text=image_text,
                )
            except Exception:
                logger.exception("QQ 图片分类归档失败: %s", saved.name)

        ocr_text = "\n".join(ocr_parts)
        if not text and not ocr_text:
            logger.warning("QQ 图片 OCR 均为空，跳过推送: message_id=%s", message_id)
            return
        self.state.mark(state_id)
        message_type = "mixed" if text and images else ("image" if images else "text")
        await self._push(text, f"message_{message_id}", ocr_text=ocr_text,
                         message_type=message_type)

    async def _push(self, text: str, source_id: str, ocr_text: str = "",
                    message_type: str = "file") -> None:
        if self._stock_name_map is None:
            self._stock_name_map = await asyncio.to_thread(
                feishu_handlers._load_stock_name_map, self.settings.aistock_api_url)
        analysis_text = "\n".join(part for part in (text, ocr_text) if part)
        stock_codes = feishu_handlers._extract_stock_codes(analysis_text, self._stock_name_map)
        keywords = feishu_handlers._extract_keywords(analysis_text)
        payload = {
            "source": "qq",
            "chat_id": str(self.settings.qq_group_id),
            "chat_name": f"qq_{self.settings.qq_group_id}",
            "message_id": f"qq_{source_id}",
            "message_type": message_type,
            "text": text,
            "ocr_text": ocr_text,
            "stock_codes": stock_codes,
            "keywords": keywords,
            "received_at": datetime.now().isoformat(),
        }
        ok = False
        for attempt in range(3):  # 推送失败重试（含首次共 3 次）
            ok = await asyncio.to_thread(
                feishu_handlers._push_to_backend,
                self.settings.aistock_api_url, self.settings.internal_token, payload)
            if ok:
                break
            if attempt < 2:
                await asyncio.sleep(3 * (attempt + 1))
        if ok:
            logger.info("已推送 QQ %s 到后端: source_id=%s codes=%s",
                        message_type, source_id, stock_codes)
        else:
            logger.warning("推送后端失败（内容已归档，可人工补推）: source_id=%s", source_id)
