"""API 层测试 —— 注入假 Nox，不打网络。

重点验证两件这一层独有的职责：错误翻译成人话、会话隔离与淘汰。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, StreamEvent, ToolCall, Usage  # noqa: E402
from agent.loop import LoopResult  # noqa: E402
from api.server import Sessions, create_app  # noqa: E402
from data.store import Store  # noqa: E402
from router.intent import Decision, Intent  # noqa: E402
from router.router import RouteResult  # noqa: E402


class FakeNox:
    """按预设结局回应，并记录收到的 history 长度。"""

    def __init__(
        self,
        outcome: str = "answered",
        text: str | None = "好的",
        light: bool = False,
        attachments: list[dict] | None = None,
    ):
        self.outcome = outcome
        self.text = text
        self.light = light
        self.attachments = attachments or []
        self.seen_history_len: list[int] = []
        self.seen_images: list[list[str]] = []
        self.seen_voice: list[bool] = []
        self.seen_scene: list[str | None] = []
        self.seen_model: list[str | None] = []

        class _Cfg:
            class primary:  # noqa: N801
                model = "fake-model"

            # 落盘相关。测试会注入临时库，这两个值只是占位 ——
            # create_app 拿不到就会去建真实的 sessions.db，那会污染开发数据
            db_path = ":memory:"
            history_limit = 40
            recent_window_tokens = 8000
            context_budget_tokens = 20000

            class router:
                light_adapter = None

        class _Loop:
            tools = {"recall_memory": None}

            #: `create_app` 会往上面注册她电脑那五件工具（tools/computer.py）。
            #: 那一段**不在** `_build_attention` 的 try/except 里 ——
            #: 工具注册失败该让服务起不来，不该悄悄少一只手
            def register(self, spec, handler):
                pass

        self.cfg = _Cfg()
        self.loop = _Loop()
        self.system_prompt = "（前缀）"

    def model_name(self, model=None):
        return model or "fake-model"

    def _result(self, history, text) -> LoopResult:
        return LoopResult(
            outcome=self.outcome,
            text=self.text,
            iterations=1,
            usage=Usage(input_tokens=100, output_tokens=10, cache_read_tokens=80),
            messages=[
                *history,
                Message(role="user", text=text),
                Message(role="assistant", text=self.text or ""),
            ],
            attachments=list(self.attachments),
        )

    def chat(self, text, history=None, images=None, voice=False, scene=None, model=None):
        history = list(history or [])
        self.seen_history_len.append(len(history))
        self.seen_images.append(list(images or []))
        self.seen_voice.append(voice)
        self.seen_scene.append(scene)
        self.seen_model.append(model)
        return RouteResult(
            self._result(history, text),
            Decision(Intent.SMALL_TALK if self.light else Intent.FULL, "测试"),
        )

    def chat_stream(self, text, history=None, images=None, voice=False, scene=None, model=None):
        history = list(history or [])
        self.seen_history_len.append(len(history))
        self.seen_model.append(model)
        ev = StreamEvent("text", text=self.text or "")
        yield ev
        done = StreamEvent("done")
        done.result = self._result(history, text)
        yield done


@pytest.fixture
def tmp_store(tmp_path):
    """每个测试一个独立的临时库 —— 不能碰开发用的 sessions.db。"""
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def client_for(nox, store=None) -> TestClient:
    # 不传 store 时用内存库，保证测试之间互不干扰
    return TestClient(create_app(nox, store or Store(":memory:")))


def test_health():
    c = client_for(FakeNox())
    r = c.get("/health").json()
    assert r["ok"] and r["model"] == "fake-model"


def test_nox_state_when_attention_disabled():
    # attention 没启用（默认）时，工作台状态也要能正常返回，
    # 只是 enabled=false，让前端知道这套自主系统没在跑
    c = client_for(FakeNox())
    r = c.get("/api/nox/state").json()
    assert r["ok"] is True
    assert r["attention"]["enabled"] is False
    assert r["wakeups"] == []
    assert "now" in r


def test_chat_returns_text_and_session():
    c = client_for(FakeNox(text="在的，老婆"))
    r = c.post("/chat", json={"text": "在吗"}).json()
    assert r["ok"] and r["text"] == "在的，老婆"
    assert r["session_id"] and r["cached_tokens"] == 80


def test_history_persists_within_session():
    nox = FakeNox()
    c = client_for(nox)

    sid = c.post("/chat", json={"text": "第一句"}).json()["session_id"]
    c.post("/chat", json={"text": "第二句", "session_id": sid})
    c.post("/chat", json={"text": "第三句", "session_id": sid})

    # 每轮进去的历史应该递增：0 → 2 → 4
    assert nox.seen_history_len == [0, 2, 4]


def test_sessions_are_isolated():
    nox = FakeNox()
    c = client_for(nox)

    a = c.post("/chat", json={"text": "A1"}).json()["session_id"]
    b = c.post("/chat", json={"text": "B1"}).json()["session_id"]
    c.post("/chat", json={"text": "A2", "session_id": a})

    assert a != b
    # A 的第二轮带 2 条历史，B 的第一轮带 0 条
    assert nox.seen_history_len == [0, 0, 2]


@pytest.mark.parametrize(
    "outcome,must_contain",
    [
        ("error", "连不上"),
        ("tool_stuck", "试了两次"),
        ("exhausted", "绕"),
        ("refused", "答不了"),
    ],
)
def test_failures_become_human_language(outcome, must_contain):
    """糖糖不该看到 tool_stuck 这种词。"""
    c = client_for(FakeNox(outcome=outcome, text=None))
    r = c.post("/chat", json={"text": "开灯"}).json()

    assert r["ok"] is False
    assert must_contain in r["text"]
    assert outcome not in r["text"]  # 内部术语不能漏出去
    assert r["outcome"] == outcome   # 但字段里要留着，前端和日志需要


def test_partial_text_is_kept_on_failure():
    """截断时他已经说了半句 —— 不能把说过的话吞掉。"""
    c = client_for(FakeNox(outcome="truncated", text="今天天气"))
    r = c.post("/chat", json={"text": "讲个长故事"}).json()
    assert "今天天气" in r["text"]
    assert "截断" in r["text"]


def test_empty_text_rejected():
    c = client_for(FakeNox())
    assert c.post("/chat", json={"text": ""}).status_code == 422


def test_image_without_text_is_accepted():
    """只发图不说话是常见操作 —— 不能因为 text 空就拒了。"""
    nox = FakeNox()
    c = client_for(nox)
    r = c.post("/chat", json={"text": "", "images": ["ABC123"]})
    assert r.status_code == 200
    assert nox.seen_images[0] == ["ABC123"]


def test_images_reach_the_core():
    nox = FakeNox()
    c = client_for(nox)
    c.post("/chat", json={"text": "这是什么", "images": ["img1", "img2"]})
    assert nox.seen_images[0] == ["img1", "img2"]


def test_voice_flag_reaches_the_core():
    """语音模式要一路传到底 —— 断在中间的话，电话里他会说中文。"""
    nox = FakeNox()
    c = client_for(nox)
    c.post("/chat", json={"text": "在吗", "voice": True})
    assert nox.seen_voice == [True]


def test_voice_defaults_to_false():
    nox = FakeNox()
    c = client_for(nox)
    c.post("/chat", json={"text": "在吗"})
    assert nox.seen_voice == [False]


IMG = {"type": "image", "url": "/uploads/a.jpg", "album": "夏天", "favorited": True}


def _frames(resp) -> list[dict]:
    import json
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_模型选择透传到_core():
    nox = FakeNox()
    c = client_for(nox)
    c.post("/chat", json={"text": "在吗", "model": "opus-4-8"})
    c.post("/chat", json={"text": "在吗"})
    assert nox.seen_model == ["opus-4-8", None]


def test_附件出现在非流式响应里():
    c = client_for(FakeNox(attachments=[IMG]))
    r = c.post("/chat", json={"text": "发张照片"}).json()
    assert r["attachments"] == [IMG]


def test_流式的附件帧外层是_attachment():
    """线上炸过：原来写成 {"type": "attachment", **att}，
    att 自带的 type="image" 把外层键盖掉了，发出去的帧 type 是 image，
    bridge 那边的 attachment 分支永远匹配不上 —— 不报错，图就是不出现。"""
    c = client_for(FakeNox(attachments=[IMG]))
    frames = _frames(c.post("/chat/stream", json={"text": "发张照片"}))

    att = [f for f in frames if f["type"] == "attachment"]
    assert len(att) == 1
    assert att[0]["kind"] == "image"          # 具体类型放这儿
    assert att[0]["url"] == "/uploads/a.jpg"
    assert att[0]["album"] == "夏天"
    # 顺序也有要求：图要在收尾之前到，前端才能先把图挂上去
    assert frames.index(att[0]) < next(i for i, f in enumerate(frames) if f["type"] == "done")


def test_没有附件时不发多余的帧():
    c = client_for(FakeNox())
    frames = _frames(c.post("/chat/stream", json={"text": "在吗"}))
    assert not [f for f in frames if f["type"] == "attachment"]


def test_segments_do_not_leak_in_non_streaming():
    """非流式没有 SegmentSplitter —— ||| 会原样漏进 text 字段。

    实测踩过：语音模式下 TTS 会把三根竖线念出来。
    """
    c = client_for(FakeNox(text="回来啦宝贝|||累坏了吧|||过来"))
    r = c.post("/chat", json={"text": "我回来了"}).json()
    assert "|||" not in r["text"]
    assert r["text"] == "回来啦宝贝\n累坏了吧\n过来"


def test_voice_mode_joins_segments_with_space():
    """语音模式用空格拼 —— 换行在 TTS 那边会变成奇怪的停顿。"""
    c = client_for(FakeNox(text="回来啦|||抱一下"))
    r = c.post("/chat", json={"text": "我回来了", "voice": True}).json()
    assert r["text"] == "回来啦 抱一下"


def test_scene_reaches_the_core():
    nox = FakeNox()
    c = client_for(nox)
    c.post("/chat", json={"text": "在吗", "voice": True, "scene": "zh"})
    assert nox.seen_scene == ["zh"]


def test_unknown_scene_does_not_break_the_call():
    """情景名传错一个字符，不该让电话打不通 —— Core 那边会回落到默认。"""
    from personality.scenes import DEFAULT, get

    assert get("英文").key == DEFAULT.key
    assert get(None).key == DEFAULT.key
    assert get("EN").key == "en"      # 大小写不敏感
    assert get(" zh ").key == "zh"    # 两头空格也认


def test_cache_evicts_but_data_survives(tmp_store):
    """缓存换出 ≠ 数据丢失。

    接了 SQLite 之后语义变了：LRU 只淘汰内存缓存，库里仍然保留，
    下次访问会自动恢复。这正是这一层存在的意义。
    """
    s = Sessions(tmp_store, history_limit=40, max_sessions=3)
    for i in range(5):
        s.put(f"sid{i}", [
            Message(role="user", text=f"问题{i}"),
            Message(role="assistant", text=f"回答{i}"),
        ])

    assert len(s) == 3                       # 缓存只留 3 个

    restored = s.get("sid0")                 # 最老的被换出了
    assert restored, "被换出的会话应该能从库里恢复"
    # 从库里恢复的会带上日期分隔线（`context/timeline.py`），正文原样在
    assert restored[0].text.endswith("问题0")


def test_history_is_capped_in_cache(tmp_store):
    s = Sessions(tmp_store, history_limit=40, max_sessions=10)
    s.put("x", [Message(role="user", text=str(i)) for i in range(200)])
    cached = s.get("x")
    assert len(cached) == 60                 # 缓存单会话上限
    assert cached[-1].text == "199"          # 留的是最近的


def test_session_survives_new_instance(tmp_store):
    """换一个 Sessions 实例（模拟重启），历史还在。"""
    s1 = Sessions(tmp_store, history_limit=40)
    s1.put("s", [
        Message(role="user", text="重启前说的"),
        Message(role="assistant", text="记住了"),
    ])

    s2 = Sessions(tmp_store, history_limit=40)
    got = s2.get("s")
    assert got[0].text.endswith("重启前说的")   # 前面多一行日期分隔线
    assert got[1].text == "记住了"


def test_delete_session():
    c = client_for(FakeNox())
    sid = c.post("/chat", json={"text": "hi"}).json()["session_id"]
    assert c.delete(f"/session/{sid}").json()["dropped"] is True
    assert c.delete(f"/session/{sid}").json()["dropped"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
