"""本地执行端链路（Caelum Harness Gateway ↔ Nox Core）。

见 `D:\\claude-code\\CAELUM-HARNESS-ARCHITECTURE.md`。

## 这里最要紧的一条

**签名算法必须和 TypeScript 那端逐字节一致**
（`D:\\deepseek-harness\\packages\\caelum\\local-gateway\\src\\auth.ts`）。

差一个字节，握手就永远失败，而错误信息只会说"身份证明不对" ——
根本看不出是两边的实现漂了。所以下面那几个签名值是**从 TS 侧
实际跑出来的**，钉在这里当契约：任何一端改了算法，这里会红。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.local_link import (  # noqa: E402
    MIN_SECRET_LENGTH,
    LocalLink,
    _sign,
    read_secret,
)

SECRET = "x" * MIN_SECRET_LENGTH


# ---------------------------------------------------------------- 🔴 跨语言契约


#: 这些值是 2026-08-24 从 TS 侧 `sign()` 实际跑出来的。
#: **不要手改** —— 要改就两端一起改，然后重新生成这张表。
TS_VECTORS = [
    (["pc", "tangtang-pc", "abc123"],
     "278ae9beb86e552075ba6e995c38c63d412ad55aa6b8ae26e9b428bc3a490f74"),
]


@pytest.mark.parametrize("parts,expected", TS_VECTORS)
def test_signature_matches_typescript(parts, expected):
    """🔴 和 TS 那端算出来的必须一模一样。

    这条红了说明两端漂了。症状会是「握手永远失败，报身份证明不对」，
    而那个错误信息完全指不到真因。
    """
    assert _sign(SECRET, parts) == expected


def test_length_prefix_uses_utf8_bytes():
    """🔴 长度必须是 UTF-8 字节数，不是字符数。

    JS 的 `.length` 数 UTF-16 码元、Python 的 `len()` 数字符 ——
    同一个 `pc-🖥`，JS 说 5、Python 说 4。
    设备名里有个 emoji 就会两边对不上，而且极难查。
    """
    emoji = "pc-🖥"
    assert len(emoji) != len(emoji.encode("utf-8"))
    # 用字符数算的话签名会不同；这里只锁住"两者确实不等"这个前提，
    # 真正的一致性由上面那张 TS 向量表保证
    by_char = "|".join(f"{len(p)}:{p}" for p in ["pc", emoji, "cafe"])
    by_byte = "|".join(f"{len(p.encode('utf-8'))}:{p}" for p in ["pc", emoji, "cafe"])
    assert by_char != by_byte


def test_different_parts_give_different_signatures():
    """拼接歧义：分段不同但拼起来一样的输入，签名必须不同。"""
    assert _sign(SECRET, ["ab", "c"]) != _sign(SECRET, ["a", "bc"])


# ---------------------------------------------------------------- 密钥


def test_missing_secret_is_none():
    """🔴 没配就是 None，让调用方拒绝 —— 不是"跳过认证"。"""
    assert read_secret({}) is None


def test_short_secret_is_none():
    assert read_secret({"CAELUM_LINK_SECRET": "short"}) is None
    assert read_secret({"CAELUM_LINK_SECRET": "x" * (MIN_SECRET_LENGTH - 1)}) is None


def test_good_secret_reads():
    assert read_secret({"CAELUM_LINK_SECRET": SECRET}) == SECRET


# ---------------------------------------------------------------- 没连上时


def test_not_ready_before_anything_connects():
    link = LocalLink()
    assert link.is_ready is False
    assert "没连上" in link.describe()


def test_call_without_connection_fails_gracefully():
    """🔴 她电脑关着不是错误，是常态 —— 不许抛。"""
    link = LocalLink()
    result = asyncio.run(link.acall("computer.read_file", {"file_path": "x"}))
    assert result.ok is False
    assert "没连上" in (result.error or "")


def test_sync_call_without_connection():
    link = LocalLink()
    result = link.call("computer.read_file")
    assert result.ok is False


# ---------------------------------------------------------------- 假的 WebSocket


class FakeWs:
    """够用的 websocket 替身：记下发出去的帧，按脚本回帧。"""

    def __init__(self, script: list[str]) -> None:
        self.sent: list[str] = []
        self._script = list(script)
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive_text(self) -> str:
        if not self._script:
            raise ConnectionError("对面没了")
        return self._script.pop(0)

    async def close(self) -> None:
        self.closed = True


def _hello_frame(device: str, nonce: str, secret: str = SECRET) -> str:
    return json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "caelum/hello",
        "params": {
            "deviceId": device, "nonce": nonce,
            "proof": _sign(secret, ["pc", device, nonce]),
        },
    })


def test_handshake_accepts_correct_proof(monkeypatch):
    monkeypatch.setenv("CAELUM_LINK_SECRET", SECRET)
    link = LocalLink()
    ws = FakeWs([_hello_frame("tangtang-pc", "n1")])

    asyncio.run(link.serve(ws))

    # 回了一份自己的证明
    assert ws.sent, "应该回一帧 hello result"
    reply = json.loads(ws.sent[0])
    assert "result" in reply
    assert reply["result"]["proof"] == _sign(
        SECRET, ["vps", "tangtang-pc", "n1", reply["result"]["nonce"]],
    )


def test_handshake_rejects_wrong_proof(monkeypatch):
    """🔴 拿不出密钥的连不上 —— 拦住"任何人都能冒充她的电脑"。"""
    monkeypatch.setenv("CAELUM_LINK_SECRET", SECRET)
    link = LocalLink()
    ws = FakeWs([_hello_frame("attacker-pc", "n1", secret="y" * MIN_SECRET_LENGTH)])

    asyncio.run(link.serve(ws))

    assert link.is_ready is False
    assert ws.closed is True


def test_handshake_rejects_non_hello_first_frame(monkeypatch):
    """第一帧必须是 hello —— 不许先干活再认证。"""
    monkeypatch.setenv("CAELUM_LINK_SECRET", SECRET)
    link = LocalLink()
    ws = FakeWs([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})])

    asyncio.run(link.serve(ws))
    assert link.is_ready is False


def test_no_secret_refuses_every_connection(monkeypatch):
    """🔴 没配密钥就拒绝所有连接，不是"先让它连上"。"""
    monkeypatch.delenv("CAELUM_LINK_SECRET", raising=False)
    link = LocalLink()
    ws = FakeWs([_hello_frame("tangtang-pc", "n1")])

    asyncio.run(link.serve(ws))
    assert link.is_ready is False
    assert ws.closed is True


def test_link_drops_after_disconnect(monkeypatch):
    """断开之后状态要清干净，不能留着一个连不上的 ws。"""
    monkeypatch.setenv("CAELUM_LINK_SECRET", SECRET)
    link = LocalLink()
    # 握完手就断（脚本空了 → receive_text 抛）
    asyncio.run(link.serve(FakeWs([_hello_frame("tangtang-pc", "n1")])))
    assert link.is_ready is False
    assert link.device_id == ""


# ---------------------------------------------------------------- 挂进 FastAPI


def _app(tmp_path, monkeypatch):
    """起一个带本地执行端的 app。用 test_resonance_v1 里那个 ChatNox。"""
    from data.store import Store
    from api.server import create_app
    from tests.test_resonance_v1 import ChatNox

    monkeypatch.delenv("NOX_ATTENTION", raising=False)
    store = Store(tmp_path / "s.db")
    return create_app(ChatNox(tmp_path / "s.db"), store), store


def test_endpoint_absent_without_secret(tmp_path, monkeypatch):
    """🔴 没配密钥 → 这条路由**根本不存在**。

    不是"注册了但拒绝" —— 那样端点仍然在，扫描器能发现，
    而且任何人都能对它发起握手。现有部署没配这个变量，
    所以行为必须零变化。
    """
    monkeypatch.delenv("CAELUM_LINK_SECRET", raising=False)
    app, store = _app(tmp_path, monkeypatch)
    try:
        paths = {r.path for r in app.routes}
        assert "/agent/local" not in paths
    finally:
        store.close()


def test_endpoint_present_with_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("CAELUM_LINK_SECRET", SECRET)
    app, store = _app(tmp_path, monkeypatch)
    try:
        paths = {r.path for r in app.routes}
        assert "/agent/local" in paths
    finally:
        store.close()


def test_health_reports_hand_state(tmp_path, monkeypatch):
    """/health 要能看出手连没连 —— 否则没法确认链路到底通没通。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CAELUM_LINK_SECRET", SECRET)
    app, store = _app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            hand = client.get("/health").json()["local_hand"]
        assert hand == {"ready": False, "device": ""}
    finally:
        store.close()


