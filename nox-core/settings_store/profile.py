"""档案：让 adapter 读 settings.db 的字段，不再按名字猜（配置层 P2a，2026-10-11）。

P2a 只接三个字段，全是「今天靠嗅探的知识」：
  · 缓存怎么打 —— `providers.cache_style`，型号可覆盖（`models.cache_style`）
  · 断点有效期 —— `models.cache_ttl`
  · 能不能直接收图 —— `models.capabilities` 里有没有 vision

地址、型号名、key **不从这里改**（还是环境变量 / BACKENDS 说了算，P2d 起才有前端写的厂商）。

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

from settings_store.store import SettingsStore

logger = logging.getLogger("settings_store.profile")

_OFF = ("off", "0", "false", "no")


def enabled() -> bool:
    return (os.environ.get("NOX_CONFIG_STORE") or "").strip().lower() not in _OFF


def _provider_for(store: SettingsStore, llm: Any) -> dict[str, Any] | None:
    url = (llm.base_url or "").rstrip("/")
    cands = [p for p in store.providers() if (p["base_url"] or "").rstrip("/") == url]
    return next((p for p in cands if p["protocol"] == llm.provider), cands[0] if cands else None)


def apply(store: SettingsStore, llm: Any) -> Any:
    """返回填好档案字段的 `LLMConfig`（新对象）。找不到厂商就原样返回 —— 走老路，不猜。"""
    p = _provider_for(store, llm)
    if p is None:
        logger.info("档案里没有 %s 这条线路（%s），照旧按名字判断", llm.model, llm.base_url or "默认地址")
        return llm
    m = next((x for x in store.models()
              if x["provider_id"] == p["id"] and x["model_name"] == llm.model), None)
    if m is None:
        # 厂商在、型号不在：缓存跟厂商走；能力不知道 → 不填，交给老规则（别把没测过的标成能看图）
        return dataclasses.replace(llm, cache_style=p["cache_style"])
    return dataclasses.replace(
        llm,
        cache_style=m.get("cache_style") or p["cache_style"],
        cache_ttl=m.get("cache_ttl") if m.get("cache_ttl") is not None else "",
        vision="vision" in (m.get("capabilities") or []),
    )


def attach(core: Any, store: SettingsStore) -> int:
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
        if a is None or id(a) in seen or not hasattr(a, "cfg"):
            continue
        seen.add(id(a))
        a.cfg = apply(store, a.cfg)
        n += 1
    # 按请求切的模型（adapter_for 缓存的）丢掉，下一次按档案重建
    cache = getattr(core, "_adapters", None)
    if isinstance(cache, dict):
        cache.clear()
    # 之后新建的 adapter（按请求切模型、server.py 里各路杂活）在工厂里套：同一条路径，不靠每个调用点记得
    from agent import adapters
    adapters.use_profiles(lambda llm: apply(store, llm))
    core.profiles = store
    return n
