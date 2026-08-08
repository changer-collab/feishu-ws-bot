"""QQ 群文件遍历、启动补拉与定时轮询。

覆盖机器人离线期间遗漏的文件：启动时补拉最近 N 天，运行期定时比对已处理集合。
"""
import asyncio
import logging
import time
from typing import Optional

from .. import handlers as feishu_handlers

logger = logging.getLogger(__name__)


async def collect_group_files(server, group_id: int) -> list:
    """递归遍历群文件根目录与子文件夹，返回文件条目列表。"""
    entries: list = []
    visited: set = set()

    async def walk(folder_id: Optional[str]) -> None:
        key = folder_id or "root"
        if key in visited:
            return
        visited.add(key)
        if folder_id is None:
            resp = await server.call("get_group_root_files", {"group_id": group_id})
        else:
            resp = await server.call("get_group_files_by_folder",
                                     {"group_id": group_id, "folder_id": folder_id})
        data = resp.get("data") or {}
        for f in data.get("files") or []:
            entries.append({
                "file_id": str(f.get("file_id") or ""),
                "file_name": str(f.get("file_name") or ""),
                "upload_time": int(f.get("upload_time") or 0),
                "busid": f.get("busid"),
                "file_size": f.get("file_size"),
            })
        for folder in data.get("folders") or []:
            await walk(folder.get("folder_id"))

    await walk(None)
    return entries


def filter_new_files(entries: list, processed_ids,
                     min_upload_time: Optional[float] = None,
                     ext: str = ".pdf") -> list:
    """过滤出待处理的文件：未处理过、扩展名匹配、（可选）上传时间在窗口内。"""
    out = []
    for e in entries:
        if not e["file_id"] or e["file_id"] in processed_ids:
            continue
        if not e["file_name"].lower().endswith(ext):
            continue
        if min_upload_time is not None and e["upload_time"] < min_upload_time:
            continue
        out.append(e)
    return out


async def run_backfill(server, settings, state, handler) -> int:
    """启动补拉：最近 N 天群文件 → 逐一下载处理。返回处理数。

    collect 失败时 try/except 记日志返回 0，防止 NapCat 未就绪击穿启动流程。
    """
    try:
        entries = await collect_group_files(server, settings.qq_group_id)
    except Exception:
        logger.exception("启动补拉获取群文件列表失败（NapCat 可能未就绪）")
        return 0
    min_ts = None
    if settings.qq_history_days > 0:
        min_ts = time.time() - settings.qq_history_days * 86400
    candidates = filter_new_files(entries, state.ids, min_ts)
    done = 0
    for e in candidates:
        try:
            await handler.process_pdf(
                e["file_id"], e["file_name"],
                busid=e["busid"], size=e.get("file_size"))
            done += 1
        except Exception:
            logger.exception("补拉处理失败: %s", e["file_id"])
    logger.info("启动补拉完成: 候选 %d 个, 处理 %d 个", len(candidates), done)
    return done


async def poll_loop(server, settings, state, handler, stop_event) -> None:
    """定时轮询群文件列表，处理新增文件。"""
    while True:
        # 每轮先写心跳：轮询周期（默认 900s）小于 watchdog 30 分钟阈值，
        # 交易时段无群消息/文件事件时也能保持心跳文件新鲜，防止进程被误杀
        feishu_handlers._write_heartbeat()
        try:
            await asyncio.wait_for(stop_event.wait(),
                                   timeout=settings.qq_file_poll_interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            entries = await collect_group_files(server, settings.qq_group_id)
            for e in filter_new_files(entries, state.ids):
                await handler.process_pdf(
                    e["file_id"], e["file_name"],
                    busid=e["busid"], size=e.get("file_size"))
        except Exception:
            logger.exception("群文件轮询失败")
