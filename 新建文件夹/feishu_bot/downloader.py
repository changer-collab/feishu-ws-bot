import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TOKEN_CACHE = {"token": None, "expire_at": 0.0}


def _safe_filename(name: str, default_ext: str = "pdf") -> str:
    name = (name or f"download.{default_ext}").strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    return name[:180] or f"download.{default_ext}"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名: {path}")


def _is_image_file(file_name: str) -> bool:
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".svg"}
    ext = Path(file_name).suffix.lower()
    return ext in image_exts


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    now = time.time()
    if _TOKEN_CACHE["token"] and now < float(_TOKEN_CACHE["expire_at"]):
        return str(_TOKEN_CACHE["token"])

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

    token = data["tenant_access_token"]
    expire = int(data.get("expire", 7200))
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expire_at"] = now + max(60, expire - 300)
    return token


def download_message_resource(
    *,
    app_id: str,
    app_secret: str,
    message_id: str,
    file_key: str,
    file_name: str,
    save_dir: str,
    resource_type: str = "file",
    allowed_types: Optional[set] = None,
) -> Optional[Path]:
    """下载用户发到机器人可见消息里的资源文件。
    
    Args:
        resource_type: "file" 或 "image"
        allowed_types: 允许的文件类型集合，例如 {"pdf"} 或 {"jpg", "jpeg", "png"}
    """
    default_ext = "pdf" if resource_type == "file" else "jpg"
    file_name = _safe_filename(file_name or f"{file_key}.{default_ext}", default_ext=default_ext)
    
    if allowed_types:
        ext = Path(file_name).suffix.lower().lstrip(".")
        if ext not in allowed_types:
            logger.info("文件类型不在允许列表中，跳过下载: %s (允许: %s)", file_name, allowed_types)
            return None

    token = get_tenant_access_token(app_id, app_secret)
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
    resp = requests.get(
        url,
        params={"type": resource_type},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        stream=True,
    )

    if resp.status_code != 200:
        text = resp.text[:1000]
        raise RuntimeError(f"下载失败 status={resp.status_code}, body={text}")

    content_type = resp.headers.get("Content-Type", "")
    if content_type and "json" in content_type.lower():
        # 飞书接口异常时有时返回 JSON 错误体。
        raise RuntimeError(f"下载接口返回 JSON，可能是权限或 file_key 不匹配: {resp.text[:1000]}")

    out_dir = Path(save_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _unique_path(out_dir / file_name)

    with out_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    logger.info("%s 已下载: %s", resource_type.upper(), out_path)
    return out_path
