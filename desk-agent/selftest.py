"""desk-agent v1 自测 —— 在 VM 上用**真 Chromium** 跑（2026-10-17）。

    PLAYWRIGHT_BROWSERS_PATH=/opt/nox-desk/browsers /opt/nox-desk/.venv/bin/python selftest.py

本机起一个只监听 127.0.0.1 的小网站（几张测试页），把 Browser 的每个动作走一遍。
任何一条不对就非零退出 —— 部署前跑，改坏了要红。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PAGES = {
    "index.html": """<!doctype html><meta charset=utf-8><title>首页</title>
<h1>测试首页</h1>
<form action="/result.html" method="get"><input name="q" placeholder="搜索"><button>搜一下</button></form>
<input type="password" id="pw" placeholder="密码">
<a href="/page2.html">下一页</a> <a href="/page2.html" target="_blank">新窗口打开</a>
<div style="height:3000px"></div><a href="/page2.html">页底的链接</a>""",
    "page2.html": "<!doctype html><meta charset=utf-8><title>第二页</title><p>这里是第二页</p><a href='/index.html'>回首页</a>",
    "result.html": """<!doctype html><meta charset=utf-8><title>结果</title><p id=r></p>
<script>document.getElementById('r').innerText='搜到了：'+new URLSearchParams(location.search).get('q')</script>""",
    # v2 登录：账号 + 密码；对了跳 home，错了留在这页写「密码错误」；页面脚本还把账号抄到正文里（抹值要抹到）
    "login.html": """<!doctype html><meta charset=utf-8><title>登录</title>
<form id=f><input name=username placeholder="账号"><input type=password name=pw placeholder="密码"><button>登录</button></form>
<p id=err></p><div id=echo></div>
<script>
const u = document.querySelector('[name=username]'), p = document.querySelector('[name=pw]');
u.addEventListener('input', () => { document.getElementById('echo').innerText = '你好 ' + u.value + ' / ' + p.value; });
p.addEventListener('input', () => { document.getElementById('echo').innerText = '你好 ' + u.value + ' / ' + p.value; });
document.getElementById('f').addEventListener('submit', (e) => {
  e.preventDefault();
  if (p.value === 'right-PW-9f3a') location.href = '/home.html';
  else document.getElementById('err').innerText = '用户名或密码错误';
});
</script>""",
    "home.html": "<!doctype html><meta charset=utf-8><title>首页（登录后）</title><p>欢迎回来</p>",
    "sms.html": """<!doctype html><meta charset=utf-8><title>短信登录</title>
