import json
import logging
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from collections import OrderedDict
from datetime import datetime
from typing import Any, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import GetChatRequest, P2ImMessageReceiveV1

from .downloader import download_message_resource
from .analyzer import classify_file, recognize_image_via_feishu

logger = logging.getLogger(__name__)

# ==================== 心跳文件 ====================

_HEARTBEAT_FILE = "feishu_bot.heartbeat"

def _write_heartbeat() -> None:
    """更新心跳文件，记录最后一次活动时间"""
    from datetime import datetime
    try:
        with open(_HEARTBEAT_FILE, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception:
        pass  # 心跳写入失败不应影响主流程

# ==================== 股票代码/关键词提取 ====================

# A股代码模式：6位数字，0/3/6开头
_STOCK_CODE_PATTERN = re.compile(r'\b([036]\d{5})\b')
# 股票名称+代码模式：如"中际旭创(300308)"、"中际旭创：300308"
_STOCK_NAME_CODE_PATTERN = re.compile(r'[\u4e00-\u9fff]{2,6}[（(：:]([036]\d{5})[）)：:]?')

# 9维度关键词体系（与后端 HotKeywordDetectorService 保持一致）
KEYWORD_DIMENSIONS = {
    "supply_demand": ["缺货", "断供", "无货", "库存告急", "库存见底", "供不应求", "需求旺盛", "订单积压", "排产紧张", "产能满载", "扩产", "新增产能", "产能瓶颈", "去库存", "低库存", "补库存"],
    "order_level": ["百亿订单", "十亿订单", "重大合同", "战略订单", "十年订单", "长单锁定", "订单爆发", "订单翻倍", "订单激增", "中标", "签约", "大客户", "头部客户", "验证通过"],
    "price_change": ["涨价", "提价", "调价", "价格上调", "价格高位", "持续上涨", "价格创新高", "降价", "价格战", "价格下行"],
    "tech_breakthrough": ["量产", "规模化", "批产", "独家", "独家供应", "唯一", "首发", "率先", "通过验证", "客户认证", "验厂通过"],
    "policy_catalyst": ["政策利好", "补贴", "纳入目录", "国家战略", "获批"],
    "earnings_verify": ["业绩超预期", "净利翻倍", "扭亏", "预告增长"],
    "industry_cycle": ["景气度上行", "行业拐点", "周期反转"],
    "capital_action": ["回购", "增持", "定增", "员工持股", "机构调研"],
    "risk_signal": ["减持", "商誉减值", "诉讼", "被调查", "退市风险"],
}

# 构建关键词→维度映射
_KEYWORD_TO_DIMENSION: dict[str, str] = {}
for _dim_key, _keywords in KEYWORD_DIMENSIONS.items():
    for _kw in _keywords:
        _KEYWORD_TO_DIMENSION[_kw] = _dim_key


_STOCK_NAME_MAP_CACHE: dict[str, dict[str, str]] = {}


def _load_stock_name_map(api_url: str) -> dict[str, str]:
    """从现有股票列表接口只读加载“股票名称→代码”，进程内缓存。"""
    base_url = api_url.rstrip("/")
    if not base_url:
        return {}
    if base_url in _STOCK_NAME_MAP_CACHE:
        return _STOCK_NAME_MAP_CACHE[base_url]

    stock_name_map: dict[str, str] = {}
    try:
        page = 1
        while True:
            query = urllib.parse.urlencode({"page": page, "pageSize": 500})
            url = f"{base_url}/api/cn/stocks?{query}"
            with urllib.request.urlopen(url, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
            data = result.get("data") or {}
            for item in data.get("股票列表") or []:
                name = str(item.get("股票简称") or "").strip()
                code = str(item.get("股票代码") or "").strip()
                if name and re.fullmatch(r"[036]\d{5}", code):
                    stock_name_map[name] = code
            total_pages = int(data.get("总页数") or 0)
            if page >= total_pages:
                break
            page += 1
        logger.info("股票名称映射加载完成: %d只", len(stock_name_map))
    except Exception:
        logger.exception("股票名称映射加载失败，暂时仅按代码识别")

    _STOCK_NAME_MAP_CACHE[base_url] = stock_name_map
    return stock_name_map


def _extract_stock_codes(
    text: str,
    stock_name_map: Optional[dict[str, str]] = None,
) -> list[str]:
    """从文本中提取A股代码"""
    codes = set()
    valid_codes = set(stock_name_map.values()) if stock_name_map else None
    for m in _STOCK_NAME_CODE_PATTERN.finditer(text):
        code = m.group(1)
        if valid_codes is None or code in valid_codes:
            codes.add(code)
    for m in _STOCK_CODE_PATTERN.finditer(text):
        code = m.group(1)
        # 过滤非A股代码（如日期、纯数字等）
        if code[0] in ('0', '3', '6') and (valid_codes is None or code in valid_codes):
            codes.add(code)
    if stock_name_map:
        for name in sorted(stock_name_map, key=len, reverse=True):
            if name in text:
                codes.add(stock_name_map[name])
    return sorted(codes)


def _extract_keywords(text: str) -> list[dict[str, str]]:
    """从文本中匹配9维度关键词，返回 [{keyword, dimension}]"""
    matched = []
    for kw, dim_key in _KEYWORD_TO_DIMENSION.items():
        if kw in text:
            matched.append({"keyword": kw, "dimension": dim_key})
    return matched


def _extract_text_from_post(content: Any) -> str:
    """从飞书 post（富文本）消息中提取纯文本"""
    text_parts: list[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("tag") == "text":
                t = obj.get("text", "").strip()
                if t:
                    text_parts.append(t)
            elif obj.get("tag") == "a":
                t = obj.get("text", "").strip()
                href = obj.get("href", "").strip()
                if t:
                    text_parts.append(t)
                if href:
                    text_parts.append(href)
            else:
                for v in obj.values():
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(content)
    return " ".join(text_parts)


def _ocr_images(
    client: lark.Client,
    image_paths: list,
    enable_ocr: bool,
) -> str:
    """对下载的图片做 OCR，返回拼接后的文本。

    Args:
        client: lark.Client 实例
        image_paths: 已下载的图片路径列表
        enable_ocr: 是否启用 OCR

    Returns:
        所有图片 OCR 文本用 \\n 拼接的字符串，失败返回空字符串
    """
    if not enable_ocr or not image_paths:
        return ""

    all_text: list[str] = []
    for img_path in image_paths:
        if img_path is None:
            continue
        ocr_text = recognize_image_via_feishu(client, img_path)
        if ocr_text:
            all_text.append(ocr_text)

    return "\n".join(all_text)


def _push_to_backend(api_url: str, internal_token: str, payload: dict) -> bool:
    """推送数据到后端API"""
    url = f"{api_url}/api/internal/feishu-message"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-internal-token": internal_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("推送后端成功: %s", url)
                return True
            else:
                logger.warning("推送后端返回非200: status=%s url=%s", resp.status, url)
                return False
    except urllib.error.URLError as e:
        logger.warning("推送后端失败: %s url=%s", e, url)
        return False
    except Exception:
        logger.exception("推送后端异常 url=%s", url)
        return False


# 已处理消息 ID 缓存，用于去重（避免飞书重推事件导致重复下载）
_MAX_DEDUP_SIZE = 10000
_PROCESSED_CACHE_FILE = "feishu_processed_ids.json"

# 从文件加载已处理消息ID
_processed_message_ids: OrderedDict[str, bool] = OrderedDict()
if os.path.exists(_PROCESSED_CACHE_FILE):
    try:
        with open(_PROCESSED_CACHE_FILE, "r") as f:
            ids = json.load(f)
            for mid in ids:
                _processed_message_ids[mid] = True
        logger.info("从文件加载 %d 条已处理消息ID", len(_processed_message_ids))
    except Exception:
        logger.warning("加载已处理消息ID缓存失败，从空开始")


def _save_processed_ids() -> None:
    """将已处理消息ID保存到文件"""
    try:
        with open(_PROCESSED_CACHE_FILE, "w") as f:
            json.dump(list(_processed_message_ids.keys()), f)
    except Exception:
        logger.warning("保存已处理消息ID缓存失败")


def _is_duplicate(message_id: str) -> bool:
    if message_id in _processed_message_ids:
        logger.info("消息 %s 已处理过，跳过", message_id)
        return True
    _processed_message_ids[message_id] = True
    if len(_processed_message_ids) > _MAX_DEDUP_SIZE:
        _processed_message_ids.popitem(last=False)
    _save_processed_ids()
    return False


def _extract_image_keys_from_post(content: Any) -> list[str]:
    """从飞书 post（富文本）消息的 content 中递归提取所有内嵌图片的 image_key。

    飞书 post 消息结构示例:
    {
        "zh_cn": {
            "title": "...",
            "content": [
                [ {"tag": "text", "text": "hello"}, {"tag": "img", "image_key": "img_xxx"} ],
                [ {"tag": "img", "image_key": "img_yyy"} ]
            ]
        }
    }
    也可能没有语言层，直接是 {"title": ..., "content": [[...]]}。
    """
    image_keys: list[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("tag") == "img":
                key = obj.get("image_key")
                if key and key not in image_keys:
                    image_keys.append(key)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(content)
    return image_keys


def _date_prefix() -> str:
    return datetime.now().strftime("%Y%m%d")


def _json_loads_maybe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    """兼容 SDK model 与 dict 两种形态。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _get_chat_info(client: lark.Client, chat_id: str) -> Optional[dict]:
    """根据 chat_id 查询群信息。需要应用具备相应 IM 群信息读取权限。"""
    request = GetChatRequest.builder().chat_id(chat_id).build()
    response = client.im.v1.chat.get(request)
    if not response.success():
        logger.warning(
            "查询群信息失败 chat_id=%s code=%s msg=%s log_id=%s",
            chat_id,
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return None
    return _json_loads_maybe(lark.JSON.marshal(response.data))


def build_message_handler(
    client: lark.Client,
    *,
    app_id: str,
    app_secret: str,
    fetch_chat_info: bool = True,
    download_pdf: bool = True,
    download_image: bool = True,
    download_dir: str = "downloads",
    classify_keywords: tuple[str, ...] = ("风口研报",),
    classify_subdir: str = "风口研报",
    aistock_api_url: str = "",
    internal_token: str = "",
    monitor_chat_name: str = "",
    enable_ocr: bool = True,
):
    def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
        raw = _json_loads_maybe(lark.JSON.marshal(data))
        # 每次收到消息都更新心跳
        _write_heartbeat()
        event = _safe_get(raw, "event", {})
        message = _safe_get(event, "message", {})
        sender = _safe_get(event, "sender", {})

        chat_id = _safe_get(message, "chat_id")
        chat_type = _safe_get(message, "chat_type")
        message_id = _safe_get(message, "message_id")
        message_type = _safe_get(message, "message_type")
        content = _json_loads_maybe(_safe_get(message, "content"))

        logger.info("收到消息: chat_type=%s chat_id=%s message_id=%s message_type=%s", chat_type, chat_id, message_id, message_type)
        logger.info("sender=%s", json.dumps(sender, ensure_ascii=False))
        logger.info("content=%s", json.dumps(content, ensure_ascii=False))

        # 根据 message_id 去重，避免飞书重推事件导致重复下载
        if message_id and _is_duplicate(message_id):
            return

        # 收集所有已下载的图片路径，用于后续 OCR
        downloaded_images: list = []

        # 如果收到的是文件消息，并且文件名是 PDF，则自动下载到 DOWNLOAD_DIR。
        if download_pdf and message_type == "file" and isinstance(content, dict):
            file_key = content.get("file_key")
            file_name = content.get("file_name") or content.get("name") or f"{file_key}.pdf"
            if message_id and file_key:
                try:
                    saved = download_message_resource(
                        app_id=app_id,
                        app_secret=app_secret,
                        message_id=message_id,
                        file_key=file_key,
                        file_name=file_name,
                        save_dir=download_dir,
                        resource_type="file",
                        allowed_types={"pdf"},
                    )
                    if saved and classify_keywords:
                        try:
                            classify_file(
                                saved,
                                keywords=list(classify_keywords),
                                target_subdir=classify_subdir,
                                base_dir=download_dir,
                            )
                        except Exception:
                            logger.exception("PDF 分类分析失败 message_id=%s file=%s", message_id, saved)
                except Exception:
                    logger.exception("PDF 下载失败 message_id=%s file_key=%s file_name=%s", message_id, file_key, file_name)
            else:
                logger.warning("文件消息缺少 message_id 或 file_key，无法下载。message_id=%s content=%s", message_id, content)

        # 如果收到的是图片消息，自动下载到 DOWNLOAD_DIR。
        if download_image and message_type == "image" and isinstance(content, dict):
            image_key = content.get("image_key")
            file_name = f"{_date_prefix()}_{image_key}.jpg"
            if message_id and image_key:
                try:
                    saved = download_message_resource(
                        app_id=app_id,
                        app_secret=app_secret,
                        message_id=message_id,
                        file_key=image_key,
                        file_name=file_name,
                        save_dir=download_dir,
                        resource_type="image",
                        allowed_types=None,
                    )
                    if saved:
                        downloaded_images.append(saved)
                    if saved and classify_keywords:
                        try:
                            classify_file(
                                saved,
                                keywords=list(classify_keywords),
                                target_subdir=classify_subdir,
                                base_dir=download_dir,
                            )
                        except Exception:
                            logger.exception("图片分类分析失败 message_id=%s file=%s", message_id, saved)
                except Exception:
                    logger.exception("图片下载失败 message_id=%s image_key=%s", message_id, image_key)
            else:
                logger.warning("图片消息缺少 message_id 或 image_key，无法下载。message_id=%s content=%s", message_id, content)

        # 如果收到的是富文本（post）消息，提取并下载其中内嵌的图片。
        if download_image and message_type == "post" and isinstance(content, dict):
            image_keys = _extract_image_keys_from_post(content)
            for idx, image_key in enumerate(image_keys, 1):
                file_name = f"{_date_prefix()}_{image_key}.jpg"
                if message_id and image_key:
                    try:
                        saved = download_message_resource(
                            app_id=app_id,
                            app_secret=app_secret,
                            message_id=message_id,
                            file_key=image_key,
                            file_name=file_name,
                            save_dir=download_dir,
                            resource_type="image",
                            allowed_types=None,
                        )
                        if saved:
                            downloaded_images.append(saved)
                        if saved and classify_keywords:
                            try:
                                classify_file(
                                    saved,
                                    keywords=list(classify_keywords),
                                    target_subdir=classify_subdir,
                                    base_dir=download_dir,
                                )
                            except Exception:
                                logger.exception("富文本内嵌图片分类分析失败 message_id=%s file=%s", message_id, saved)
                    except Exception:
                        logger.exception("富文本内嵌图片下载失败 message_id=%s image_key=%s", message_id, image_key)
                else:
                    logger.warning("富文本内嵌图片缺少 message_id 或 image_key，无法下载。message_id=%s image_key=%s", message_id, image_key)

        # 飞书群聊一般是 group；单聊一般是 p2p。
        if chat_type == "group" and chat_id:
            logger.info("捕获到群消息，chat_id=%s", chat_id)
            chat_name = ""
            if fetch_chat_info:
                chat_info = _get_chat_info(client, chat_id)
                if chat_info:
                    chat_name = chat_info.get("name", "")
                    logger.info("群信息=%s", json.dumps(chat_info, ensure_ascii=False, indent=2))

            # 文本消息解析 + 推送后端（仅处理监控群或所有群）
            if monitor_chat_name and chat_name != monitor_chat_name:
                logger.info("群名[%s]不匹配监控群[%s]，跳过文本解析", chat_name, monitor_chat_name)
            else:
                text_content = ""
                if message_type == "text" and isinstance(content, dict):
                    text_content = content.get("text", "").strip()
                elif message_type == "post" and isinstance(content, dict):
                    text_content = _extract_text_from_post(content).strip()

                # 对下载的图片做 OCR；正文仍走原字段，完整 OCR 单独保存。
                ocr_text = ""
                if downloaded_images:
                    ocr_text = _ocr_images(client, downloaded_images, enable_ocr)
                    if ocr_text:
                        logger.info("图片OCR成功, 获取%d字符完整文本", len(ocr_text))

                analysis_text = "\n".join(part for part in (text_content, ocr_text) if part)
                stock_name_map = _load_stock_name_map(aistock_api_url)
                stock_codes = (
                    _extract_stock_codes(analysis_text, stock_name_map)
                    if analysis_text
                    else []
                )
                keywords = _extract_keywords(analysis_text) if analysis_text else []
                payload = {
                    "source": "feishu",
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "message_id": message_id,
                    "message_type": message_type,
                    "text": text_content[:2000],
                    "ocr_text": ocr_text,
                    "stock_codes": stock_codes,
                    "keywords": keywords,
                    "received_at": datetime.now().isoformat(),
                }

                # 所有包含正文或 OCR 内容的群消息均入库；是否调用千问由后端决定。
                if text_content or ocr_text:
                    logger.info(
                        "飞书消息解析: stock_codes=%s keywords=%s",
                        stock_codes,
                        [k["keyword"] for k in keywords],
                    )
                    _push_to_backend(aistock_api_url, internal_token, payload)
        else:
            logger.info("当前不是群消息，已跳过群信息查询。")

    return do_p2_im_message_receive_v1


def do_customized_event(data: lark.CustomizedEvent) -> None:
    """兜底处理未显式注册的自定义事件，便于后续扩展。"""
    logger.info("收到自定义事件=%s", lark.JSON.marshal(data))
