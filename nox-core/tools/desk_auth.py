"""我的电脑 v2：登录与登录态的写动作（2026-10-23，设计稿 5.2；desk-agent 那半在 desk-agent/desk_login.py）。

Core 在这里只是**转手和记账**：
  · 登录卡：desk-agent 发的卡（一次性公钥 + 要填哪几个框）原样给她的手机；她交回来的是**密文**，原样交回 desk-agent。
    🔴 Core 从头到尾见不到明文；框的细节只进她的卡，**不进他的工具结果**
  · 她认可过的网站（完整 origin）、我登着的站（主域名）—— data/desk-login.json，她能看能退
  · 登录着的站上，他要点 / 要填 → 不直接做，出一张确认卡；她点「确认」才由确认端点去做（同瑞幸 R8）
  · 结果记一条给他的话，他下一次用电脑时看到（他那一轮早就结束了）
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_MULTI = {"com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "co.uk", "org.uk", "ac.uk",
          "com.hk", "com.tw", "co.jp", "ne.jp", "or.jp", "com.au", "net.au", "co.kr", "com.sg", "com.my"}

#: 这一类按钮确认卡也不放行（设计稿 5.3：花钱的整类不做，付款永远在她手机上）
PAY_RX = re.compile(r"付款|支付|结算|提交订单|确认订单|立即购买|立即下单|去下单|下单|pay|checkout|place order|buy now|purchase",
                    re.I)

ACTION_TTL_S = 300


def site_of(host: str) -> str:
    """主域名（和 desk-agent/desk_login.py 同一口径）：login.taobao.com → taobao.com"""
    host = (host or "").lower().rstrip(".")
    parts = host.split(".")
    if len(parts) <= 2 or host.replace(".", "").isdigit():
        return host
    return ".".join(parts[-3:]) if ".".join(parts[-2:]) in _MULTI else ".".join(parts[-2:])


def host_of_origin(origin: str) -> str:
    return (urlsplit(origin).hostname or "").lower()


class DeskAuth:
    """登录卡 / 认可的站 / 登着的站 / 确认卡 / 给他的话。线程安全（工具线程和接口线程都会碰）"""

    def __init__(self, path: Path | None, clock: Any = time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._card: dict | None = None
        self._action: dict | None = None
        self._notes: list[str] = []
        self._url = ""
        self._allowed: dict[str, float] = {}
        self._signed: dict[str, dict] = {}
        self._load()

    # ---- 落盘：认可过的 origin、登着的站（没有任何账号密码）----
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self._allowed = {str(k): float(v) for k, v in (d.get("allowed") or {}).items()}
            self._signed = {str(k): dict(v) for k, v in (d.get("signed_in") or {}).items()}
        except (OSError, ValueError) as exc:
            logger.warning("读不了 desk-login.json（当作一个站都没认可、没登）：%s", exc)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"allowed": self._allowed, "signed_in": self._signed},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("存不了 desk-login.json：%s", exc)

    # ---- 他现在在哪一页（从每次工具结果的「【标题】网址」那行来）----
    def saw(self, url: str) -> None:
        with self._lock:
            self._url = url or self._url

    def signed_in_here(self) -> str | None:
        """现在这页属于一个我登着的站吗 → 回主域名"""
        with self._lock:
            host = (urlsplit(self._url).hostname or "").lower()
            s = site_of(host) if host else ""
            return s if s and s in self._signed else None

    # ---- 登录卡 ----
    def add_card(self, card: dict) -> dict:
        """desk-agent 发的卡 → 存一份给她看的。新卡顶掉旧卡（desk-agent 那边也只有一张）"""
        host = host_of_origin(card.get("origin", ""))
        view = {
            "id": str(card["login_id"]),
            "origin": card.get("origin", ""),
            "host": host,
            "site": site_of(host),
            "fields": [{"key": f.get("key"), "kind": f.get("kind"), "label": str(f.get("label") or "")[:30]}
                       for f in card.get("fields") or []],
            "public_key": card.get("public_key", ""),
            "expires_at": self._clock() + float(card.get("expires_in") or 180),
        }
        with self._lock:
            view["first_seen"] = view["origin"] not in self._allowed
            view["site_known"] = any(site_of(host_of_origin(o)) == view["site"] for o in self._allowed)
            self._card = view
        return view

    def card(self, cid: str | None = None) -> dict | None:
        with self._lock:
            c = self._card
            if c is not None and self._clock() > c["expires_at"]:
                self._card = c = None
            return c if c is not None and (cid is None or c["id"] == cid) else None

    def drop_card(self, cid: str) -> None:
        with self._lock:
            if self._card is not None and self._card["id"] == cid:
                self._card = None

    def allow(self, origin: str) -> None:
        with self._lock:
            self._allowed[origin] = self._clock()
            self._save()

    def is_allowed(self, origin: str) -> bool:
        with self._lock:
            return origin in self._allowed

    def signed_in(self, site: str, origin: str) -> None:
        with self._lock:
            self._signed[site] = {"origin": origin, "at": self._clock()}
            self._save()

    def signed_out(self, site: str) -> bool:
        with self._lock:
            had = self._signed.pop(site, None) is not None
            self._save()
            return had

    def sites(self) -> list[dict]:
        with self._lock:
            return [{"site": s, "origin": v.get("origin", ""), "at": v.get("at")}
                    for s, v in sorted(self._signed.items(), key=lambda kv: -float(kv[1].get("at") or 0))]

    # ---- 登录着的站上的写动作：确认卡 ----
    def add_action(self, kind: str, args: dict, desc: dict, site: str) -> dict:
        name = str(desc.get("name") or "")
        if kind == "desk_click":
            text = f"在 {site} 点「{name or '（没写字的按钮）'}」"
        else:
            text = f"在 {site} 的「{name or '输入框'}」里填「{str(args.get('text') or '')[:60]}」" + \
                   ("，然后按回车" if args.get("submit") else "")
        a = {"id": secrets.token_hex(6), "kind": kind, "args": dict(args), "site": site, "text": text,
             "expect": {"name": name, "url": desc.get("url", "")}, "expires_at": self._clock() + ACTION_TTL_S}
        with self._lock:
            self._action = a
        return a

    def action(self, aid: str | None = None) -> dict | None:
        with self._lock:
            a = self._action
            if a is not None and self._clock() > a["expires_at"]:
                self._action = a = None
            return a if a is not None and (aid is None or a["id"] == aid) else None

    def take_action(self, aid: str) -> dict | None:
        """原子地取走（她连点两次只做一次）"""
        with self._lock:
            a = self._action
            if a is None or a["id"] != aid or self._clock() > a["expires_at"]:
                return None
            self._action = None
            return a

    # ---- 给他的话（他那一轮早结束了：下一次用电脑时带上）----
    def note(self, text: str) -> None:
        with self._lock:
            self._notes = (self._notes + [text])[-5:]

    def take_notes(self) -> str:
        with self._lock:
            notes, self._notes = self._notes, []
        return "".join(f"（{n}）\n" for n in notes)

    # ---- 给她看的状态（胶囊那个接口带上）----
    def public(self) -> dict:
        c, a = self.card(), self.action()
        return {
            "login": c,
            "confirm": {"id": a["id"], "text": a["text"], "site": a["site"]} if a else None,
        }
