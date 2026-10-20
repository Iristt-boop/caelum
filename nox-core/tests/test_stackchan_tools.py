"""stackchan 工具（他的身体）的测试。假 client，不打网络。

盯的是四件事：

- **失败要炸不要装**：gateway 挂了必须 raise，不许变成一句「说完了」
- **闸门接着**：动作类工具过 embodied 闸门，DENY 归还话术不 raise
- **看一眼不把 base64 泄进对话**，视觉模型挂了就明说看不见、**绝不编**
- **specs 的副作用都声明了**（漏声明 _EFFECTS 会在注册时炸，这里双保险）
"""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import stackchan as S  # noqa: E402


class FakeResult:
    def __init__(self, ok=True, text="", error=None):
        self.ok, self.text, self.error = ok, text, error


class FakeClient:
    def __init__(self, result=None):
        self.result = result or FakeResult(text="{}")
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args or {}))
        return self.result


# ---------------------------------------------------------------- specs


def test_六个工具都在_顺序固定():
    names = [s.name for s in (S.STATUS_SPEC, S.LOOK_SPEC, S.TURN_SPEC,
                              S.FACE_SPEC, S.SAY_SPEC, S.LED_SPEC)]
    assert names == [
        "stackchan_status", "stackchan_look", "stackchan_turn",
        "stackchan_face", "stackchan_say", "stackchan_led",
    ]


def test_副作用都声明了():
    reads = {S.STATUS_SPEC.name, S.LOOK_SPEC.name}
    for spec in (S.STATUS_SPEC, S.LOOK_SPEC, S.TURN_SPEC,
                 S.FACE_SPEC, S.SAY_SPEC, S.LED_SPEC):
        expected = "read" if spec.name in reads else "write"
        assert spec.side_effect == expected, f"{spec.name} 的 side_effect 不对"


def test_表情集跟gateway对齐():
    assert S.FACES == ("idle", "happy", "thinking", "sad",
                       "surprised", "embarrassed", "off")


# ---------------------------------------------------------------- handlers


def test_转头_参数原样传():
    c = FakeClient(FakeResult(text='{"ok": true}'))
    h = S.make_handlers(c)
    h["stackchan_turn"]({"yaw": -20, "pitch": 45})
    assert c.calls == [("move_head", {"yaw": -20, "pitch": 45})]


def test_失败要炸_不许装没事():
    c = FakeClient(FakeResult(ok=False, error="not connected"))
    h = S.make_handlers(c)
    with pytest.raises(RuntimeError):
        h["stackchan_say"]({"text": "嗨"})


def test_led_不带颜色_交还自动配色():
    c = FakeClient(FakeResult(text="{}"))
    h = S.make_handlers(c)
    h["stackchan_led"]({})
    assert c.calls == [("leds_auto", {})]


def test_led_带颜色_定色():
    c = FakeClient(FakeResult(text="{}"))
    h = S.make_handlers(c)
    h["stackchan_led"]({"r": 255, "g": 100, "b": 0})
    assert c.calls == [("set_all_leds", {"r": 255, "g": 100, "b": 0})]


def test_status_不在线_直说不在线():
    c = FakeClient(FakeResult(text=json.dumps({"esp32_connected": False})))
    h = S.make_handlers(c)
    assert "不在线" in h["stackchan_status"]({})


# ---------------------------------------------------------------- look


def _write_jpeg(tmp_path: Path) -> Path:
    p = tmp_path / "capture_1.jpg"
    p.write_bytes(base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
        "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
        "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="))
    return p


def test_look_照片换成转述_base64不进对话(tmp_path, monkeypatch):
    jpeg = _write_jpeg(tmp_path)
    c = FakeClient(FakeResult(text=json.dumps({"image_path": str(jpeg)})))
    h = S.make_handlers(c, vision_cfg=object())

    seen = {}
    fake_desc = "她坐在桌前，手里拿着一杯奶茶。"
    from agent import vision
    monkeypatch.setattr(vision, "describe",
                        lambda images, cfg: seen.update(images=images) or fake_desc)
    out = h["stackchan_look"]({"question": "她在干嘛"})

    assert c.calls[0][0] == "take_photo"
    assert seen["images"], "视觉模型必须收到图"
    assert "data:image/jpeg;base64," in seen["images"][0]
    assert fake_desc in out
    assert "转述" in out
    # base64 本体不许出现在给模型的话里
    assert base64.b64encode(jpeg.read_bytes()).decode()[:40] not in out


def test_look_视觉模型挂了_明说看不见_绝不编(tmp_path, monkeypatch):
    jpeg = _write_jpeg(tmp_path)
    c = FakeClient(FakeResult(text=json.dumps({"image_path": str(jpeg)})))
    h = S.make_handlers(c, vision_cfg=object())

    from agent import vision
    monkeypatch.setattr(vision, "describe", lambda images, cfg: None)
    out = h["stackchan_look"]({})
    assert "看不了" in out and "她在" not in out.replace("她在干嘛", "")


def test_look_没配视觉模型_不猜():
    c = FakeClient(FakeResult(text=json.dumps({"image_path": "/nonexistent.jpg"})))
    h = S.make_handlers(c, vision_cfg=None)
    out = h["stackchan_look"]({})
    assert "看不了" in out or "没配" in out


# ---------------------------------------------------------------- gate


def test_闸门拦下_话术归他_不raise():
    """把 stackchan 从 DEVICE_MODEL 里临时摘掉 → not_in_model DENY。"""
    from embodied.device_model import DEVICE_MODEL
    saved = DEVICE_MODEL.pop("stackchan")
    try:
        c = FakeClient(FakeResult(text="{}"))
        h = S.make_handlers(c)
        out = h["stackchan_say"]({"text": "嗨"})
        assert "说" not in c.calls, "被拦的动作不许真的发出去"
        assert out, "DENY 必须带人话"
    finally:
        DEVICE_MODEL["stackchan"] = saved
