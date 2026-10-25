"""Nox Skills（10-25，《Nox-skill-设计稿-2026-10-25》）。

钉住她能感知的几件事：
- Claude 格式的 SKILL.md 收得下（多行 description、多余的键），不合格的说清楚为什么
- 改一版留一版，同内容点两次不多一版，她关着的改完还关着
- 她这句话对上了才递正文，对不上只有一行目录；关掉的不出现；他主动开口那轮不拿系统提示去对
- **从使用方看**：真的 Nox._dynamic 拼出来的那段里有正文（不只看 render 的返回值）
- 首批三篇都收得下，而且都短到能整篇递进去
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from skills import render
from skills.parse import SkillError, parse
from skills.store import SkillStore
from skills import tools as skill_tools

STARTER = Path(__file__).resolve().parent.parent / "skills" / "starter"


def md(name="xhs-topics", desc="想选题时用", when="选题, 拍什么", body="1. 先问她发哪边"):
    w = f"when: {when}\n" if when is not None else ""
    return f"---\nname: {name}\ndescription: {desc}\n{w}---\n{body}\n"


# ---------------------------------------------------------------- 解析


def test_Claude格式_多行描述和多余的键都收():
    sk = parse("---\nname: brainstorming\ndescription: |\n  动手之前\n  先问清楚\nlicense: MIT\nmetadata:\n  a: 1\n---\n正文\n")
    assert sk.name == "brainstorming" and sk.description == "动手之前 先问清楚" and sk.when == () and sk.body == "正文"


def test_触发词_中英文逗号顿号都能分_单字丢掉():
    assert parse(md(when="选题，拍什么、读, 写;涨粉")).when == ("选题", "拍什么", "涨粉")


@pytest.mark.parametrize("text,why", [
    ("没有文件头", "第一行"),
    ("---\nname: a\n", "结尾"),
    (md(name="Bad_Name"), "name"),
    (md(desc=""), "description"),
    (md(body=""), "正文"),
    ("---\n- 一个列表\n---\n正文", "key: value"),
])
def test_不合格的说清楚为什么(text, why):
    with pytest.raises(SkillError, match=why):
        parse(text)


def test_首批三篇都收得下_而且短到整篇递进去():
    files = sorted(STARTER.glob("*.md"))
    assert {p.stem for p in files} == {"xhs-douyin-topics", "script-spoken", "reading-together"}
    for p in files:
        sk = parse(p.read_text(encoding="utf-8"))
        assert sk.name == p.stem
        assert len(sk.body) <= render.INLINE_MAX, f"{p.name} 正文 {len(sk.body)} 字，命中时递不进整篇"
        assert len(sk.when) >= 3


# ---------------------------------------------------------------- 存储


@pytest.fixture
def store():
    return SkillStore(":memory:")


def test_装_改_同内容不多一版_关着的改完还关着(store):
    sk, st = store.install(md(), origin="xiaoke")
    assert st == "created" and sk["version"] == 1 and sk["enabled"] and sk["triggers"] == ["选题", "拍什么"]
    assert store.install(md(), origin="her")[1] == "unchanged"
    assert store.set_enabled("xhs-topics", False)
    sk, st = store.install(md(body="2. 改过了"), origin="her")
    assert st == "updated" and sk["version"] == 2 and sk["body"] == "2. 改过了" and sk["enabled"] is False
    assert [h["version"] for h in store.history("xhs-topics")] == [2, 1]
    assert "1. 先问她发哪边" in store.history("xhs-topics")[1]["raw"]


def test_来源不认识就拒(store):
    with pytest.raises(ValueError):
        store.install(md(), origin="internet")


def test_用量按七天数(store):
    store.install(md(), origin="xiaoke")
    store.record("xhs-topics", "hit")
    store.record("xhs-topics", "hit")
    store.record("xhs-topics", "read")
    assert store.uses_since(7) == {"xhs-topics": {"hit": 2, "read": 1}}


# ---------------------------------------------------------------- 匹配与渲染


def _sk(name, when, body="做法", enabled=True, desc="一句话"):
    return {"name": name, "description": desc, "triggers": when, "body": body, "enabled": enabled}


def test_对上触发词或名字才算_关掉的不算_最多两个按词数():
    a = _sk("xhs-topics", ["选题", "小红书笔记"])
    b = _sk("script-spoken", ["口播", "脚本"])
    c = _sk("reading", ["尼采"], enabled=False)
    d = _sk("other", ["选题"])
    assert render.match([a, b, c, d], "今天吃什么") == []
    assert render.match([a, b, c, d], "尼采这句什么意思") == []
    assert render.match([a, b], "用 script-spoken 帮我看看") == [b]
    got = render.match([d, a, b], "帮我想个选题，顺便写个小红书笔记的口播")
    assert [s["name"] for s in got] == ["xhs-topics", "other"], "对上两个词的排前面，最多两个"
    assert render.match([a], "") == [] and render.match([a], None) == []


def test_没装就一个字不占_没对上只有目录_对上了整篇():
    a = _sk("xhs-topics", ["选题"], body="第一步先问她")
    assert render.render_block([], []) == ""
    assert render.render_block([_sk("x", ["y"], enabled=False)], []) == ""
    only = render.render_block([a], [])
    assert only == "装着的做法（skill_read 翻全文）：xhs-topics"
    full = render.render_block([a], [a])
    assert render.HEAD in full and "— xhs-topics：一句话" in full and "第一步先问她" in full


def test_太长的只给一句话_叫他先翻():
    long = _sk("long", ["长"], body="很" * (render.INLINE_MAX + 1))
    out = render.render_block([long], [long])
    assert "很" * 50 not in out and 'skill_read(name="long")' in out


def test_skill_read_翻到正文并记一笔_关掉的和不存在的把目录递回去(store):
    store.install(md(), origin="xiaoke")
    store.install(md(name="off-one"), origin="xiaoke")
    store.set_enabled("off-one", False)
    assert "1. 先问她发哪边" in skill_tools.read(store, "xhs-topics")
    assert store.uses_since(7)["xhs-topics"]["read"] == 1
    for bad in ("off-one", "nope", None):
        out = skill_tools.read(store, bad)
        assert out.startswith("没有叫") and "xhs-topics" in out and "off-one" not in out.split("装着的：")[1]


# ---------------------------------------------------------------- 接口（页面 P2 从这里读写）


def test_接口_装_列_看一个_开关_装不上说为什么(tmp_path):
    from fastapi.testclient import TestClient

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox

    nox = FakeNox()
    nox.skills = SkillStore(tmp_path / "skills.db")
    seen = []
    nox.loop.register = lambda spec, handler: seen.append(spec.name)
    c = TestClient(create_app(nox, Store(tmp_path / "sessions.db")))
    assert seen[-1] == "skill_read" and "desk_request_login" in seen, "skill_read 登记在 server.py 那一串最后"

    r = c.post("/api/nox/skills", json={"text": md(), "source": "https://example.test/x"}).json()
    assert r["status"] == "created" and r["item"]["origin"] == "her" and r["item"]["source"].endswith("/x")
    assert c.post("/api/nox/skills", json={"text": md()}).json()["status"] == "unchanged"
    bad = c.post("/api/nox/skills", json={"text": md(name="Bad")})
    assert bad.status_code == 422 and "name" in bad.json()["detail"]

    nox.skills.record("xhs-topics", "hit")
    items = c.get("/api/nox/skills").json()["items"]
    assert [(i["name"], i["hits_7d"], i["enabled"]) for i in items] == [("xhs-topics", 1, True)]
    one = c.get("/api/nox/skills/xhs-topics").json()
    assert "1. 先问她发哪边" in one["item"]["raw"] and [h["version"] for h in one["history"]] == [1]

    assert c.patch("/api/nox/skills/xhs-topics", json={"enabled": False}).json()["ok"]
    assert nox.skills.get("xhs-topics")["enabled"] is False
    assert c.patch("/api/nox/skills/nope", json={"enabled": True}).status_code == 404
    assert c.get("/api/nox/skills/nope").status_code == 404
    nox.skills.close()


# ---------------------------------------------------------------- 真装配路径：Nox._dynamic 拼出来的那段


@pytest.fixture(scope="module")
def nox():
    import config as config_mod
    import nox as nox_mod
    from config import Config, LLMConfig
    from tests.test_guide_nox_assembly import _FakeOB, _SECRETISH

    mp = pytest.MonkeyPatch()
    src = Path(config_mod.__file__).read_text(encoding="utf-8")
    for name in set(re.findall(r'_env(?:_int|_float)?\(\s*"([A-Z0-9_]+)"', src)):
        if _SECRETISH.search(name):
            mp.setenv(name, "")
    mp.setattr(nox_mod, "OmbreBrain", _FakeOB)
    cfg = dataclasses.replace(
        Config(), primary=LLMConfig(provider="openai_compat", model="m", api_key="k", base_url="http://127.0.0.1:9/v1"),
        bridge_url="http://127.0.0.1:9", bridge_token="t", ob_url="http://127.0.0.1:9")
    try:
        n = nox_mod.Nox(cfg)
        yield n
    finally:
        mp.undo()


@pytest.fixture
def seeded(nox):
    old = nox.skills
    nox.skills = SkillStore(":memory:")
    for p in sorted(STARTER.glob("*.md")):
        nox.skills.install(p.read_text(encoding="utf-8"), origin="xiaoke")
    try:
        yield nox
    finally:
        nox.skills = old


def test_真装配路径上开了库(nox):
    assert nox.skills is not None


def test_她说选题_这一轮他收到的动态段里有那篇正文(seeded):
    out = seeded._dynamic("帮我想想下一期拍什么", voice=False)
    assert render.HEAD in out and "一次给 3 个，不多给" in out
    assert "对着镜头说的稿子" not in out, "没对上的那篇不递"
    assert seeded.skills.uses_since(7)["xhs-douyin-topics"]["hit"] == 1


def test_闲聊只有一行目录(seeded):
    out = seeded._dynamic("今天好冷啊", voice=False)
    assert "装着的做法（skill_read 翻全文）：" in out and render.HEAD not in out


def test_他主动开口那轮拿recall去对_不拿系统提示(seeded):
    prompt = "（系统提示：不是她在跟你说话。她之前提过选题和口播的事……）"
    assert render.HEAD not in seeded._dynamic(prompt, voice=False, recall="")
    assert "先问她停在哪" in seeded._dynamic(prompt, voice=False, recall="她说读到尼采那句很难受")


def test_关掉的和总开关(seeded, monkeypatch):
    seeded.skills.set_enabled("xhs-douyin-topics", False)
    assert "一次给 3 个" not in seeded._dynamic("下一期拍什么", voice=False)
    monkeypatch.setenv("NOX_SKILLS", "off")
    assert "装着的做法" not in seeded._dynamic("读到尼采这一章", voice=False)


def test_读库坏了这一轮不带_不挡回话(seeded):
    class Broken:
        def all(self, **k):
            raise RuntimeError("库坏了")
    seeded.skills = Broken()
    assert "装着的做法" not in seeded._dynamic("下一期拍什么", voice=False)
