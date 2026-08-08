import asyncio
import logging
import threading

import lark_oapi as lark
from lark_oapi.ws import Client as WsClient

from feishu_bot.config import Settings
from feishu_bot.handlers import build_message_handler
from feishu_bot.watchdog import run_watchdog

logger = logging.getLogger("feishu_ws_bot")  # 模块级，供 run_qq_main / run_qq_runtime 使用


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def run_qq_runtime(settings) -> None:
    from feishu_bot.onebot.group_files import poll_loop, run_backfill
    from feishu_bot.onebot.handlers import QqEventHandler
    from feishu_bot.onebot.onebot_client import OneBotServer
    from feishu_bot.onebot.state import FileIdState

    if not settings.qq_group_id:
        raise RuntimeError("QQ_ENABLE=true 但未配置 QQ_GROUP_ID")

    server = OneBotServer(settings.onebot_ws_host, settings.onebot_ws_port)
    state = FileIdState(settings.qq_state_path)
    handler = QqEventHandler(settings, state, server)
    server.on("message", handler.on_message)
    server.on("notice", handler.on_notice)
    server.on("meta_event", handler.on_meta_event)  # 心跳 → 防看门狗误杀

    server_task = asyncio.create_task(server.serve_forever())

    # 等待 NapCat 连接；未连接则提示检查反向 WS 配置（不永久挂起）
    try:
        await asyncio.wait_for(server.wait_connected(), timeout=120)
    except asyncio.TimeoutError:
        logger.error("120 秒内 NapCat 未连接，请检查 NapCat WebUI 的反向 WebSocket 配置")
        raise

    # 启动时补拉最近 N 天群文件（对齐飞书 history_fetcher 行为）
    await run_backfill(server, settings, state, handler)

    stop_event = asyncio.Event()
    poll_task = asyncio.create_task(
        poll_loop(server, settings, state, handler, stop_event))
    try:
        await server_task
    finally:
        stop_event.set()
        poll_task.cancel()


def run_qq_main(settings) -> None:
    """QQ/OneBot 捕获模式入口（阻塞运行）。"""
    from feishu_bot.watchdog import run_watchdog

    # 未配置群号时先失败，避免启动看门狗线程后才报错
    if not settings.qq_group_id:
        raise RuntimeError("QQ_ENABLE=true 但未配置 QQ_GROUP_ID")

    watchdog_thread = threading.Thread(target=run_watchdog, daemon=True)
    watchdog_thread.start()
    logger.info("启动 QQ/OneBot 捕获机器人: 监听 %s:%s, 目标群 %s",
                settings.onebot_ws_host, settings.onebot_ws_port,
                settings.qq_group_id)
    asyncio.run(run_qq_runtime(settings))


def main() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)

    # QQ 模式为主入口（互斥）：QQ_ENABLE=true 时只跑 QQ 捕获，飞书侧不启动
    if settings.qq_enable:
        run_qq_main(settings)
        return

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
        from lark_oapi.api.im.v1 import ListChatRequest
        group_request = ListChatRequest.builder().page_size(50).build()
        group_response = api_client.im.v1.chat.list(group_request)

        if group_response.success() and group_response.data and group_response.data.items:
            # 只拉取监控群聊的历史消息
            for chat in group_response.data.items:
                chat_id = chat.chat_id
                chat_name = chat.name or ""
                # 只处理配置的监控群聊
                if settings.monitor_chat_name and chat_name == settings.monitor_chat_name:
                    logger.info("拉取监控群[%s]历史消息...", chat_name)
                    fetch_and_push_history(
                        api_client,
                        chat_id,
                        chat_name,
                        settings.aistock_api_url,
                        settings.internal_token,
                        days=3,
                    )
                    break  # 找到目标群后跳出循环
            if settings.monitor_chat_name:
                logger.info("仅拉取监控群[%s]的历史消息，忽略其他群", settings.monitor_chat_name)
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
