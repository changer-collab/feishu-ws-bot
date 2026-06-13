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

    @classmethod
    def from_env(cls) -> "Settings":
        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        if not app_id or not app_secret:
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
        )
