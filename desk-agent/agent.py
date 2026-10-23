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
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
from typing import Any

def _sibling(name: str) -> Any:
    """登录那一套在旁边的 desk_login.py（部署时两个文件一起放进 /opt/nox-desk）。
    ⚠️ 按文件路径载入，不往 sys.path 里塞目录 —— 塞了的话测试进程里这个目录的 agent.py 会盖掉 Core 的 agent 包"""
    if name in sys.modules:
        return sys.modules[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


desk_login = _sibling("desk_login")

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
#: 给她看的帧：半尺寸（1280×900 → 640×450）、质量 55，一帧几十 KB，手机上每秒拉一张也不卡
FRAME_SCALE = 0.5
#: 截屏回包里图那一行的开头（look / frame）—— 抹值只抹它前面那段，图本身不动
IMAGE_SEP = "\nIMAGE "
FRAME_QUALITY = 55


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
        #: 最近一帧（v1.5 她看我屏幕用）。动作做到一半时拿这张，不去抢正在导航的页面
        self._frame = ""
        #: v2 登录：发出去还没填的那张卡（私钥跟着它）、凭证保护（她填的值，只用来抹原文）
        self._card: desk_login.Card | None = None
        self._protect: desk_login.Protection | None = None

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
        await self._grab()

    async def _grab(self) -> str:
        """截半尺寸的一帧存起来（给她看的，不给他看 —— 他看页面结构）。截不成留着上一帧"""
        page = self._page
        cover = await self._cover(page)
        try:
            cdp = await self._ctx.new_cdp_session(page)
            try:
                x, y = await page.evaluate("() => [scrollX, scrollY]")
                vp = page.viewport_size or {"width": 1280, "height": 900}
                shot = await cdp.send("Page.captureScreenshot", {
                    "format": "jpeg", "quality": FRAME_QUALITY,
                    "clip": {"x": x, "y": y, "width": vp["width"], "height": vp["height"], "scale": FRAME_SCALE}})
            finally:
                await cdp.detach()
            self._frame = shot["data"]
        except Exception as exc:  # noqa: BLE001 —— 截不成不耽误他干活，日志留一笔
            log.warning("截帧失败：%s", (str(exc).splitlines() or [type(exc).__name__])[0][:200])
            try:
                raw = await page.screenshot(type="jpeg", quality=FRAME_QUALITY, full_page=False)
                self._frame = base64.b64encode(raw).decode()
            except Exception as exc2:  # noqa: BLE001
                log.warning("整屏截图也失败：%s", (str(exc2).splitlines() or [type(exc2).__name__])[0][:200])
        finally:
            await self._uncover(cover)
        return self._frame

    async def frame(self) -> str:
        """她在看我屏幕：手上没活就现截一张；正在做动作（锁着）就回最近那张，不排队"""
        if not self._url:
            return "（还没打开任何页面）"
        if not self._lock.locked():
            async with self._lock:
                if self._page is not None and not self._page.is_closed():
                    await self._grab()
        return f"【{self._title}】{self._url}\nIMAGE image/jpeg {self._frame}" if self._frame else "（还没有画面）"

    def _need_page(self) -> str | None:
        """每个动作开头问一句：能不能动这一页。🔴 登录卡发出去还没填的那段时间（「登录中」）一律不看不碰"""
        if self._login_pending():
            return "正在登录：糖糖在她那边填表，这一页先不看也不碰。等她填完（或者取消）再说。"
        return None if self._url else "现在没有打开的页面，先用 desk_open 打开一个。"

    def _login_pending(self) -> bool:
        if self._card is not None and self._card.expired():
            log.info("登录卡过期作废（私钥销毁）")
            self._card = None
        return self._card is not None

    def _protected(self) -> desk_login.Protection | None:
        p = self._protect
        if p is not None and not p.active():
            log.info("凭证保护到兜底上限 / 已结束，值丢掉")
            self._protect = p = None
        return p

    def scrub(self, text: str) -> str:
        """🔴 交出去的所有文字过一遍：凭证保护期间，她填过的值原文抹成 ••••（网页把密码抄到正文里也抹）"""
        p = self._protected()
        return p.scrub(text) if p else text

    async def _cover(self, page: Any) -> Any:
        """截图前：登录中 / 凭证保护期间，把凭证框涂成实心块。回一个之后要拿掉的 style 元素"""
        if not (self._login_pending() or self._protected()):
            return None
        try:
            return await page.add_style_tag(content=desk_login.COVER_CSS)
        except Exception as exc:  # noqa: BLE001
            log.warning("截图前涂凭证框失败：%s", type(exc).__name__)
            return None

    @staticmethod
    async def _uncover(handle: Any) -> None:
        if handle is not None:
            try:
                await handle.evaluate("el => el.remove()")
            except Exception:  # noqa: BLE001 —— 页面已经换了，style 跟着没了
                pass

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
            if self._login_pending():
                #: 他换页了 = 那张登录卡对应的页面没了：作废（她那边再点就会说卡没了）
                log.info("登录中他打开了别的网址，登录卡作废")
                self._card = None
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

    async def click(self, ref: int, expect: dict | None = None) -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            await self._ensure()
            loc = await self._check_ref(ref)
            await self._expect(ref, expect)
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

    async def type(self, ref: int, text: str, submit: bool = False, expect: dict | None = None) -> str:
        async with self._lock:
            if (why := self._need_page()):
                return why
            page = await self._ensure()
            loc = await self._check_ref(ref)
            await self._expect(ref, expect)
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
            cover = await self._cover(page)
            try:
                raw = await page.screenshot(type="jpeg", quality=60, full_page=False)
            finally:
                await self._uncover(cover)
            return f"【{await page.title()}】{page.url}\nIMAGE image/jpeg {base64.b64encode(raw).decode()}"

    # ---------------- v2 登录（设计稿 5.2：她填表、我不经手）----------------

    async def login_begin(self) -> str:
        """这一页要登录：认出登录框、生成这张卡的一次性钥匙。回给 Core 的是 JSON（字段细节只去她的卡，Core 不交给他）"""
        async with self._lock:
            if (why := self._need_page()):
                raise ValueError(why)
            page = await self._ensure()
            if (why := desk_login.check_origin(page.url)):
                raise ValueError(why)
            fields = await page.evaluate(desk_login.FIND_FIELDS_JS)
            kinds = {f["kind"] for f in fields}
            #: 能登：有密码框；或者只有一个验证码框（二次验证那一步）。手机号 + 短信验证码这种这一版不做
            if "pass" not in kinds and kinds != {"code"}:
                if await page.evaluate(desk_login.HAS_FRAME_PASSWORD_JS):
                    raise ValueError("登录框在页面里嵌着的另一个框（iframe）里，这一版还不支持在那里登录。")
                if "user" in kinds:
                    raise ValueError("这一页像是手机号 + 短信验证码登录，这一版只支持账号密码登录 —— 找找有没有「密码登录」切过去。")
                raise ValueError("这一页没找到登录框。先打开网站的登录页。")
            card = desk_login.new_card(page.url, page, fields)
            self._card = card
            log.info("发了一张登录卡 %s（%s，%d 个框）", card.login_id, card.origin, len(fields))
            return json.dumps({"login_id": card.login_id, "origin": card.origin, "fields": fields,
                               "public_key": card.public_spki, "expires_in": desk_login.CARD_TTL_S},
                              ensure_ascii=False)

    async def login_fill(self, login_id: str, epk: str, iv: str, ct: str) -> str:
        """她点了「登录」：解密 → 核对还是不是发卡时那一页 → 一个框一个框填（每填一个前再核对一次）→ 回车 → 看结果"""
        async with self._lock:
            card = self._card if self._login_pending() else None
            if card is None or card.login_id != login_id:
                raise ValueError("这张登录卡已经作废了（过期、他换了页，或者已经用过）。让他重新发一张。")
            self._card = None    # 🔴 私钥用一次就扔：同一张卡的密文不可能被用第二次
            values = desk_login.decrypt(card, epk, iv, ct)
            page = self._page
            if page is not card.page or page.is_closed() or desk_login.origin_of(page.url) != card.origin:
                raise ValueError("发卡之后页面换了（跳走了或者关了），没填。让他重新发一张。")
            #: 凭证保护从这一刻开始（二次验证是同一次登录：值并在一起）
            prev = self._protect.values if (self._protect and self._protect.origin == card.origin) else []
            self._protect = desk_login.Protection(origin=card.origin, values=prev + [v for v in values.values() if v],
                                                  until=time.time() + desk_login.PROTECT_MAX_S)
            last = None
            for f in card.fields:
                v = values.get(f["key"])
                if not v:
                    continue
                if desk_login.origin_of(page.url) != card.origin:
                    raise ValueError("填到一半页面跳走了，剩下的没填。")
                loc = page.locator(f'[data-nox-cred="{f["key"]}"]')
                if await loc.count() != 1:
                    raise ValueError("页面上的登录框变了（被换掉了），没填。让他重新发一张。")
                await loc.fill(v, timeout=8000)
                last = loc
            if last is None:
                raise ValueError("卡上什么都没填。")
            await last.press("Enter")
            await asyncio.sleep(1.2)
            await self._settle(page)
            return await self._login_result(card)

    async def _login_result(self, card: desk_login.Card) -> str:
        page = self._page
        still = await page.evaluate(
            "() => [...document.querySelectorAll('input[type=password]')].some(e => e.getBoundingClientRect().width > 1)")
        site = desk_login.site_of(desk_login.urlsplit(page.url).hostname or "")
        if not still:
            fields = await page.evaluate(desk_login.FIND_FIELDS_JS)
            if any(f["kind"] == "code" for f in fields) and desk_login.origin_of(page.url) == card.origin:
                nxt = desk_login.new_card(page.url, page, [f for f in fields if f["kind"] == "code"])
                self._card = nxt
                log.info("登录要二次验证，接着发验证码卡 %s", nxt.login_id)
                return json.dumps({"status": "more", "site": site, "message": "还要输一个验证码",
                                   "card": {"login_id": nxt.login_id, "origin": nxt.origin, "fields": nxt.fields,
                                            "public_key": nxt.public_spki, "expires_in": desk_login.CARD_TTL_S}},
                                  ensure_ascii=False)
            #: 登进去了、也离开了登录页 → 凭证保护结束，值丢掉
            self._protect = None
            await self._refresh()
            log.info("登录成功：%s", site)
            return json.dumps({"status": "ok", "site": site, "origin": card.origin, "message": "登进去了"},
                              ensure_ascii=False)
        #: 还在登录页 = 没登进去。找一句页面上的报错给她（抹过值）；值丢掉
        text = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        why = next((ln.strip() for ln in text.splitlines()
                    if re.search(r"错误|不正确|失败|有误|不存在|invalid|incorrect|wrong|failed", ln, re.I)
                    and len(ln.strip()) <= 80), "")
        why = self.scrub(why)
        self._protect = None
        await self._refresh()
        log.info("登录没成：%s", site)
        return json.dumps({"status": "failed", "site": site, "origin": card.origin,
                           "message": f"没登进去（页面说：{why}）" if why else "没登进去（页面没说为什么）"},
                          ensure_ascii=False)

    async def login_cancel(self, login_id: str) -> str:
        async with self._lock:
            if self._card is not None and self._card.login_id == login_id:
                self._card = None
                log.info("她取消了登录卡 %s", login_id)
            return "好，不登了。"

    async def logout(self, site: str) -> str:
        """退出一个站：清掉它（和它子域名）的登录状态"""
        site = (site or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", site):
            raise ValueError("站点名不对")
        async with self._lock:
            await self._ensure()
            await self._ctx.clear_cookies(domain=re.compile(r"(^|\.)" + re.escape(site) + r"$"))
            log.info("退出了 %s（清了它的 cookie）", site)
            return f"退出了 {site}"

    async def describe(self, ref: int) -> str:
        """编号 ref 是个什么东西（给她的确认卡用）：{kind, name, url}"""
        async with self._lock:
            if (why := self._need_page()):
                raise ValueError(why)
            await self._ensure()
            await self._check_ref(ref)
            e = next((e for e in self._elements if e.get("ref") == ref), {})
            return json.dumps({"kind": e.get("kind", ""), "name": e.get("name", ""), "url": self._page.url},
                              ensure_ascii=False)

    async def _expect(self, ref: int, expect: dict | None) -> None:
        """确认卡点完再做时：编号现在指的还得是卡上写的那个东西（同一个名字、同一个网址），不然不做"""
        if not expect:
            return
        e = next((e for e in self._elements if e.get("ref") == ref), {})
        if e.get("name", "") != expect.get("name", "") or self._page.url != expect.get("url", self._page.url):
            raise LookupError("页面变了：现在这个编号不是卡上那个东西了，没做。让他重新看一下再发卡。")

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
        return self.scrub("这一屏能点 / 能填的（用编号）：\n" + "\n".join(lines))

    def _render(self, part: int, size: int) -> str:
        text = self._text[:MAX_CHARS]
        total = max(1, -(-len(text) // size))
        part = min(max(1, part), total)
        body = text[(part - 1) * size: part * size]
        head = f"【{self._title or '（无标题）'}】{self._url}\n"
        if total > 1:
            head += f"正文第 {part} / {total} 段（desk_read part=… 接着看）\n"
        #: 🔴 在源头抹（不只靠 handle 那一层）：凭证保护期间网页正文 / 输入框里的值出现她填的原文 → ••••
        return self.scrub(head + "\n" + (body or "（这一页没有可读的正文）") + "\n\n" + self._render_elements())


TOOLS = [
    {"name": "desk.open", "description": "打开网址"},
    {"name": "desk.read", "description": "读当前页的第 part 段 + 这一屏能点的"},
    {"name": "desk.click", "description": "点编号 ref"},
    {"name": "desk.type", "description": "在编号 ref 的输入框里填字，submit=回车"},
    {"name": "desk.scroll", "description": "上下翻一屏"},
    {"name": "desk.back", "description": "退回上一页"},
    {"name": "desk.look", "description": "截这一屏"},
    {"name": "desk.frame", "description": "最近一帧（半尺寸，给她看我屏幕用）"},
    {"name": "desk.login_begin", "description": "这一页要登录：发一张登录卡（一次性公钥）"},
    {"name": "desk.login_fill", "description": "她填好的密文：解开、核对、填、回车（只有 Core 的确认端点会调）"},
    {"name": "desk.login_cancel", "description": "她取消了登录卡"},
    {"name": "desk.logout", "description": "退出一个站（清它的 cookie）"},
    {"name": "desk.describe", "description": "编号 ref 是个什么（给确认卡用）"},
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
                text = await browser.click(int(args.get("ref")), args.get("expect"))
            elif name == "desk.type":
                text = await browser.type(int(args.get("ref")), str(args.get("text") or ""), bool(args.get("submit")),
                                          args.get("expect"))
            elif name == "desk.login_begin":
                text = await browser.login_begin()
            elif name == "desk.login_fill":
                text = await browser.login_fill(str(args.get("login_id") or ""), str(args.get("epk") or ""),
                                                str(args.get("iv") or ""), str(args.get("ct") or ""))
            elif name == "desk.login_cancel":
                text = await browser.login_cancel(str(args.get("login_id") or ""))
            elif name == "desk.logout":
                text = await browser.logout(str(args.get("site") or ""))
            elif name == "desk.describe":
                text = await browser.describe(int(args.get("ref")))
            elif name == "desk.scroll":
                text = await browser.scroll(str(args.get("direction") or "down"))
            elif name == "desk.back":
                text = await browser.back()
            elif name == "desk.look":
                text = await browser.look()
            elif name == "desk.frame":
                head, sep, img = (await browser.frame()).partition(IMAGE_SEP)
                return {"jsonrpc": "2.0", "id": mid,
                        "result": {"content": [{"type": "text", "text": browser.scrub(head) + sep + img}]}}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"没有这件能力：{name}"}}
            log.info("%s 完成", name)
            #: 🔴 交出去之前抹一遍（凭证保护期间，网页正文 / 能点的清单 / 报错里出现她填过的值都抹掉）。
            #: 截屏那行的图不动（抹 base64 会把图弄坏；图里的凭证框截之前已经涂掉了）
            head, sep, img = text.partition(IMAGE_SEP)
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": browser.scrub(head) + sep + img}]}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不认识的方法：{method}"}}
    except Exception as exc:  # noqa: BLE001 —— 一次失败如实回给他，不断链
        why = browser.scrub((str(exc).splitlines() or [type(exc).__name__])[0][:300])
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

        async def reply(msg: dict[str, Any]) -> None:
            try:
                out = await handle(browser, msg)
            except Exception as exc:  # noqa: BLE001 —— 一定要回一句：不回的话 Core 那边干等到超时（10-23 测试里卡死过）
                log.error("处理 %s 时出了意外：%s", msg.get("method"), type(exc).__name__)
                out = None if msg.get("id") is None else {
                    "jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": f"我电脑那边出了意外（{type(exc).__name__}）"}}
            if out is not None:
                await ws.send(json.dumps(out, ensure_ascii=False))

        #: 每条请求各起一个任务（v1.5）：她要看一帧时，不用排在我一个 20 秒的导航后面。
        #: 动作之间照旧靠 Browser._lock 一件一件来
        pending: set[asyncio.Task] = set()
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            task = asyncio.create_task(reply(msg))
            pending.add(task)
            task.add_done_callback(pending.discard)


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
