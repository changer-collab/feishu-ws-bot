import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    return tuple(v.strip() for v in value.split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    log_level: str = "INFO"
    fetch_chat_info: bool = True
    download_pdf: bool = True
    download_image: bool = True
    download_dir: str = "downloads"
    classify_keywords: tuple[str, ...] = ("风口研报",)
    classify_subdir: str = "风口研报"
    env: str = "local"
    aistock_api_url: str = "https://gupiao-api.yaozhineng.com"
    internal_token: str = "crawler-int-2026-token"
    monitor_chat_name: str = ""
    enable_ocr: bool = True
    # QQ/OneBot 捕获配置（飞书停更后 QQ 群为来源）
    qq_enable: bool = False
    onebot_ws_host: str = "0.0.0.0"
    onebot_ws_port: int = 8081
    qq_group_id: int = 0
    qq_history_days: int = 7
    qq_file_poll_interval: int = 900
    qq_state_path: str = "qq_processed_ids.json"

    @classmethod
    def from_env(cls) -> "Settings":
        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        qq_enable = _env_bool("QQ_ENABLE", False)
        # QQ 模式不依赖飞书凭据；飞书模式仍强制校验
        if not qq_enable and (not app_id or not app_secret):
            raise RuntimeError(
                "缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET。请复制 .env.example 为 .env 并填写，"
                "或在华为云容器/主机环境变量中配置。"
            )
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            fetch_chat_info=_env_bool("FETCH_CHAT_INFO", True),
            download_pdf=_env_bool("DOWNLOAD_PDF", True),
            download_image=_env_bool("DOWNLOAD_IMAGE", True),
            download_dir=os.getenv("DOWNLOAD_DIR", "downloads").strip(),
            classify_keywords=_env_list("CLASSIFY_KEYWORDS", ("风口研报",)),
            classify_subdir=os.getenv("CLASSIFY_SUBDIR", "风口研报").strip(),
            env=os.getenv("ENV", "local").strip(),
            aistock_api_url=os.getenv("AISTOCK_API_URL", "https://gupiao-api.yaozhineng.com").strip().rstrip("/"),
            internal_token=os.getenv("INTERNAL_TOKEN", "crawler-int-2026-token").strip(),
            monitor_chat_name=os.getenv("MONITOR_CHAT_NAME", "").strip(),
            enable_ocr=_env_bool("ENABLE_OCR", True),
            qq_enable=qq_enable,
            onebot_ws_host=os.getenv("ONEBOT_WS_HOST", "0.0.0.0").strip() or "0.0.0.0",
            onebot_ws_port=int(os.getenv("ONEBOT_WS_PORT", "8081") or 8081),
            qq_group_id=int(os.getenv("QQ_GROUP_ID", "0") or 0),
            qq_history_days=int(os.getenv("QQ_HISTORY_DAYS", "7") or 7),
            qq_file_poll_interval=int(os.getenv("QQ_FILE_POLL_INTERVAL", "900") or 900),
            qq_state_path=os.getenv("QQ_STATE_PATH", "qq_processed_ids.json").strip()
            or "qq_processed_ids.json",
        )
