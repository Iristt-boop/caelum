"""手册 + 地图在**真实装配路径**上到手（`Nox.__init__`），不是手搓 ctx。

为什么要有这个：单元测试里自己 `register_all(loop)` 再断言，证明不了 `nox.py` 里真的接上了。
（cordis 那次：手搓 ctx 绕过了运行期才验的 inject，80 个测试全绿，线上全挂。）
这里真的构造一个 `Nox`，只把「会连外面的东西」换成假的 / 清空。

⚠️ 构造前把 config.py 里所有像地址、密钥的环境变量清空 —— 在她的机器上跑这个测试，
`.env` 里是真的服务地址，测试不许顺手去连它们。
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import config as config_mod
import nox as nox_mod
from config import Config, LLMConfig
from guide.loader import load_topics, render_directory

_SECRETISH = re.compile(
    r"(_URL|_TOKEN|_KEY|_HOST|_REPO|_CHANNEL|_LOCATION|_PERSON)$"
    r"|^(GITHUB|NOTION|QWEATHER|TAVILY|CO_READING|READING)_"
)
LEAK = "KEY-LEAK-CHECK-777"


class _FakeOB:
    def __init__(self, *a, **k):
        pass

    def core_principles(self):
        return SimpleNamespace(ok=False, text="", error="offline-test")

    def profile_read(self):
        import json as _json
        return SimpleNamespace(ok=True, error=None, text=_json.dumps(
            {"sections": [{"name": "基本", "lines": ["启动时就在 〔10-23〕"]}]}, ensure_ascii=False))


@pytest.fixture(scope="module")
def nox():
    mp = pytest.MonkeyPatch()
    src = Path(config_mod.__file__).read_text(encoding="utf-8")
    for name in set(re.findall(r'_env(?:_int|_float)?\(\s*"([A-Z0-9_]+)"', src)):
        if _SECRETISH.search(name):
            mp.setenv(name, "")
    mp.setattr(nox_mod, "OmbreBrain", _FakeOB)
    cfg = dataclasses.replace(
        Config(),
        primary=LLMConfig(provider="openai_compat", model="asm-model", api_key=LEAK,
                          base_url="http://127.0.0.1:9/v1"),
        bridge_url="http://127.0.0.1:9",
        bridge_token="t",
        ob_url="http://127.0.0.1:9",   # check() 要求非空；OmbreBrain 已换成假的，不会真连
    )
    try:
        yield nox_mod.Nox(cfg)
    finally:
        mp.undo()


def test_两个工具排在工具表最后_planner之后(nox):
    """工具定义进静态前缀，顺序一变整段缓存作废：新工具只许往后排。"""
    names = list(nox.loop.tools)
    assert names[-2:] == ["guide_read", "caelum_map"]
    assert names[-3] == "daily_summary"


def test_静态前缀里有目录没有正文(nox):
    prefix = nox.system_prompt
    assert render_directory(load_topics()) in prefix
    for t in load_topics():
        assert t.body[:30] not in prefix


def test_真实的guide_read拿到正文(nox):
    topics = load_topics()
    out = nox.loop.tools["guide_read"].handler({"topic": "memory"})
    assert out == next(t.body for t in topics if t.id == "memory")


def test_真实的地图看得见自己_且不泄密(nox):
    out = nox.loop.tools["caelum_map"].handler({})
    assert "手册与地图" in nox.loop.tools["caelum_map"].handler({"scope": "tools"})
    assert f"工具（{len(nox.loop.tools)} 个" in out
    assert "主线（和她聊天）" in nox.loop.tools["caelum_map"].handler({"scope": "models"})
    for scope in (None, "models", "tools", "context", "health", "pending", "os"):
        assert LEAK not in nox.loop.tools["caelum_map"].handler({"scope": scope} if scope else {})


def test_地图的上下文来源就是注册表里的(nox):
    out = nox.loop.tools["caelum_map"].handler({"scope": "context"})
    assert f"在场 {len(nox.context)} 个" in out
    for name in nox.context.names():
        assert name in out


def test_仓库没建好时_待确认段老实说取不到(nox):
    """订单 / 任务仓库在 api/server.py 才建；Nox 单独构造时还没有。"""
    out = nox.loop.tools["caelum_map"].handler({"scope": "pending"})
    assert "取不到" in out and "点单 0" not in out


def test_own_day拿到梦的本子所在(nox):
    """10-20 梦不进 OB，他靠 own_day 读梦的本子记得自己的梦 —— 真装配路径上得接上 data_dir。"""
    import os
    p = nox.context.get("own_day")
    assert p is not None and p.data_dir == os.path.dirname(str(nox.cfg.db_path))


def test_目标那一行注册了_用的是2秒超时的client(nox):
    p = nox.context.get("goals")
    assert p is not None and p.bridge is not nox.bridge and p.bridge.timeout == 2.0


# ---------------------------------------------------------------- 「关于她」档案进前缀（10-23）


def test_档案进前缀_改了才重建_读不到不动(nox):
    import json as _json
    from types import SimpleNamespace as NS

    class OB:
        def __init__(self, lines):
            self.lines, self.fail = lines, False

        def profile_read(self):
            if self.fail:
                return NS(ok=False, text="", error="OB 睡了")
            return NS(ok=True, text=_json.dumps({"sections": [{"name": "基本", "lines": self.lines}]}, ensure_ascii=False), error=None)

    old_ob = nox.ob
    try:
        nox.ob = OB(["自由职业 〔10-20 她说〕"])
        assert nox.refresh_prefix("测试") is True
        assert "基本：\n- 自由职业\n" in nox.system_prompt + "\n", "来源标记不进前缀"
        assert nox.router.system_prompt == nox.system_prompt, "Router 拿的得是新前缀"
        assert nox.refresh_prefix("测试") is False, "没变就不动（缓存不冷）"
        before = nox.system_prompt
        nox.ob.fail = True
        assert nox.refresh_prefix("测试") is False and nox.system_prompt == before, "读不到不许换成兜底"
        nox.ob = OB(["自由职业", "在学西语"])
        assert nox.refresh_prefix("测试") is True and "- 在学西语" in nox.system_prompt
    finally:
        nox.ob = old_ob
        nox.refresh_prefix("测试收尾")


def test_启动时档案就进了前缀(nox):
    assert "=== 关于她" in nox.system_prompt and "- 启动时就在" in nox.system_prompt
    assert "射手座，INFP，自由职业" not in nox.system_prompt, "读得到档案就不该用兜底那几行"


def test_有DeepSeek_key_搜索和读网页都登记了():
    """10-23：搜索 DeepSeek 打头；只有它没有 Tavily 也要能搜（nox.py 原来只认 tavily_key）。"""
    mp = pytest.MonkeyPatch()
    src = Path(config_mod.__file__).read_text(encoding="utf-8")
    for name in set(re.findall(r'_env(?:_int|_float)?\(\s*"([A-Z0-9_]+)"', src)):
        if _SECRETISH.search(name):
            mp.setenv(name, "")
    mp.setattr(nox_mod, "OmbreBrain", _FakeOB)
    try:
        cfg = dataclasses.replace(
            Config(), primary=LLMConfig(provider="openai_compat", model="m", api_key="k", base_url="http://127.0.0.1:9/v1"),
            bridge_url="http://127.0.0.1:9", bridge_token="t", ob_url="http://127.0.0.1:9",
            tavily_key="", deepseek_search_key="ds-test")
        n = nox_mod.Nox(cfg)
        assert "web_search" in n.loop.tools and "web_read" in n.loop.tools
    finally:
        mp.undo()
