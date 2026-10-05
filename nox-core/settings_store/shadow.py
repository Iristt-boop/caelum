"""影子对比：从库里**重建**一份配置，与现在真正在用的那份逐字段比。

P0 的验收就靠它（设计稿第十三节）：一周内每次启动都应当「零不一致」。

## 比什么

  · providers：BACKENDS 里每一家的 protocol / base_url / key_envs / cache_style
  · models：`cfg.models` 每个短名对应的行（型号、归属厂商、label、价格）
  · slots：primary / utility / vision 重建出的 `LLMConfig` 与 `cfg.<slot>` **每个字段**
  · 反过来：库里不该有的种子行（代码里已经没有、库里还留着）

## 🔴 密钥不出现在任何输出里

api_key 只比「摘要」：`sha256:` 前 8 位，空就写「(空)」。不一致时报出来的也是摘要，**绝不是值**。
`tests/test_settings_shadow.py` 用已知标记串扫日志、返回值、shadow_log、整个库文件。

## 为什么要「从库里重建」而不是直接比种子

要的就是**走一遍 sqlite 往返**：JSON 列、外键、事务、NULL 的 UNIQUE 行为 ——
这些都可能让「写进去的」和「读出来的」悄悄不一样，而那正是 P2 切到读库时会炸的地方。
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from settings_store.seed import (
    SLOT_ATTRS, SeedReport, resolve_key, seed_cache_style,
)
from settings_store.store import SettingsStore

logger = logging.getLogger("settings_store.shadow")


def digest(secret: str | None) -> str:
    """密钥的可对账摘要。**不可逆到只能用来判断「同不同」。**"""
    s = secret or ""
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:8] if s else "(空)"


@dataclass
class Diff:
    path: str
    live: str          # 现在真正在用的（密钥字段已是摘要）
    store: str         # 库里重建出来的

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "live": self.live, "store": self.store}


@dataclass
class ShadowResult:
    n_checked: int = 0
    diffs: list[Diff] = field(default_factory=list)
    integrity_ok: bool = True

    @property
    def ok(self) -> bool:
        return self.integrity_ok and not self.diffs


def rebuild_llm(store: SettingsStore, slot: str, env: Mapping[str, str]):
    """从库里重建一个 `LLMConfig`。这就是 P2 切到读库时 `config.primary` 的来源。"""
    from config import LLMConfig

    s = store.slot(slot)
    if not s or not s["targets"]:
        return None
    t = s["targets"][0]
    m = store.model(t["model_id"])
    if m is None:
        return None
    p = store.provider(m["provider_id"])
    if p is None:
        return None
    params = t.get("params") or {}
    key = resolve_key(list(params.get("key_envs", p["key_envs"])), env)
    default = {f.name: f.default for f in dataclasses.fields(LLMConfig)}
    return LLMConfig(
        provider=params.get("provider") or p["protocol"],
        model=m["model_name"],
        api_key=key,
        base_url=p["base_url"],
        max_tokens=params.get("max_tokens", default["max_tokens"]),
    )


def compare(store: SettingsStore, cfg: Any, env: Mapping[str, str] | None = None) -> ShadowResult:
    """库 ↔ 现状，逐项对账。返回所有不一致，不抛。"""
    from config import BACKENDS, LLMConfig

    env = os.environ if env is None else env
    res = ShadowResult()

    def check(path: str, live: Any, got: Any, *, secret: bool = False) -> None:
        res.n_checked += 1
        if live != got:
            lv, gv = (digest(live), digest(got)) if secret else (repr(live), repr(got))
            res.diffs.append(Diff(path, lv, gv))

    res.integrity_ok = store.integrity_ok()
    if not res.integrity_ok:
        res.diffs.append(Diff("sqlite.integrity_check", "ok", "not ok"))

    # ---- providers ----
    rows = {p["id"]: p for p in store.providers()}
    for name, b in BACKENDS.items():
        p = rows.get(name)
        if p is None:
            res.n_checked += 1
            res.diffs.append(Diff(f"providers.{name}", "存在", "库里没有"))
            continue
        check(f"providers.{name}.protocol", b.provider, p["protocol"])
        check(f"providers.{name}.base_url", b.base_url, p["base_url"])
        check(f"providers.{name}.key_envs", list(b.key_envs), p["key_envs"])
        check(f"providers.{name}.cache_style", seed_cache_style(b.provider, b.base_url), p["cache_style"])
    extra = sorted(i for i, p in rows.items()
                   if p["source"] == "seed" and i not in BACKENDS and not i.startswith("env-"))
    check("providers.多出的种子行", [], extra)

    # ---- models ----
    by_short = {m["short_name"]: m for m in store.models() if m["short_name"]}
    live_models = getattr(cfg, "models", None) or {}
    price_table = getattr(cfg, "PRICING_CNY", None) or {}
    for short, c in live_models.items():
        m = by_short.get(short)
        if m is None:
            res.n_checked += 1
            res.diffs.append(Diff(f"models.{short}", "存在", "库里没有"))
            continue
        check(f"models.{short}.model", c.model, m["model_name"])
        check(f"models.{short}.provider", c.backend, m["provider_id"])
        check(f"models.{short}.label", c.label or c.model, m["label"])
        check(f"models.{short}.price", price_table.get(c.model), m["price"])
    check("models.多出的短名", [], sorted(set(by_short) - set(live_models)))

    # ---- slots：重建出的 LLMConfig 与现状逐字段比 ----
    for slot_name, attr in SLOT_ATTRS:
        live = getattr(cfg, attr, None)
        if live is None:
            continue
        rebuilt = rebuild_llm(store, slot_name, env)
        if rebuilt is None:
            res.n_checked += 1
            res.diffs.append(Diff(f"slots.{slot_name}", "有", "库里重建不出来"))
            continue
        for f in dataclasses.fields(LLMConfig):
            check(f"slots.{slot_name}.{f.name}", getattr(live, f.name), getattr(rebuilt, f.name),
                  secret=(f.name == "api_key"))
    return res


def run(store: SettingsStore, cfg: Any, seed_report: SeedReport,
        env: Mapping[str, str] | None = None) -> ShadowResult:
    """对账 + 记账（shadow_log）+ 打日志。**日志里只有路径和摘要。**"""
    res = compare(store, cfg, env)
    store.log_shadow(ok=res.ok, n_checked=res.n_checked, n_diff=len(res.diffs),
                     seed_added=1 if seed_report.changed else 0, detail=[d.as_dict() for d in res.diffs])
    if res.ok:
        logger.info("配置影子：%d 项逐字段一致（种子 %s，%d 厂商 / %d 型号 / %d 槽位）",
                    res.n_checked, "有变化" if seed_report.changed else "与上次相同",
                    seed_report.providers, seed_report.models, seed_report.slots)
    else:
        for d in res.diffs:
            logger.warning("配置影子不一致：%s 现状=%s 库=%s", d.path, d.live, d.store)
        logger.warning("配置影子：%d 项里 %d 项不一致（P0 只观察，不影响行为）",
                       res.n_checked, len(res.diffs))
    return res
