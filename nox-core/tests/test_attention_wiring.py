"""Attention 接进 server 那一层的接线测试。

## 为什么单独一个文件

2026-08-08 部署时踩了一个只在**线上**才活的 bug：

    api/server.py:245  provider = core.context.get("health")
    AttributeError: 'FakeNox' object has no attribute 'context'
    → 一次干掉 22 个测试

根因是 `try` 的范围写小了，只包住了最后那行装配。
而它在本地测不出来 —— **线上 `.env` 里有 `NOX_ATTENTION=1`，本地没有**
（`config.py` 会把 .env 读进 os.environ，pytest 也吃得到）。
本地永远走「没开就 return None」那条路，下面的代码根本不执行。

所以这里显式用 monkeypatch 把开关打开，逼那段「只在线上活的分支」
在本地也跑一遍。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.server import _build_attention  # noqa: E402


class BrokenCore:
    """连 context 都没有的 core。模拟测试替身 / 初始化到一半的 Nox。"""


class NoHealthCore:
    """有 context，但没注册 health Provider。"""

    class _Ctx:
        def get(self, name):
            return None

    context = _Ctx()


class _Cfg:
    history_limit = 40
    recent_window_tokens = 8000
    context_budget_tokens = 20000

    class router:
        light_adapter = None

    def __init__(self, db_path):
        self.db_path = db_path


class _Loop:
    """只需要 register —— `remind_myself` 会注册到这里。"""

    def __init__(self):
        self.registered = []

    def register(self, spec, handler):
        self.registered.append(spec.name)


class WorkingCore:
    """能装配起来的 core。M4 之后还要看 `bridge` 在不在。"""

    class _Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    class _Ctx:
        def get(self, name):
            return WorkingCore._Provider() if name == "health" else None

    def __init__(self, db_path, bridge=None):
        self.context = self._Ctx()
        self.cfg = _Cfg(db_path)
        self.bridge = bridge
        self.loop = _Loop()
        self.current_session_id = None
        self.router = type("R", (), {"light_adapter": None})()


def _build(core):
    """`sessions` / `db` 只在 LIVE 那条路上用得到，返回 None 的用例传 None 就行。"""
    return _build_attention(core, None, None)


def test_disabled_by_default():
    """不设环境变量就整条链路不跑 —— 现有部署零影响。"""
    assert _build(BrokenCore()) is None


def test_broken_core_does_not_raise(monkeypatch):
    """开着开关但 core 不完整时，只能返回 None，**不许抛**。

    抛出去的后果是 `create_app` 挂掉 —— Attention 起不来，
    连带整个 Nox Core 起不来。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    assert _build(BrokenCore()) is None


def test_missing_health_provider_is_skipped(monkeypatch):
    """没有 health Provider 就没有睡眠数据，安静跳过。"""
    monkeypatch.setenv("NOX_ATTENTION", "1")
    assert _build(NoHealthCore()) is None


def test_live_flag_alone_does_not_enable(monkeypatch):
    """只开 LIVE 不开 NOX_ATTENTION，仍然什么都不跑。"""
    monkeypatch.setenv("NOX_ATTENTION_LIVE", "1")
    monkeypatch.delenv("NOX_ATTENTION", raising=False)
    assert _build(BrokenCore()) is None


# ---------------------------------------------------------------- M4 接线


def test_default_is_dry_run(monkeypatch, tmp_path):
    """只开 NOX_ATTENTION 不开 LIVE = 想但不说。观察期就靠这个。"""
    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_ATTENTION_LIVE", raising=False)
    svc = _build_attention(WorkingCore(tmp_path / "nox.db"), None, None)
    assert svc is not None and svc.dry_run
    svc.store.close()


