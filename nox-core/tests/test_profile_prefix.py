"""「关于她」挪出人设、进静态前缀（10-23，她 10-20 定：Soul 只放人设）。"""

from __future__ import annotations

from personality import prompt as P


def test_人设里不再有关于她和过时的数据环境():
    assert "关于她：" not in P.NOX_PERSONA
    assert "数据环境" not in P.NOX_PERSONA and "VPS" not in P.NOX_PERSONA


def test_档案渲染_去来源标记_空分区不写():
    text = P.render_profile({"sections": [
        {"name": "基本", "lines": ["自由职业 〔10-20 她说〕", "  "]},
        {"name": "她身边的人", "lines": []},
        {"name": "我们之间", "lines": ["约定：1 是暗号"]}]})
    assert text == "基本：\n- 自由职业\n我们之间：\n- 约定：1 是暗号"


def test_有档案用档案_没有用兜底():
    with_profile = P.build("", "", "基本：\n- 自由职业").render()
    assert "=== 关于她" in with_profile and "- 自由职业" in with_profile
    assert P.FALLBACK_ABOUT_HER not in with_profile
    fallback = P.build("", "", "").render()
    assert P.FALLBACK_ABOUT_HER in fallback


def test_他改了档案就重建前缀():
    import json
    from types import SimpleNamespace as NS
    from tools import profile as profile_tools

    class OB:
        def profile_read(self):
            return NS(ok=True, text=json.dumps({"sections": [{"name": "基本", "lines": ["自由职业"]}]}, ensure_ascii=False))

        def profile_edit(self, op, **kw):
            return NS(ok=True, text=json.dumps({"ok": True, "op": op, "section": "喜欢", "old": "", "new": kw["text"]}, ensure_ascii=False))

    seen = []
    profile_tools.make_handler(OB(), on_change=seen.append)({"op": "add", "section": "喜欢", "text": "拼豆"})
    assert seen == ["他改了档案"]
