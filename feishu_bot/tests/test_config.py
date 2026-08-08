import os
from feishu_bot.config import Settings


def _clear():
    for k in ("QQ_ENABLE", "ONEBOT_WS_HOST", "ONEBOT_WS_PORT",
              "QQ_GROUP_ID", "QQ_HISTORY_DAYS", "QQ_FILE_POLL_INTERVAL",
              "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        os.environ.pop(k, None)


def test_qq_defaults():
    _clear()
    os.environ["FEISHU_APP_ID"] = "cli_test"
    os.environ["FEISHU_APP_SECRET"] = "secret"
    s = Settings.from_env()
    assert s.qq_enable is False
    assert s.onebot_ws_host == "0.0.0.0"
    assert s.onebot_ws_port == 8081
    assert s.qq_group_id == 0
    assert s.qq_history_days == 7
    assert s.qq_file_poll_interval == 900


def test_qq_env_override():
    _clear()
    os.environ["FEISHU_APP_ID"] = "cli_test"
    os.environ["FEISHU_APP_SECRET"] = "secret"
    os.environ["QQ_ENABLE"] = "true"
    os.environ["ONEBOT_WS_HOST"] = "127.0.0.1"
    os.environ["ONEBOT_WS_PORT"] = "9090"
    os.environ["QQ_GROUP_ID"] = "123456789"
    os.environ["QQ_HISTORY_DAYS"] = "3"
    os.environ["QQ_FILE_POLL_INTERVAL"] = "600"
    s = Settings.from_env()
    assert s.qq_enable is True
    assert s.onebot_ws_host == "127.0.0.1"
    assert s.onebot_ws_port == 9090
    assert s.qq_group_id == 123456789
    assert s.qq_history_days == 3
    assert s.qq_file_poll_interval == 600


def test_qq_only_mode_without_feishu_creds():
    """QQ 模式不依赖飞书凭据（Docker 部署只配 QQ 相关变量）。"""
    _clear()
    os.environ["QQ_ENABLE"] = "true"
    s = Settings.from_env()
    assert s.qq_enable is True
    assert s.app_id == ""


def test_feishu_mode_requires_creds():
    _clear()
    os.environ["QQ_ENABLE"] = "false"
    try:
        Settings.from_env()
        assert False, "应当抛出 RuntimeError"
    except RuntimeError:
        pass
