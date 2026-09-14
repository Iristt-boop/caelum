"""跨重启的状态：累积情绪 + 欠卡账（排期 债 5，2026-09-14）。

## 这些测试能挡什么

- 情绪存不住 / 读不回来（键错、形状错、拿不到 store）
- 衰减曲线被改坏（该原样恢复的清零了，该忘掉的还记着）
- **恢复时新建对象替换而不是就地改** —— MoodProvider 攥的是引用，
  换对象它会一直读旧的**而且不报错**。这是本次改动最容易假绿的一处
- 存档损坏 / 时钟回拨把他弄得起不来

## 挡不住什么

- `_dynamic` 之外的路径第一次读情绪（眼下没有；真加了要补一条）
- 进程被 kill -9 时最后一轮还没落盘的那次更新（窗口几毫秒，不治）
- 半衰期这个数字合不合她的感觉 —— 那是她说了算的，测试只守住"确实按它衰减"

参照 `docs/RESTART-STATE.md`。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.store import AttentionStore  # noqa: E402
from context import ContextProviderRegistry  # noqa: E402
from context.base import Turn  # noqa: E402
from context.providers.mood import MoodProvider  # noqa: E402
from nox import STATE_KEY, Nox, _CardDebt  # noqa: E402
from personality import mood as mood_mod  # noqa: E402
from personality.mood import HALF_LIFE, Mood, now_cst  # noqa: E402


def _saved(valence=0.68, arousal=0.55, emotion="开心", turns=7, ago=timedelta(0)):
    """造一份"ago 之前存下来的"情绪存档。"""
    m = Mood(valence=valence, arousal=arousal, her_emotion=emotion, turns=turns)
    m.updated_at = now_cst() - ago
    return m.to_dict()


# --------------------------------------------------------------- 衰减曲线

@pytest.mark.parametrize("ago, expect_valence", [
    (timedelta(seconds=40), 0.68),    # 发版本的那几十秒 —— 几乎不动
    (timedelta(hours=2), 0.49),       # 一个半衰期 —— 走一半
    (timedelta(hours=8), 0.32),       # 四个半衰期 —— 基本回默认
    (timedelta(hours=24), 0.30),      # 隔天 —— 默认
])
def test_衰减到她定的那条曲线上(ago, expect_valence):
    """糖糖 2026-09-14 选的规则：按时间衰减、不跳变。

    这四个点就是她当时看到的那张表，改半衰期会让它们全红 —— 那正是本意：
    这个数字是她的感觉，不许顺手调。
    """
    m = Mood()
    assert m.restore(_saved(ago=ago)) is True
    assert m.valence == pytest.approx(expect_valence, abs=0.01)


def test_离开久了她上一轮的情绪不再算数():
    """her_emotion 和 turns 是离散的，没法衰减一半，挂同一个半衰期。"""
    fresh = Mood()
    fresh.restore(_saved(emotion="撒娇", turns=7, ago=timedelta(minutes=10)))
    assert fresh.her_emotion == "撒娇"
    assert fresh.turns == 7

    stale = Mood()
    stale.restore(_saved(emotion="撒娇", turns=7, ago=HALF_LIFE + timedelta(minutes=1)))
    assert stale.her_emotion == "平静", "隔了一个半衰期还当她在撒娇"
    assert stale.turns == 0


def test_衰减的目标是默认坐标不是零():
    """低落的情绪也该往 0.3 回，不是一路掉到 0 或者 -1。"""
    m = Mood()
    m.restore(_saved(valence=-0.7, arousal=0.4, emotion="难过", ago=timedelta(hours=24)))
    assert m.valence == pytest.approx(mood_mod.DEFAULT_VALENCE, abs=0.01)
    assert m.arousal == pytest.approx(mood_mod.DEFAULT_AROUSAL, abs=0.01)


# --------------------------------------------------------------- 引用陷阱

def test_恢复必须就地改而不是换对象():
    """🔴 本次改动最容易假绿的一条。

    `nox.py` 是 `MoodProvider(self.mood)` 注册的，Provider 攥着引用。
    如果 restore 写成 `self.mood = Mood.from_dict(...)`，
    Provider 会继续读旧对象 —— 他的上下文里永远是默认情绪，**一个错都不报**。

    所以这里不断言"值对了"，断言的是**同一个对象上的值变了**，
    而且**从 Provider 那一侧读**（消费者视角）。
    """
    m = Mood()
    provider = MoodProvider(m)
    before = provider._fetch(Turn(text="在吗"))["valence"]

    m.restore(_saved(valence=0.68, ago=timedelta(seconds=30)))

    after = provider._fetch(Turn(text="在吗"))["valence"]
    assert before != after, "Provider 那侧没看见变化 —— 多半是换了对象而不是就地改"
    assert after == pytest.approx(0.68, abs=0.01)


# --------------------------------------------------------------- 坏输入

@pytest.mark.parametrize("bad", [
    None, {}, {"valence": 0.7},                      # 缺 updated_at
    {"valence": "热", "arousal": 0.5, "updated_at": now_cst().isoformat()},
    {"valence": 0.7, "arousal": 0.5, "updated_at": "昨天"},
])
def test_存档坏了也要起得来(bad):
    """坏存档最坏的后果是这次从平常状态开始，绝不能让他起不来。"""
    m = Mood()
    assert m.restore(bad) is False
    assert m.valence == mood_mod.DEFAULT_VALENCE


def test_时钟回拨不会把情绪放大():
    """NTP 校时/换机器会让存档时间跑到未来。

    不 clamp 的话 0.5**负数 > 1，会把 valence 推到坐标系外面去。
    """
    m = Mood()
    m.restore(_saved(valence=0.68, ago=-timedelta(hours=5)))
    assert m.valence == pytest.approx(0.68, abs=0.01)
    assert -1.0 <= m.valence <= 1.0


# --------------------------------------------------------------- 欠卡账

def test_欠卡账短间隔留住隔久了丢掉():
    """她 2026-09-14 定：跟情绪同一个半衰期。"""
    debt = _CardDebt()
    fresh_at = (now_cst() - timedelta(minutes=5)).isoformat()
    stale_at = (now_cst() - HALF_LIFE - timedelta(minutes=1)).isoformat()

    kept = debt.restore({"s-刚欠的": fresh_at, "s-老黄历": stale_at})
    assert kept == 1
    assert "s-刚欠的" in debt
    assert "s-老黄历" not in debt, "隔久了还背着一个过时的指控"


def test_欠卡账每次变动都会触发落盘():
    """server.py 直接 .add()/.discard()，落盘得挂在这两个动作上。"""
    hits = []
    debt = _CardDebt(on_change=lambda: hits.append(1))

    debt.add("s-1")
    assert len(hits) == 1
    debt.discard("s-1")
    assert len(hits) == 2
    debt.discard("s-不存在")
    assert len(hits) == 2, "销一笔根本不存在的账不该写盘"


def test_欠卡账仍然是个正常的_set():
    """子类不能把 server.py 和现有测试依赖的 set 语义弄坏。"""
    debt = _CardDebt()
    assert debt == set()
    debt.add("s-1")
    assert "s-1" in debt and debt == {"s-1"}


def test_欠卡账读回来不刷新记账时刻():
    """否则连着重启几次，一笔账就永远过不了期。"""
    at = (now_cst() - timedelta(minutes=90)).isoformat()
    debt = _CardDebt()
    debt.restore({"s-1": at})
    assert debt.to_dict()["s-1"] == at


# --------------------------------------------------------------- 真库 + 真接线

def _core_with_store(tmp_path) -> Nox:
    """只把状态相关的字段装起来的 Nox。

    用 `__new__` 是因为真 `Nox()` 要连模型和一堆外部服务，单测里起不来
    （smoke_chat.py 才跑真的）。但 **store 是真 SQLite、方法是真方法、
    键是真键** —— 这条测试要挡的就是键错/形状错/拿不到 store。
    """
    core = Nox.__new__(Nox)
    core.mood = Mood()
    core.card_debt = _CardDebt(on_change=core._save_state)
    core._state_restored = False
    core.attention = type("A", (), {"store": AttentionStore(tmp_path / "attention.db")})()
    return core


def test_存进去再读回来_走真库(tmp_path):
    """一个进程存，另一个进程读 —— 部署重启就是这个形状。"""
    first = _core_with_store(tmp_path)
    first.mood.update("开心")
    first.mood.update("开心")
    # `Mood.update()` 自己不落盘 —— personality 层不该知道库的存在。
    # 真实路径上是 `nox.py` 收到回复、update 完之后紧接着调这一下
    first._save_state()
    first.card_debt.add("s-欠着")   # 这条自己会触发落盘
    saved_valence = first.mood.valence

    # 新进程：同一个库，全新的 Nox
    second = _core_with_store(tmp_path)
    assert second.mood.valence == mood_mod.DEFAULT_VALENCE  # 还没恢复
    second._restore_state_once()

    assert second.mood.valence == pytest.approx(saved_valence, abs=0.01)
    assert "s-欠着" in second.card_debt


def test_拿不到_store_时不炸也不算已恢复(tmp_path):
    """attention 比 Nox.__init__ 晚造出来，头几轮可能还没有。"""
    core = Nox.__new__(Nox)
    core.mood = Mood()
    core.card_debt = _CardDebt()
    core._state_restored = False
    # attention 还没挂上
    core._restore_state_once()
    assert core._state_restored is False, "没读成却标记成已恢复，后面就再也不试了"
    core._save_state()  # 不该抛


def test_第一轮就把情绪接回来_走真的_dynamic(tmp_path):
    """🔴 挡「恢复根本没被调用」—— 这条的失败形状是**什么都没发生**。

    存得好好的、restore 写得好好的，但没人调它，于是他永远从平常状态开始，
    日志干干净净、测试全绿。交接里五种假验证的第五种（检查没覆盖被改的东西）。

    所以这里调**真的 `_dynamic`**（他每轮组装提示的必经之路），
    然后从**渲染出来的那段文本**里断言 —— 消费者视角，不是看内部字段。
    """
    first = _core_with_store(tmp_path)
    first.mood.update("难过")
    first.mood.update("难过")
    first._save_state()
    assert first.mood.valence < 0, "前置没成立：没先把情绪压下去"

    # 新进程：只有 __init__ 会做的那些，没人手工调 restore
    second = _core_with_store(tmp_path)
    second.context = ContextProviderRegistry()
    second.context.register(MoodProvider(second.mood))

    rendered = second._dynamic("在吗", voice=False, session_id="s-1")

    # ⚠️ 判据必须挑**只有恢复了才会出现**的那句。
    # 第一版写的是 `"难过" in rendered` —— 那是**空断言**：
    # `MOOD_INSTRUCTION` 里本来就有「从 开心/难过/烦躁/… 里选一个」，
    # 这个匹配恒为真，把 `_dynamic` 里的恢复整个删掉它照样绿。
    # 变异测试抓到的（2026-09-14），不是想出来的。
    assert "看起来是「难过」" in rendered, (
        "第一轮的提示里没有上个进程留下的情绪 —— "
        "多半是 _dynamic 没调 _restore_state_once"
    )
    assert "看起来是「平静」" not in rendered


def test_落盘用的是那张共用的_source_state_表(tmp_path):
    """别再开一张新表（CAELUM-MAP 那条「平行实现反复长出来」）。"""
    core = _core_with_store(tmp_path)
    core.mood.update("兴奋")
    core._save_state()

    # 键写死在这里（不是引用 STATE_KEY）：用常量的话改名两边一起改，
    # 断言恒成立 —— 又一个空断言。写死才挡得住"悄悄挪到别的键/别的表"
    assert STATE_KEY == "personality.state"
    raw = core.attention.store.get_source_state("personality.state")
    assert raw is not None, "personality.state 这个键下什么都没有"
    assert "mood" in raw and "card_debt" in raw
