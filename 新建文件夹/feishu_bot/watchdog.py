"""
feishu-bot 看门狗：检测 WebSocket 假死并强制退出进程。

工作原理：
1. 读取心跳文件，检查最后活动时间
2. 如果超过阈值（默认30分钟）无活动，且当前在交易时段，则 sys.exit(1)
3. pm2 的 autorestart 会自动重启进程

交易时段定义（周一到周五）：
- 上午: 09:15-11:35
- 下午: 12:55-15:10
- 非交易时段不检测（周末、节假日、夜间）
"""

import logging
import os
import sys
from datetime import datetime, time as dt_time

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = "feishu_bot.heartbeat"
STALE_THRESHOLD_MINUTES = 30


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


def run_watchdog():
    """看门狗主循环，每5分钟检查一次"""
    import time
    logger.info("看门狗启动，检查间隔=5分钟，过期阈值=%d分钟", STALE_THRESHOLD_MINUTES)
    while True:
        time.sleep(300)  # 5分钟检查一次
        now = datetime.now()
        if not _is_trading_hours(now):
            continue
        if not check_heartbeat():
            logger.error("WebSocket 假死检测触发，强制退出进程！")
            sys.exit(1)
