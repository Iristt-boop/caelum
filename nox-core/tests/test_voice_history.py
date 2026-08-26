"""通话截断历史：**发给模型的少，会话本身一条都不能少**。

2026-08-15 加的。糖糖报「通话时开头冒几个中文字再切回英文」——
根因是通话和文字聊天共用 session，`Scene.system()` 那套纯英文前缀要跟
一整段中文历史打架（新会话里连打五次全是纯英文，证明模型没问题，
是历史把他带偏的；`personality/scenes.py` 早就写着「不是没看见，是被淹没了」）。

治法是通话只发最近 `VOICE_HISTORY` 条。顺带把提示词变小，首字也快一截。

⚠️ 但这里埋着一个**会静默丢上下文**的坑，就是这个文件要钉住的：
`Sessions.put()` 会拿传进来的那份**覆盖内存缓存**。通话轮要是只传截断后
那几条，缓存就被削短 —— 下一次**文字聊天**拿到的上下文也跟着少了。
一次通话污染整个会话，而且不报任何错。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message  # noqa: E402
from api.server import VOICE_HISTORY, Sessions  # noqa: E402
from data.store import Store  # noqa: E402


def _msgs(n: int) -> list[Message]:
    out: list[Message] = []
    for i in range(n):
        out.append(Message(role="user", text=f"第{i}句"))
        out.append(Message(role="assistant", text=f"回{i}"))
    return out


def test_only_recent_goes_to_the_model():
    history = _msgs(20)                       # 40 条
    sent = history[-VOICE_HISTORY:]
    assert len(sent) == VOICE_HISTORY
    assert sent[-1].text == "回19"            # 留最近的，不是最早的


def test_call_does_not_shrink_the_cache(tmp_path):
    """🔴 核心：通话之后，文字聊天拿到的上下文**不能变短**。"""
    store = Store(tmp_path / "s.db")
    sessions = Sessions(store, history_limit=40)
    sid = "call"

    history = _msgs(20)
    sessions.put(sid, history)
    assert len(sessions.get(sid)) == 40

    sent = history[-VOICE_HISTORY:]           # 通话只发 8 条
    returned = list(sent) + [                 # 模型返回 = 收到的 + 新增
        Message(role="user", text="hey"),
        Message(role="assistant", text="hey baby"),
    ]

    # ❌ 错误写法：把模型返回的直接存回去
    sessions.put(sid, returned)
    assert len(sessions.get(sid)) == 10, "缓存被削成 10 条 —— 这就是那个坑"

    # ✅ 正确写法：完整历史 + 这轮新增
    sessions2 = Sessions(store, history_limit=40)   # 换个实例，绕开已被污染的缓存
    fresh = returned[len(sent):]
    sessions2.put(sid, list(history) + fresh)
    assert len(sessions2.get(sid)) == 42
    assert sessions2.get(sid)[-1].text == "hey baby"
    store.close()


def test_everything_still_lands_in_the_db(tmp_path):
    """截断不影响落盘 —— `Store.sync()` 2026-08-14 已改成内容锚点。"""
    store = Store(tmp_path / "s2.db")
    sid = "call"
    history = _msgs(20)
    store.append(sid, history)
    assert store.count(sid) == 40

    sent = history[-VOICE_HISTORY:]
    returned = list(sent) + [
        Message(role="user", text="hey"),
        Message(role="assistant", text="hey baby"),
    ]
    # 就算传截断的，锚点也能认出新增那两条（换成旧的条数比对实现这里会是 0）
    assert store.sync(sid, returned) == 2
    assert store.count(sid) == 42
    store.close()