<form><input type=tel placeholder="手机号"><input placeholder="短信验证码"><button>登录</button></form>""",
}

SECRET = "pw-SENTINEL-9f3a"
fails: list[str] = []


def check(cond: bool, what: str) -> None:
    print(("✓ " if cond else "✗ ") + what, flush=True)
    if not cond:
        fails.append(what)


def ref_of(b, kind: str, name: str) -> int:
    for e in b._elements:
        if e["kind"] == kind and name in (e.get("name") or ""):
            return e["ref"]
    raise AssertionError(f"这一屏没找到 {kind}「{name}」：{b._elements}")


async def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    site = tempfile.mkdtemp()
    for n, body in PAGES.items():
        with open(os.path.join(site, n), "w", encoding="utf-8") as f:
            f.write(body)
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    os.environ["DESK_PROFILE"] = tempfile.mkdtemp()
    import agent
    agent.PROFILE = os.environ["DESK_PROFILE"]
    b = agent.Browser()

    out = await b.open(base + "/index.html")
    check("【首页】" in out and "能点 / 能填的" in out, "打开：标题 + 能点的清单")
    check(any(e["kind"] == "密码框" for e in b._elements), "密码框认得出来")

    out = await b.type(ref_of(b, "输入框", "搜索"), "猫粮", submit=True)
    check("搜到了：猫粮" in out and "/result.html" in b._url, "填字 + 回车 → 跳到结果页")

    out = await b.back()
    check("【首页】" in out, "退回上一页")

    out = await b.click(ref_of(b, "链接", "下一页"))
    check("这里是第二页" in out and b._url.endswith("/page2.html"), "点链接 → 第二页")
    await b.back()

    out = await b.click(ref_of(b, "链接", "新窗口打开"))
    check("这里是第二页" in out and len(b._ctx.pages) == 1, "点出新标签 → 跟过去，旧的关掉")
    await b.open(base + "/index.html")

    pw = ref_of(b, "密码框", "")
    for act, coro in (("点", b.click(pw)), ("填", b.type(pw, "x"))):
        try:
            await coro
            check(False, f"密码框不许{act}")
        except PermissionError:
            check(True, f"密码框不许{act}")

    await b._page.fill("#pw", SECRET)
    out = await b.read()
    check(SECRET not in out and SECRET not in str(b._elements), "密码框里的值不出现在他读到的任何东西里")

    try:
        await b.click(999)
        check(False, "过期编号要报清楚")
    except LookupError:
        check(True, "过期编号要报清楚")

    check(not any("页底" in (e.get("name") or "") for e in b._elements), "页底的链接一开始不在这一屏")
    out = await b.scroll("down")
    out = await b.scroll("down")
    out = await b.scroll("down")
    check("页底" in out and "位置" in out, "往下翻几屏 → 页底的链接出现了")

    out = await b.look()
    check("\nIMAGE image/jpeg " in out and len(out) > 2000, "截屏回一行图")

    # v1.5 她看我屏幕：半尺寸的帧；动作做到一半（锁着）也立刻回最近那张，不排队
    import base64
    import struct
    out = await b.frame()
    raw = base64.b64decode(out.rpartition("IMAGE image/jpeg ")[2])
    i, w = 2, 0
    while i < len(raw) - 9:   # 找 JPEG 的 SOF0/SOF2 段读宽
        seg, ln = raw[i + 1], struct.unpack(">H", raw[i + 2:i + 4])[0]
        if seg in (0xC0, 0xC2):
            w = struct.unpack(">H", raw[i + 7:i + 9])[0]
            break
        i += 2 + ln
    check(out.startswith("【") and 0 < w <= 700, f"帧是半尺寸（宽 {w}）")
    await b._lock.acquire()
    try:
        got = await asyncio.wait_for(b.frame(), 2)
        check("IMAGE image/jpeg " in got, "做动作时要帧：不排队，回最近那张")
    except asyncio.TimeoutError:
        check(False, "做动作时要帧：不排队，回最近那张")
    finally:
        b._lock.release()

    try:
        await b.open("file:///etc/passwd")
        check(False, "file:// 不开")
    except ValueError:
        check(True, "file:// 不开")

    await login_checks(b, base)

    srv.shutdown()
    print(f"\n{'全部通过' if not fails else f'{len(fails)} 条没过'}")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


async def raises(coro, what: str, contains: str = "") -> None:
    try:
        await coro
        check(False, what)
    except (ValueError, LookupError, PermissionError) as exc:
        check(contains in str(exc), what + (f"（{exc}）" if contains not in str(exc) else ""))


async def login_checks(b, base: str) -> None:
    """v2 登录（设计稿 5.2）：她填表、我不经手。PW 是哨兵值 —— 交出去的任何文字里都不许出现"""
    import json

    import desk_login
    PW, USER = "right-PW-9f3a", "tangtang-USER-7c1e"

    await b.open(base + "/login.html")
    await raises(b.login_begin(), "http 的页面不给登录", "https")
    desk_login.ALLOW_HTTP_HOSTS.add("127.0.0.1")   # 本机测试站是 http，只在这里放开

    card = json.loads(await b.login_begin())
    check(sorted(f["kind"] for f in card["fields"]) == ["pass", "user"], "认出账号框 + 密码框")
    check("正在登录" in await b.read() and "正在登录" in await b.look(), "登录中：他读不了、截不了这一页")
    check("正在登录" in await b.click(1), "登录中：他也点不了")

    # 密码错：留在登录页、报页面上的错；值不在回包里
    bad = desk_login.encrypt_for(card["public_key"], card["login_id"], {"user": USER, "pass": "wrong-pw"})
    res = json.loads(await b.login_fill(card["login_id"], **bad))
    check(res["status"] == "failed" and "密码错误" in res["message"], f"密码错 → 没登进去、带上页面的报错（{res}）")
    check(b._protect is None, "没登进去 → 凭证保护结束、值丢掉")
    await raises(b.login_fill(card["login_id"], **bad), "🔴 同一张卡的密文不能用第二次", "作废")

    # 凭证保护期间：网页把账号密码抄进正文，他读到的是 ••••（直接造一个保护态测抹值）
    b._protect = desk_login.Protection(origin=desk_login.origin_of(b._page.url), values=[USER, PW],
                                       until=__import__("time").time() + 60)
    await b._page.fill("[name=username]", USER)
    await b._page.fill("[name=pw]", PW)
    out = await b.read()
    check(USER not in out and PW not in out and "••••" in out, "🔴 凭证保护：网页抄到正文里的值也抹掉")
    b._protect = None

    # 换了页 → 卡作废、不填
    card = json.loads(await b.login_begin())
    await b._page.evaluate("location.href = '/home.html'")
    await __import__("asyncio").sleep(0.6)
    good = desk_login.encrypt_for(card["public_key"], card["login_id"], {"user": USER, "pass": PW})
    await raises(b.login_fill(card["login_id"], **good), "🔴 发卡之后页面换了 → 不填", "没填")

    # 别的卡的密文解不开 / 密文里封的 login_id 对不上
    await b.open(base + "/login.html")
    card = json.loads(await b.login_begin())
    other = desk_login.encrypt_for(card["public_key"], "0000000000000000", {"user": USER, "pass": PW})
    await raises(b.login_fill(card["login_id"], **other), "🔴 密文里的 login_id 对不上 → 不填", "解不开")

    # 对的：登进去、凭证保护结束、页面已经离开登录页
    card = json.loads(await b.login_begin())
    good = desk_login.encrypt_for(card["public_key"], card["login_id"], {"user": USER, "pass": PW})
    res = json.loads(await b.login_fill(card["login_id"], **good))
    check(res["status"] == "ok" and b._url.endswith("/home.html"), f"密码对 → 登进去了（{res}）")
    check(PW not in json.dumps(res) and USER not in json.dumps(res), "🔴 结果回包里没有值")
    check(b._protect is None and b._card is None, "登进去了 → 卡和值都丢掉")

    # 短信登录这一版不支持，说清楚
    await b.open(base + "/sms.html")
    await raises(b.login_begin(), "手机号 + 短信验证码登录：说清楚这一版不支持", "密码登录")

    # 退出一个站 = 清它（和子域名）的 cookie，别的站不动
    await b._ctx.add_cookies([{"name": "s", "value": "1", "domain": ".taobao.com", "path": "/"},
                              {"name": "s", "value": "1", "domain": "login.taobao.com", "path": "/"},
                              {"name": "k", "value": "1", "domain": ".example.com", "path": "/"}])
    await b.logout("taobao.com")
    left = {c["domain"] for c in await b._ctx.cookies()}
    check(not any("taobao" in d for d in left) and ".example.com" in left, f"退出淘宝：只清淘宝的 cookie（剩 {left}）")
    desk_login.ALLOW_HTTP_HOSTS.discard("127.0.0.1")


if __name__ == "__main__":
    asyncio.run(main())
