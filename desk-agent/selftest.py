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

    srv.shutdown()
    print(f"\n{'全部通过' if not fails else f'{len(fails)} 条没过'}")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