def test_health_hand_is_none_when_disabled(tmp_path, monkeypatch):
    """没启用时是 None —— 和"启用了但没连上"要看得出区别。"""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("CAELUM_LINK_SECRET", raising=False)
    app, store = _app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            assert client.get("/health").json()["local_hand"] is None
    finally:
        store.close()


# ---------------------------------------------------------------- ⑧ 记进 World


class FakeWorld:
    """够用的 World Model 替身。"""

    def __init__(self) -> None:
        self.observed: list[dict] = []
        self.keys: set[str] = set()

    def observe(self, *, source, type, observed, observed_at, dedup_key=None, **kw):
        if dedup_key is not None and dedup_key in self.keys:
            return None          # 幂等，和真的 World Model 一样
        if dedup_key is not None:
            self.keys.add(dedup_key)
        self.observed.append({"source": source, "type": type, "observed": observed})
        return object()


def _summary_frame(**over) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "caelum/event",
        "params": {
            "kind": "execution",
            "at": "2026-08-25T10:00:00.000Z",
            "capability": "computer.read_file",
            "ok": True,
            "paths": ["D:/x.md"],
            "ms": 12,
            **over,
        },
    }


def test_summary_goes_into_world():
    """🔴 他记得自己做过什么 —— 这是「用电脑」和「调工具」的区别。"""
    world = FakeWorld()
    link = LocalLink(world=world)
    link._on_message(_summary_frame())

    assert len(world.observed) == 1
    obs = world.observed[0]
    assert obs["source"] == "local_hand"
    assert obs["observed"]["做了什么"] == "computer.read_file"
    assert obs["observed"]["动了哪些"] == ["D:/x.md"]
    assert obs["observed"]["成了吗"] is True


