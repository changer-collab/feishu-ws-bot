import pytest

from main import run_qq_main


def test_run_qq_main_requires_group_id(monkeypatch):
    class FakeSettings:
        qq_group_id = 0
        qq_enable = True

    with pytest.raises(RuntimeError, match="QQ_GROUP_ID"):
        run_qq_main(FakeSettings())
