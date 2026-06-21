import logging
import threading

import lark_oapi as lark
from lark_oapi.ws import Client as WsClient

from feishu_bot.config import Settings
from feishu_bot.handlers import build_message_handler
from feishu_bot.watchdog import run_watchdog


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    logger = logging.getLogger("feishu_ws_bot")
    lark_log_level = getattr(lark.LogLevel, settings.log_level, lark.LogLevel.INFO)

    api_client = (
        lark.Client.builder()
        .app_id(settings.app_id)
        .app_secret(settings.app_secret)
        .log_level(lark_log_level)
        .build()
    )

    # 注意：builder 的前两个参数分别是 encrypt_key 和 verification_token。
    # 使用 WebSocket 长连接时一般填空字符串即可；第三个参数是日志等级。
    event_handler = (
        lark.EventDispatcherHandler.builder("", "", lark_log_level)
        .register_p2_im_message_receive_v1(
            build_message_handler(
                api_client,
                app_id=settings.app_id,
                app_secret=settings.app_secret,
                fetch_chat_info=settings.fetch_chat_info,
                download_pdf=settings.download_pdf,
                download_image=settings.download_image,
                download_dir=settings.download_dir,
                classify_keywords=settings.classify_keywords,
                classify_subdir=settings.classify_subdir,
                aistock_api_url=settings.aistock_api_url,
                internal_token=settings.internal_token,
                monitor_chat_name=settings.monitor_chat_name,
                enable_ocr=settings.enable_ocr,
            )
        )
        .build()
    )

    logger.info("启动飞书长连接机器人，ENV=%s", settings.env)
    logger.info("请确保开放平台已选择：事件与回调 -> 使用长连接接收事件，并订阅 im.message.receive_v1")

    # 启动看门狗后台线程
    watchdog_thread = threading.Thread(target=run_watchdog, daemon=True)
    watchdog_thread.start()
    logger.info("看门狗线程已启动")

    # ===== 启动时拉取最近3天的历史消息 =====
    try:
        from feishu_bot.history_fetcher import fetch_and_push_history

        # 获取机器人所在的群列表
        from lark_oapi.api.im_v1 import ListUserGroupRequest
        group_request = ListUserGroupRequest.builder().page_size(50).build()
        group_response = api_client.im.v1.chat.list(group_request)

        if group_response.success() and group_response.data and group_response.data.items:
            for chat in group_response.data.items:
                chat_id = chat.chat_id
                chat_name = chat.name or ""
                logger.info("拉取群[%s]历史消息...", chat_name)
                fetch_and_push_history(
                    api_client,
                    chat_id,
                    chat_name,
                    settings.aistock_api_url,
                    settings.internal_token,
                    days=3,
                )
        else:
            logger.warning("获取群列表失败: %s", group_response.msg if not group_response.success() else "无群")
    except Exception:
        logger.exception("启动时拉取历史消息失败，继续启动 WebSocket")

    logger.info("历史消息拉取完成，启动 WebSocket 长连接...")

    ws_client = WsClient(
        settings.app_id,
        settings.app_secret,
        event_handler=event_handler,
        log_level=lark_log_level,
    )
    # start() 会阻塞主线程；连接成功后控制台会出现 connected to wss://...
    ws_client.start()


if __name__ == "__main__":
    main()
