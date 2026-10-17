"""desk-agent —— Nox 的电脑（N150 上 VM nox-desk）里那只手（2026-10-17，v0）。

设计稿：`Caelum-Nox的电脑-设计稿-2026-10-14.md`。她电脑上那份 Gateway 是 dsh 里的 Cordis 插件（Windows、整套 harness），
搬到一台 Linux 小虚拟机上太重；v0 只要浏览器，所以这里是一个**只说同一套链路协议**的小程序：

    WS 外连 Core（/agent/desk）→ caelum/hello 握手（HMAC，与 tools/local_link.py `_sign` 逐字节一致）
    → 收 JSON-RPC：tools/list、tools/call（desk.open / desk.read）

## 边界

- 只有浏览器，**没有终端、没有文件**（v1 用得着再开）
- 浏览器 profile 是空的，什么站都没登
- 只开 http/https；VM 本身的出站被 Proxmox 防火墙挡在家里网段之外（宿主、HAOS、她的设备都碰不到）
- 密钥在 /etc/nox-desk/secret（600），不进任何日志

环境变量：DESK_URL（ws://…/agent/desk）、DESK_SECRET_FILE、DESK_DEVICE（默认 nox-desk）、DESK_PROFILE
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("desk-agent")

URL = os.environ.get("DESK_URL", "ws://192.168.1.52:8110/agent/desk")
SECRET_FILE = os.environ.get("DESK_SECRET_FILE", "/etc/nox-desk/secret")
DEVICE = os.environ.get("DESK_DEVICE", "nox-desk")
PROFILE = os.environ.get("DESK_PROFILE", "/var/lib/nox-desk/profile")

#: 一段多长（字符）。一页正文按段给他，免得一次把几万字倒进上下文
PART_CHARS = 6000
#: 一页最多留多少字（再长的页只读前面这些）
MAX_CHARS = 60000
NAV_TIMEOUT_MS = 20000


def sign(secret: str, parts: list[str]) -> str:
    """🔴 与 nox-core/tools/local_link.py `_sign` 逐字节一致：长度用 UTF-8 字节数。"""
    payload = "|".join(f"{len(p.encode('utf-8'))}:{p}" for p in parts)
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def chunk(text: str, part: int) -> tuple[str, int]:
    """第 part 段（从 1 开始）和总段数"""
    text = text[:MAX_CHARS]
    total = max(1, -(-len(text) // PART_CHARS))
    part = min(max(1, part), total)
    return text[(part - 1) * PART_CHARS: part * PART_CHARS], total


class Browser:
    """一个持久浏览器、一个标签页。v0 不并发：她和他一次只看一页"""

    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._page = None
        self._text = ""
        self._title = ""
        self._url = ""
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright
        if self._pw is None:
            self._pw = await async_playwright().start()
        if self._ctx is None:
            os.makedirs(PROFILE, exist_ok=True)
            self._ctx = await self._pw.chromium.launch_persistent_context(
                PROFILE, headless=True, locale="zh-CN", viewport={"width": 1280, "height": 900},
                args=["--disable-dev-shm-usage"])
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        return self._page

    async def open(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ValueError("只能开 http / https 的网址")
        async with self._lock:
            page = await self._ensure()
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001 —— 有的页一直有请求，不等它
                pass
            self._url, self._title = page.url, await page.title()
            self._text = (await page.evaluate("() => document.body ? document.body.innerText : ''") or "").strip()
            return self._render(1)

    async def read(self, part: int = 1) -> str:
        async with self._lock:
            if not self._url:
                return "现在没有打开的页面，先用 desk_open 打开一个。"
            return self._render(part)

    def _render(self, part: int) -> str:
        body, total = chunk(self._text, part)
        head = f"【{self._title or '（无标题）'}】{self._url}\n"
        if total > 1:
            head += f"第 {min(max(1, part), total)} / {total} 段\n"
        return head + "\n" + (body or "（这一页没有可读的正文）")


TOOLS = [
    {"name": "desk.open", "description": "打开网址，回标题和正文第一段"},
    {"name": "desk.read", "description": "读当前页的第 part 段"},
]


async def handle(browser: Browser, msg: dict[str, Any]) -> dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    if mid is None:
        return None
    try:
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            p = msg.get("params") or {}
            name, args = p.get("name"), p.get("arguments") or {}
            if name == "desk.open":
                text = await browser.open(str(args.get("url") or ""))
            elif name == "desk.read":
                text = await browser.read(int(args.get("part") or 1))
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"没有这件能力：{name}"}}
            log.info("%s 完成", name)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不认识的方法：{method}"}}
    except Exception as exc:  # noqa: BLE001 —— 一次失败如实回给他，不断链
        log.warning("%s 失败：%s", method, exc)
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": f"{type(exc).__name__}: {str(exc)[:300]}"}}


async def session(secret: str, browser: Browser) -> None:
    import websockets
    async with websockets.connect(URL, max_size=8 * 1024 * 1024, ping_interval=20) as ws:
        nonce = secrets.token_hex(16)
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "caelum/hello",
                                  "params": {"deviceId": DEVICE, "nonce": nonce,
                                             "proof": sign(secret, ["pc", DEVICE, nonce])}}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
        res = reply.get("result") or {}
        expect = sign(secret, ["vps", DEVICE, nonce, str(res.get("nonce", ""))])
        if not hmac.compare_digest(str(res.get("proof", "")), expect):
            raise ConnectionError("对面证明不了自己是 Nox Core，断开")
        log.info("链路建立：%s", URL)
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            out = await handle(browser, msg)
            if out is not None:
                await ws.send(json.dumps(out, ensure_ascii=False))


async def main() -> None:
    with open(SECRET_FILE, encoding="utf-8") as f:
        secret = f.read().strip()
    if len(secret) < 32:
        raise SystemExit(f"{SECRET_FILE} 里的密钥太短")
    browser = Browser()
    backoff = 1
    while True:
        try:
            await session(secret, browser)
            backoff = 1
        except Exception as exc:  # noqa: BLE001
            log.warning("链路断开：%s（%d 秒后重连）", exc, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    asyncio.run(main())
