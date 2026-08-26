"""`_tools_this_turn` —— 工具调用只报这一轮的。

糖糖 2026-08-05 报的 bug：前端工具卡片一轮轮累加，已经累到 11 个。
根因在这里：`result.messages` 是完整会话，不是这轮的增量。
"""

from __future__ import annotations

from types import SimpleNamespace

from api.server import _tools_this_turn


def _call(name):
    return SimpleNamespace(name=name)


def _msg(role, tools=()):
    return SimpleNamespace(role=role, tool_calls=[_call(t) for t in tools])


def test_only_this_turn_not_whole_session():
    history = [
        _msg("user"),
        _msg("assistant", ["add_todo"]),
        _msg("user"),
        _msg("assistant", ["send_meme", "recall_memory"]),
    ]
    messages = history + [_msg("user"), _msg("assistant", ["complete_todo"])]
    assert _tools_this_turn(messages, history) == ["complete_todo"]


def test_no_tools_this_turn_returns_empty_even_if_earlier_turns_used_some():
    """上一轮用了工具、这一轮没用 —— 这轮的气泡不该顶着上轮的卡片。"""
    history = [_msg("user"), _msg("assistant", ["add_todo"])]
    messages = history + [_msg("user"), _msg("assistant")]
    assert _tools_this_turn(messages, history) == []


def test_multiple_tools_in_one_turn_all_reported():
    history = [_msg("user"), _msg("assistant")]
    messages = history + [
        _msg("user"),
        _msg("assistant", ["reading_current"]),
        _msg("assistant", ["reading_continue"]),
    ]
    assert _tools_this_turn(messages, history) == ["reading_current", "reading_continue"]


def test_first_turn_with_empty_history():
    messages = [_msg("user"), _msg("assistant", ["daily_summary"])]
    assert _tools_this_turn(messages, []) == ["daily_summary"]


def test_user_messages_never_contribute():
    history = []
    messages = [_msg("user", ["不该被算进去"]), _msg("assistant", ["add_todo"])]
    assert _tools_this_turn(messages, history) == ["add_todo"]
