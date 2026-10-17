"""desk-agent —— Nox 的电脑（N150 上 VM nox-desk）里那只手（2026-10-17，v0）。

设计稿：`Caelum-Nox的电脑-设计稿-2026-10-14.md`。她电脑上那份 Gateway 是 dsh 里的 Cordis 插件（Windows、整套 harness），
搬到一台 Linux 小虚拟机上太重；v0 只要浏览器，所以这里是一个**只说同一套链路协议**的小程序：

    WS 外连 Core（/agent/desk）→ caelum/hello 握手（HMAC，与 tools/local_link.py `_sign` 逐字节一致）
    → 收 JSON-RPC：tools/list、tools/call（desk.open / read / click / type / scroll / back / look）

## 边界

- 只有浏览器，**没有终端、没有文件**（用得着再开）。v1（10-17）：会点、会填、会翻、会退、会截屏；密码框一律不碰
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


#: 收集「能点能填的东西」的脚本：只看视口里和附近的，按页面顺序编号，写进 data-nox-ref。
#: 每次收集先清掉旧编号 —— 编号只对「最近一次看到的」那一屏有效
COLLECT_JS = r"""
(limit) => {
  document.querySelectorAll('[data-nox-ref]').forEach(e => e.removeAttribute('data-nox-ref'));
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab],' +
              '[role=checkbox],[role=menuitem],[role=option],[contenteditable=""],[contenteditable=true],[onclick]';
  const vh = window.innerHeight, out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= limit) break;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.bottom < -100 || r.top > vh + 600) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || el.disabled) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type === 'hidden') continue;
    n += 1;
    el.setAttribute('data-nox-ref', String(n));
    let kind = tag === 'a' ? '链接' : tag === 'button' ? '按钮' : tag === 'select' ? '下拉框' :
               (tag === 'textarea' || el.isContentEditable) ? '输入框' :
               tag === 'input' ? ({checkbox: '勾选框', radio: '单选', submit: '按钮', button: '按钮', password: '密码框'}[type] || '输入框') :
               (el.getAttribute('role') === 'link' ? '链接' : '按钮');
    const name = (el.getAttribute('aria-label') || el.innerText || (type === 'password' ? '' : el.value) ||
                  el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || '')
                 .replace(/\s+/g, ' ').trim().slice(0, 40);
    const item = {ref: n, kind, name};
    if ((tag === 'input' || tag === 'textarea') && type !== 'password' && el.value) item.value = String(el.value).slice(0, 30);
    if (r.top > vh) item.below = true;
    out.push(item);
  }
  return out;
}
"""

#: 动作之后回给他的正文只要开头这么多（看全文用 desk_read）—— 一次动作的回包别太大
ACTION_TEXT_CHARS = 1500
MAX_ELEMENTS = 60
PASSWORD_REFUSAL = "这是密码框 —— 登录不由你来填（要走她自己填表的那条路，还没做）。"


class Browser:
    """一个持久浏览器、一个标签页（点出新标签就跟过去）。不并发：一次只看一页"""

    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._page = None
        self._text = ""
        self._title = ""
        self._url = ""
        self._elements: list[dict[str, Any]] = []
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
        self._page = self._ctx.pages[-1] if self._ctx.pages else await self._ctx.new_page()
        return self._page

    async def _settle(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:  # noqa: BLE001 —— 有的页一直有请求，不等它
            pass

    async def _refresh(self) -> None:
        """动作之后重新看一眼：标题、地址、正文、这一屏能点的东西"""
        page = self._page
        self._url, self._title = page.url, await page.title()
        self._text = (await page.evaluate("() => document.body ? document.body.innerText : ''") or "").strip()
        try:
            self._elements = await page.evaluate(COLLECT_JS, MAX_ELEMENTS)
        except Exception:  # noqa: BLE001
            self._elements = []

    def _need_page(self) -> str | None:
        return None if self._url else "现在没有打开的页面，先用 desk_open 打开一个。"

    async def _check_ref(self, ref: int) -> Any:
        loc = self._page.locator(f'[data-nox-ref="{int(ref)}"]').first
        if await loc.count() == 0:
            raise LookupError(f"编号 [{ref}] 不在这一屏上了（页面变了或者翻过去了）。先 desk_read 看一下现在能点的。")
        return loc

    async def open(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ValueError("只能开 http / https 的网址")
        async with self._lock:
            page = await self._ensure()
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await self._settle(page)
            await self._refresh()
            return self._render(1, PART_CHARS)

    async def read(self, part: int = 1) -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            await self._ensure()
            await self._refresh()
            return self._render(part, PART_CHARS)

    async def click(self, ref: int) -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            await self._ensure()
            loc = await self._check_ref(ref)
            if (await loc.get_attribute("type") or "").lower() == "password":
                raise PermissionError(PASSWORD_REFUSAL)
            label = next((e for e in self._elements if e.get("ref") == ref), {})
            before = len(self._ctx.pages)
            await loc.scroll_into_view_if_needed(timeout=5000)
            await loc.click(timeout=8000)
            await asyncio.sleep(0.5)
            #: 点出了新标签页 → 跟过去（旧的关掉，一次只看一页）
            if len(self._ctx.pages) > before:
                new = self._ctx.pages[-1]
                for p in self._ctx.pages[:-1]:
                    await p.close()
                self._page = new
            await self._settle(self._page)
            await self._refresh()
            return f"点了 [{ref}]「{label.get('name', '')}」\n" + self._render(1, ACTION_TEXT_CHARS)

    async def type(self, ref: int, text: str, submit: bool = False) -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            page = await self._ensure()
            loc = await self._check_ref(ref)
            info = await loc.evaluate(
                "e => ({tag: e.tagName.toLowerCase(), type: (e.getAttribute('type')||'').toLowerCase(), ce: e.isContentEditable})")
            if info["type"] == "password":
                raise PermissionError(PASSWORD_REFUSAL)
            if info["tag"] not in ("input", "textarea") and not info["ce"]:
                raise ValueError(f"[{ref}] 不是输入框，填不了字。")
            await loc.fill(text[:500], timeout=8000)
            if submit:
                await loc.press("Enter")
                await asyncio.sleep(0.5)
                await self._settle(self._page)
            await self._refresh()
            return (f"在 [{ref}] 填了「{text[:40]}」{'，按了回车' if submit else ''}\n"
                    + self._render(1, ACTION_TEXT_CHARS))

    async def scroll(self, direction: str = "down") -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            page = await self._ensure()
            sign = -1 if direction == "up" else 1
            await page.evaluate(f"() => window.scrollBy(0, {sign} * Math.round(window.innerHeight * 0.85))")
            await asyncio.sleep(0.4)
            await self._refresh()
            y, h = await page.evaluate(
                "() => [Math.round(scrollY), Math.max(1, document.documentElement.scrollHeight - innerHeight)]")
            pct = min(100, round(y / h * 100))
            return f"往{'上' if sign < 0 else '下'}翻了一屏（现在在页面 {pct}% 的位置）\n" + self._render_elements()

    async def back(self) -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            page = await self._ensure()
            was = page.url
            await page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if page.url == was:
                return "已经是第一页了，退不回去。"
            await self._settle(page)
            await self._refresh()
            return "退回上一页\n" + self._render(1, ACTION_TEXT_CHARS)

    async def look(self) -> str:
        """截这一屏。回「标题 地址」一行 + `IMAGE image/jpeg <base64>` 一行，Core 那边拦下来换成描述"""
        async with self._lock:
            if (why := self._need_page()):
                return why
            page = await self._ensure()
            import base64
            raw = await page.screenshot(type="jpeg", quality=60, full_page=False)
            return f"【{await page.title()}】{page.url}\nIMAGE image/jpeg {base64.b64encode(raw).decode()}"

    def _render_elements(self) -> str:
        if not self._elements:
            return "（这一屏没找到能点的东西）"
        lines = []
        for e in self._elements:
            line = f"[{e['ref']}] {e['kind']}「{e.get('name') or '（没写字）'}」"
            if e.get("value"):
                line += f"（里面现在是：{e['value']}）"
            if e.get("below"):
                line += " ↓"
            lines.append(line)
        return "这一屏能点 / 能填的（用编号）：\n" + "\n".join(lines)

    def _render(self, part: int, size: int) -> str:
        text = self._text[:MAX_CHARS]
        total = max(1, -(-len(text) // size))
        part = min(max(1, part), total)
        body = text[(part - 1) * size: part * size]
        head = f"【{self._title or '（无标题）'}】{self._url}\n"
        if total > 1:
            head += f"正文第 {part} / {total} 段（desk_read part=… 接着看）\n"
        return head + "\n" + (body or "（这一页没有可读的正文）") + "\n\n" + self._render_elements()


TOOLS = [
    {"name": "desk.open", "description": "打开网址"},
    {"name": "desk.read", "description": "读当前页的第 part 段 + 这一屏能点的"},
    {"name": "desk.click", "description": "点编号 ref"},
    {"name": "desk.type", "description": "在编号 ref 的输入框里填字，submit=回车"},
    {"name": "desk.scroll", "description": "上下翻一屏"},
    {"name": "desk.back", "description": "退回上一页"},
    {"name": "desk.look", "description": "截这一屏"},
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
            elif name == "desk.click":
                text = await browser.click(int(args.get("ref")))
            elif name == "desk.type":
                text = await browser.type(int(args.get("ref")), str(args.get("text") or ""), bool(args.get("submit")))
            elif name == "desk.scroll":
                text = await browser.scroll(str(args.get("direction") or "down"))
            elif name == "desk.back":
                text = await browser.back()
            elif name == "desk.look":
                text = await browser.look()
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"没有这件能力：{name}"}}
            log.info("%s 完成", name)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不认识的方法：{method}"}}
    except Exception as exc:  # noqa: BLE001 —— 一次失败如实回给他，不断链
        why = (str(exc).splitlines() or [type(exc).__name__])[0][:300]
        log.warning("%s 失败：%s", method, why)
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": why}}


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
