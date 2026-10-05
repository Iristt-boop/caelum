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
