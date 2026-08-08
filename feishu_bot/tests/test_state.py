from feishu_bot.onebot.state import FileIdState


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    state = FileIdState(path)
    assert state.is_duplicate("f1") is False
    state.mark("f1", "a.pdf", 100)
    assert state.is_duplicate("f1") is True
    assert state.is_duplicate_name_size("a.pdf", 100) is True
    assert state.is_duplicate_name_size("a.pdf", 200) is False

    # 重新加载，持久化生效
    state2 = FileIdState(path)
    assert state2.is_duplicate("f1") is True
    assert state2.is_duplicate_name_size("a.pdf", 100) is True


def test_mark_without_size_only_marks_id(tmp_path):
    state = FileIdState(str(tmp_path / "s2.json"))
    state.mark("f2", "b.pdf", None)
    assert state.is_duplicate("f2") is True
    assert state.is_duplicate_name_size("b.pdf", 100) is False
