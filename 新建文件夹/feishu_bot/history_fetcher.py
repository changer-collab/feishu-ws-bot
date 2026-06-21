"""
飞书群历史消息拉取模块

功能：
- 启动时/重连后拉取最近3天的群消息
- 调用飞书 Open API GET /open-apis/im/v1/messages
- 将消息推送到后端 API 入库（复用现有 _push_to_backend 逻辑）
- 自动跳过已入库的消息（后端 message_id 去重）

前置条件：
- 飞书应用需开启权限：im:message.group_at_msg:readonly（读取群消息）
- 飞书应用需加入目标群
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import ListMessageRequest

logger = logging.getLogger(__name__)

# 拉取天数
FETCH_DAYS = 3
# 每次API调用最大返回条数
PAGE_SIZE = 50


def fetch_group_history_messages(
    client: lark.Client,
    chat_id: str,
    days: int = FETCH_DAYS,
) -> list[dict]:
    """
    拉取指定群最近N天的历史消息

    Args:
        client: lark.Client 实例
        chat_id: 群聊ID
        days: 拉取天数

    Returns:
        消息列表，每条包含 message_id, message_type, content, create_time 等
    """
    messages: list[dict] = []
    start_time = int((datetime.now() - timedelta(days=days)).timestamp())
    page_token: Optional[str] = None
    has_more = True

    logger.info("开始拉取群 %s 最近 %d 天历史消息...", chat_id, days)

    while has_more:
        try:
            request = ListMessageRequest.builder() \
                .container_id_type("chat") \
                .container_id(chat_id) \
                .page_size(PAGE_SIZE) \
                .start_time(str(start_time)) \
                .direction("DESC")  # 从新到旧

            if page_token:
                request = request.page_token(page_token)

            response = client.im.v1.message.list(request)

            if not response.success():
                logger.warning(
                    "拉取历史消息失败: code=%s msg=%s",
                    response.code,
                    response.msg,
                )
                break

            data = response.data
            if data and data.items:
                for item in data.items:
                    messages.append({
                        "message_id": item.message_id,
                        "message_type": item.msg_type,
                        "content": item.body.content if item.body else "",
                        "create_time": item.create_time,
                        "sender_id": item.sender.id if item.sender else "",
                        "sender_type": item.sender.sender_type if item.sender else "",
                    })

            has_more = data.has_more if data else False
            page_token = data.page_token if data and data.page_token else None

            # 避免API限频
            time.sleep(0.5)

        except Exception:
            logger.exception("拉取历史消息异常 chat_id=%s", chat_id)
            break

    logger.info("拉取到 %d 条历史消息 (群 %s)", len(messages), chat_id)
    return messages


def fetch_and_push_history(
    client: lark.Client,
    chat_id: str,
    chat_name: str,
    aistock_api_url: str,
    internal_token: str,
    days: int = FETCH_DAYS,
) -> int:
    """
    拉取历史消息并推送到后端入库

    Returns:
        成功推送的消息数
    """
    from .handlers import _push_to_backend, _extract_stock_codes, _extract_keywords, _extract_text_from_post

    messages = fetch_group_history_messages(client, chat_id, days)
    pushed = 0

    for msg in messages:
        message_id = msg["message_id"]
        message_type = msg["message_type"]
        content_str = msg.get("content", "")

        # 解析消息内容
        text_content = ""
        try:
            content = json.loads(content_str) if content_str else {}
        except (json.JSONDecodeError, TypeError):
            content = {}

        if message_type == "text" and isinstance(content, dict):
            text_content = content.get("text", "").strip()
        elif message_type == "post" and isinstance(content, dict):
            text_content = _extract_text_from_post(content).strip()
        else:
            text_content = content_str[:2000] if content_str else ""

        if not text_content:
            continue

        # 提取股票代码和关键词
        stock_codes = _extract_stock_codes(text_content) if text_content else []
        keywords = _extract_keywords(text_content) if text_content else []

        if not (stock_codes or keywords):
            continue

        # 构造 payload
        create_time = msg.get("create_time", "")
        received_at = datetime.fromtimestamp(int(create_time)).isoformat() if create_time else datetime.now().isoformat()

        payload = {
            "source": "feishu_history",
            "chat_id": chat_id,
            "chat_name": chat_name,
            "message_id": message_id,
            "message_type": message_type,
            "text": text_content[:2000],
            "stock_codes": stock_codes,
            "keywords": keywords,
            "received_at": received_at,
        }

        if _push_to_backend(aistock_api_url, internal_token, payload):
            pushed += 1

    logger.info("历史消息推送完成: 拉取 %d 条, 推送成功 %d 条", len(messages), pushed)
    return pushed