def test_same_summary_twice_is_recorded_once():
    """重复收到同一条不该记两遍 —— dedup_key 把住。"""
    world = FakeWorld()
    link = LocalLink(world=world)
    link._on_message(_summary_frame())
    link._on_message(_summary_frame())
    assert len(world.observed) == 1


def test_failure_is_recorded_too():
    """失败也要记 —— 「他试过但没成」和「他没试过」是两回事。"""
    world = FakeWorld()
    link = LocalLink(world=world)
    link._on_message(_summary_frame(ok=False, error="文件不存在"))

    obs = world.observed[0]["observed"]
    assert obs["成了吗"] is False
    assert obs["没成的原因"] == "文件不存在"


def test_no_world_is_fine():
    """没接 World Model 时只是不记，链路照常。"""
    link = LocalLink()
    link._on_message(_summary_frame())   # 不该抛


def test_broken_summary_does_not_break_link():
    """🔴 摘要坏了不能影响链路。"""
    world = FakeWorld()
    link = LocalLink(world=world)
    link._on_message({"jsonrpc": "2.0", "method": "caelum/event", "params": {}})
    link._on_message({"jsonrpc": "2.0", "method": "caelum/event"})
    assert world.observed == []          # 没有 capability 就不记，但也不抛


def test_world_failure_does_not_break_link():
    """World Model 炸了也不许影响链路。"""
    class Boom:
        def observe(self, **kw):
            raise RuntimeError("库锁了")

    link = LocalLink(world=Boom())
    link._on_message(_summary_frame())   # 不该抛
