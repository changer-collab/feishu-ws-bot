"""
feishu-bot 看门狗：检测 WebSocket 假死并强制退出进程，定期清理下载文件。

工作原理：
1. 读取心跳文件，检查最后活动时间
2. 如果超过阈值（默认30分钟）无活动，且当前在交易时段，则 sys.exit(1)
3. pm2 的 autorestart 会自动重启进程
4. 每天凌晨清理 downloads 文件夹中3天前的图片

交易时段定义（周一到周五）：
- 上午: 09:15-11:35
- 下午: 12:55-15:10
- 非交易时段不检测（周末、节假日、夜间）
"""

import logging
import os
import sys
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = "feishu_bot.heartbeat"
STALE_THRESHOLD_MINUTES = 30
DOWNLOADS_DIR = "downloads"
CLEANUP_DAYS = 3  # 清理3天前的文件


def _is_trading_hours(now: datetime) -> bool:
    """判断当前是否在 A 股交易时段附近（含盘前盘后缓冲）"""
    # 周末不检测
    if now.weekday() >= 5:
        return False
    t = now.time()
    # 上午 09:10-11:40
    if dt_time(9, 10) <= t <= dt_time(11, 40):
        return True
    # 下午 12:50-15:15
    if dt_time(12, 50) <= t <= dt_time(15, 15):
        return True
    return False


def check_heartbeat() -> bool:
    """检查心跳是否过期。返回 True 表示健康，False 表示需要重启。"""
    if not os.path.exists(HEARTBEAT_FILE):
        # 心跳文件不存在，可能是首次启动，视为健康
        return True

    try:
        with open(HEARTBEAT_FILE, "r") as f:
            ts_str = f.read().strip()
        last_active = datetime.fromisoformat(ts_str)
    except (ValueError, OSError):
        logger.warning("心跳文件读取失败，跳过检查")
        return True

    now = datetime.now()
    elapsed_minutes = (now - last_active).total_seconds() / 60

    if elapsed_minutes > STALE_THRESHOLD_MINUTES:
        logger.error(
            "心跳过期: 最后活动 %s，已过 %.0f 分钟（阈值 %d 分钟）",
            last_active.isoformat(),
            elapsed_minutes,
            STALE_THRESHOLD_MINUTES,
        )
        return False

    return True


def check_heartbeat_loose() -> bool:
    """非交易时段心跳检查（宽松阈值：2小时）"""
    if not os.path.exists(HEARTBEAT_FILE):
        return True

    try:
        with open(HEARTBEAT_FILE, "r") as f:
            ts_str = f.read().strip()
        last_active = datetime.fromisoformat(ts_str)
    except (ValueError, OSError):
        return True

    now = datetime.now()
    elapsed_minutes = (now - last_active).total_seconds() / 60

    if elapsed_minutes > 120:  # 非交易时段2小时阈值
        logger.error(
            "非交易时段心跳过期: 最后活动 %s，已过 %.0f 分钟（阈值 120 分钟）",
            last_active.isoformat(),
            elapsed_minutes,
        )
        return False

    return True


def cleanup_old_downloads() -> int:
    """
    清理 downloads 文件夹中超过 CLEANUP_DAYS 天的文件。

    Returns:
        删除的文件数量
    """
    downloads_path = Path(DOWNLOADS_DIR)
    if not downloads_path.exists():
        logger.info("downloads 文件夹不存在，跳过清理")
        return 0

    cutoff_date = datetime.now() - timedelta(days=CLEANUP_DAYS)
    deleted_count = 0

    try:
        for file_path in downloads_path.iterdir():
            if not file_path.is_file():
                continue

            # 方法1：从文件名提取日期（前8位：YYYYMMDD）
            filename = file_path.name
            if len(filename) >= 8 and filename[:8].isdigit():
                try:
                    file_date = datetime.strptime(filename[:8], "%Y%m%d")
                    if file_date < cutoff_date:
                        logger.info("删除旧文件（文件名日期）：%s（%s）", filename, file_date.strftime("%Y-%m-%d"))
                        file_path.unlink()
                        deleted_count += 1
                        continue
                except ValueError:
                    pass  # 文件名日期格式错误，继续用创建时间判断

            # 方法2：使用文件创建时间（修改时间）
            file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            if file_mtime < cutoff_date:
                logger.info("删除旧文件（修改时间）：%s（%s）", filename, file_mtime.strftime("%Y-%m-%d"))
                file_path.unlink()
                deleted_count += 1

        if deleted_count > 0:
            logger.info("downloads 清理完成：删除 %d 个文件，保留 %d 个", deleted_count, len(list(downloads_path.iterdir())))
        else:
            logger.debug("downloads 清理完成：无旧文件需要删除")

    except Exception:
        logger.exception("downloads 清理异常")

    return deleted_count


def should_cleanup() -> bool:
    """
    判断是否应该执行清理（每天凌晨一次）。

    Returns:
        True 表示应该清理
    """
    now = datetime.now()
    # 每天凌晨 02:00-02:10 执行清理
    return dt_time(2, 0) <= now.time() <= dt_time(2, 10)


def run_watchdog():
    """看门狗主循环，每5分钟检查一次（全天候），并定期清理旧文件"""
    import time

    logger.info("看门狗启动（全天候模式），检查间隔=5分钟，过期阈值=%d分钟", STALE_THRESHOLD_MINUTES)
    logger.info("文件清理：每天凌晨02:00清理%d天前的downloads文件", CLEANUP_DAYS)

    cleanup_done_today = False  # 标记当天是否已清理

    while True:
        time.sleep(300)  # 5分钟检查一次
        now = datetime.now()

        # 重置清理标记（过了02:10后）
        if now.time() > dt_time(2, 10):
            cleanup_done_today = False

        # 执行清理（凌晨02:00-02:10）
        if should_cleanup() and not cleanup_done_today:
            logger.info("开始执行 downloads 文件清理...")
            cleanup_old_downloads()
            cleanup_done_today = True

        # 非交易时段使用更宽松的阈值（2小时）
        if not _is_trading_hours(now):
            if not check_heartbeat_loose():
                logger.error("非交易时段心跳过期，强制退出进程！")
                sys.exit(1)
            continue

        # 交易时段使用严格阈值（30分钟）
        if not check_heartbeat():
            logger.error("交易时段WebSocket假死检测触发，强制退出进程！")
            sys.exit(1)