def test_live_without_bridge_falls_back_to_dry_run(monkeypatch, tmp_path):
    """⚠️ 没有推送通道时**必须**退回 dry-run。

    不退的话每次都会走到「发送失败」——而冷却是照记的，
    等于这件事被静静吃掉，她什么都收不到，日志里也只有一行 exception。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.setenv("NOX_ATTENTION_LIVE", "1")
    svc = _build_attention(WorkingCore(tmp_path / "nox.db", bridge=None), None, None)
    assert svc is not None and svc.dry_run
    svc.store.close()


def test_live_with_bridge_gets_a_speaker(monkeypatch, tmp_path):
    """两个开关都开 + 有 bridge = 真的会发。"""
    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.setenv("NOX_ATTENTION_LIVE", "1")
    svc = _build_attention(
        WorkingCore(tmp_path / "nox.db", bridge=object()), None, None)
    assert svc is not None and not svc.dry_run
    svc.store.close()


def test_remind_tool_registered_last(monkeypatch, tmp_path):
    """⚠️ `remind_myself` 必须是最后一个注册的工具。

    工具定义是缓存前缀的一部分，插在中间会让前缀整个失效 ——
    一轮 ¥0.00055 → ¥0.011，而且服务照常、回答照常，只有账单翻倍。
    `_build_attention` 在 `Nox.__init__` 之后跑，所以这里天然是最后一个。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    core = WorkingCore(tmp_path / "nox.db")
    svc = _build_attention(core, None, None)
    assert core.loop.registered[-1] == "remind_myself"
    svc.store.close()


def test_wake_live_needs_attention_live(monkeypatch, tmp_path):
    """唤醒链有自己的开关，但**不能越过** NOX_ATTENTION_LIVE 单独生效。

    它一次最多说 5 句，比睡眠关心激进得多 ——
    「主体还在 dry-run，追问却已经在发」是最糟的组合。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_ATTENTION_LIVE", raising=False)
    monkeypatch.setenv("NOX_WAKE_LIVE", "1")
    core = WorkingCore(tmp_path / "nox.db", bridge=object())
    svc = _build_attention(core, None, None)
    assert svc is not None
    # waker 存在，但它是 dry-run 的
    assert svc.waker is not None
    assert svc.dry_run          # speaker 没接 = 主体仍是 dry-run
    svc.store.close()


# ---------------------------------------------------------------- core.attention 挂载


class AppReadyCore(WorkingCore):
    """WorkingCore 再补上 `create_app` 要的几样，用来验挂载那一步。

    `_build_attention` 能不能跑起来上面那些用例已经盖住了；
    这里要验的是**它返回之后有没有被挂到 core 上**。
    """

    def __init__(self, tmp_path):
        super().__init__(tmp_path / "nox.db")
        self.loop.tools = {"recall_memory": None}
        self.system_prompt = "（前缀）"

        class _P:
            model = "fake-model"

        self.cfg.primary = _P()

    def model_name(self, model=None):
        return model or "fake-model"


def test_create_app_把_attention_挂到_core_上(tmp_path, monkeypatch):
    """🔴 2026-09-14 实录：这一行在它被写出来之前**根本不存在**。

    `attention` 一直只是 `create_app` 的局部变量，于是 `nox.py` 里三处
    `getattr(self, "attention", None)` 在线上恒为 None：

      · ResonanceProvider   → {"available": False}，静默渲染成空
      · UnderstandingProvider → 同上
      · has_live_anchor()   → 恒为 False

    2026-09-04 加 ResonanceProvider 是为了让六个 Drive 进他的上下文，
    **那次修复从来没生效过**，而且两周没人发现 ——
    因为它的失败形状是「什么都没发生」，没有异常、没有日志、测试全绿。

    能挡什么：挂载被删 / 改名 / 挪到 `_build_attention` 里面去。
    挡不住什么：`NOX_ATTENTION` 关着时（那时本来就没有 attention，挂不上是对的）。
    """
    from api.server import Store, create_app

    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_ATTENTION_LIVE", raising=False)

    core = AppReadyCore(tmp_path)
    store = Store(tmp_path / "sessions.db")
    try:
        create_app(core, store)
        svc = getattr(core, "attention", None)
        assert svc is not None, (
            "create_app 跑完了，core 还是够不着 attention —— "
            "Resonance / Understanding 两个 Provider 会永远渲染成空且不报错"
        )
        # 不只是"有个对象"：得是真能出 Drive 的那个
        assert callable(getattr(svc, "drives", None)), "挂上来的东西不认识 drives()"
    finally:
        store.close()
