"""档案：让 adapter 读 settings.db 的字段，不再按名字猜（配置层 P2a，2026-10-11）。

P2a 只接三个字段，全是「今天靠嗅探的知识」：
  · 缓存怎么打 —— `providers.cache_style`，型号可覆盖（`models.cache_style`）
  · 断点有效期 —— `models.cache_ttl`
  · 能不能直接收图 —— `models.capabilities` 里有没有 vision

P2b 起还接 **key**：她在前端填过的（`settings.keys`）优先于环境变量；清掉就回到环境变量。
地址、型号名**不从这里改**（还是环境变量 / BACKENDS 说了算，P2d 起才有前端写的厂商）。

## 什么时候不用档案（adapter 照旧嗅探，行为与 P2 之前逐字节一致）

  · `NOX_CONFIG_STORE=off`（也认 0 / false / no）
  · 库没起来（启动钩子失败）
  · **影子对账有不一致**：库和现状对不上，就不该让库说话
  · 档案里找不到这条线路（厂商按 base_url + 协议找；型号按名字找）
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Any

from settings_store.keys import KeyStore
from settings_store.store import SettingsStore

logger = logging.getLogger("settings_store.profile")

_OFF = ("off", "0", "false", "no")


def enabled() -> bool:
    return (os.environ.get("NOX_CONFIG_STORE") or "").strip().lower() not in _OFF


def _provider_for(store: SettingsStore, llm: Any) -> dict[str, Any] | None:
    url = (llm.base_url or "").rstrip("/")
    cands = [p for p in store.providers() if (p["base_url"] or "").rstrip("/") == url]
    return next((p for p in cands if p["protocol"] == llm.provider), cands[0] if cands else None)


def apply(store: SettingsStore, llm: Any, keys: KeyStore | None = None) -> Any:
    """返回填好档案字段的 `LLMConfig`（新对象）。找不到厂商就原样返回 —— 走老路，不猜。

    `llm` 应当是**环境变量建出来的那份**（见 `attach` 里的 `_env_cfg`）：key 清掉之后要能回到它。
    """
    p = _provider_for(store, llm)
    if p is None:
        logger.info("档案里没有 %s 这条线路（%s），照旧按名字判断", llm.model, llm.base_url or "默认地址")
        return llm
    fields: dict[str, Any] = {"cache_style": p["cache_style"]}
    front_key = keys.get(p["id"]) if keys is not None else ""
    if front_key:
        fields["api_key"] = front_key                   # 前端 > 环境变量
    m = next((x for x in store.models()
              if x["provider_id"] == p["id"] and x["model_name"] == llm.model), None)
    if m is not None:
        fields.update(
            cache_style=m.get("cache_style") or p["cache_style"],
            cache_ttl=m.get("cache_ttl") if m.get("cache_ttl") is not None else "",
            vision="vision" in (m.get("capabilities") or []),
        )
    # 厂商在、型号不在：缓存跟厂商走；能力不知道 → 不填，交给老规则（别把没测过的标成能看图）
    return dataclasses.replace(llm, **fields)


def _reapply(a: Any, store: SettingsStore, keys: KeyStore | None) -> None:
    """对一个已经建好的 adapter 重新套档案。key 变了要**重建客户端**（SDK 客户端建的时候就把 key 捏在手里了）。"""
    base = getattr(a, "_env_cfg", None) or a.cfg
    new = apply(store, base, keys)
    if new.api_key != a.cfg.api_key:
        a.__init__(new)                                 # 同一个对象：Router / Archiver 手里那个跟着换
    else:
        a.cfg = new
    a._env_cfg = base


def attach(core: Any, store: SettingsStore, keys: KeyStore | None = None) -> int:
    """启动时把档案套到已经建好的 adapter 上（**就地**换 `cfg`）。返回套了几个。

    就地换而不是重建：Router 和 Archiver 持有的是同一个杂活 adapter（Archiver 那个外面
    包了一层计量），重建一个新的只会换掉一边。
    """
    seen: set[int] = set()
    n = 0
    loop = getattr(core, "loop", None)
    router = getattr(core, "router", None)
    for a in (getattr(loop, "adapter", None), getattr(router, "light_adapter", None)):
        a = getattr(a, "_inner", a)          # 计量包装（agent/meter.Metered）：往它身上赋值只会落在包装上
        if hasattr(a, "swap") and hasattr(a, "current"):
            a = a.current()                  # 槽位代理（settings_store/slots.SlotAdapter）：套到里面那个上
        if a is None or id(a) in seen or not hasattr(a, "cfg"):
            continue
        seen.add(id(a))
        _reapply(a, store, keys)
        n += 1
    # 按请求切的模型（adapter_for 缓存的）丢掉，下一次按档案重建
    cache = getattr(core, "_adapters", None)
    if isinstance(cache, dict):
        cache.clear()
    # 之后新建的 adapter（按请求切模型、server.py 里各路杂活）在工厂里套：同一条路径，不靠每个调用点记得
    from agent import adapters
    adapters.use_profiles(lambda llm: apply(store, llm, keys))
    core.profiles = store
    core.config_keys = keys
    return n


def refresh(core: Any) -> int:
    """她改了 key 之后：已经建好的主线 / 杂活按新档案重套，按请求切的模型缓存丢掉。没装档案就什么都不做。"""
    store = getattr(core, "profiles", None)
    if store is None:
        return 0
    n = attach(core, store, getattr(core, "config_keys", None))
    # 换了 key / 地址：她在 Slots 里选的杂活要按新档案重建（P3）
    from settings_store import slots
    slots.apply(core)
    return n
