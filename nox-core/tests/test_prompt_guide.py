"""静态前缀里的手册目录：只加目录、不加正文、不带目录时与原来逐字节相同。"""

from __future__ import annotations

from agent.guard import GUARD_INSTRUCTION
from guide.loader import load_topics, render_directory
from personality.prompt import NOX_PERSONA, StaticPrefix, build


def _legacy(core: str) -> str:
    """加 guide 字段之前 render() 的拼法（照抄旧版）。"""
    parts = [NOX_PERSONA.strip()]
    if core.strip():
        parts.append("=== 关于你和糖糖的核心记忆 ===\n" + core.strip())
    parts.append(GUARD_INSTRUCTION.strip())
    return "\n\n".join(parts)


def test_不带目录时与原来逐字节相同():
    for core in ("", "核心记忆", "  带空白  \n"):
        assert build(core).render() == _legacy(core)


def test_老的位置参数写法不坏():
    assert build("x").guide == ""
    assert StaticPrefix(persona="p", core_memory="", guard="g").render() == "p\n\ng"


def test_带目录时以目录结尾_且只出现一次():
    d = render_directory(load_topics())
    out = build("核心记忆", d).render()
    assert out.endswith(d.strip())
    assert out.count(d.strip()) == 1
    assert out.startswith(_legacy("核心记忆"))


def test_前缀里没有任何一篇正文():
    out = build("核心记忆", render_directory(load_topics())).render()
    for t in load_topics():
        assert t.body[:30] not in out, f"{t.id} 的正文被塞进了静态前缀"


def test_目录让前缀变大_但不超过上限():
    d = render_directory(load_topics())
    base = build("核心记忆")
    with_guide = build("核心记忆", d)
    assert with_guide.size > base.size
    assert with_guide.size - base.size <= 1300
