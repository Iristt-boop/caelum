"""配置层的只读视图（P1）：给 `/api/nox/config/*` 和 `caelum_map` 用。

**只读、不含密钥。** 这一层只回答「有哪些厂商、各自缓存怎么算、key 在不在、哪个槽位指向谁」：

  · key 只说**配没配**（布尔），连最后几位都不给；回去哪个环境变量里取（`key_envs`）是名字，不是密钥
  · 地址只留 `scheme://host[:port]/path`：账号密码、查询串（`?key=…`）、片段一律剥掉 ——
    将来有人把 key 塞进地址里，这里也不会把它送给前端
  · 槽位的 `params` 只放白名单字段（`max_tokens`）

读的是 `settings.db`（P0 起每次启动由代码 + 环境变量灌出来、并和现状逐字段对过账），
所以 P1 里这些视图与线上实际在用的配置一致 —— 不一致时 P0 的影子账本会先报警。
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from settings_store.probe import status_of, target_of
from settings_store.seed import SLOT_ATTRS, resolve_key
from settings_store.store import SettingsStore

#: 槽位的展示顺序：先三个已知的，其余（将来的 tts / stt …）按名字排在后面
SLOT_ORDER = [name for name, _ in SLOT_ATTRS]


def safe_url(url: str | None) -> str:
    """去掉账号密码、查询串、片段。解析不了就返回空串（宁可不显示，也不原样送出去）。"""
    if not url:
        return ""
    try:
        u = urlsplit(url)
        host = u.hostname or ""
        if u.port:
            host += f":{u.port}"
    except ValueError:
        return ""
    return urlunsplit((u.scheme, host, u.path, "", ""))


def _key_source(pid: str, key_envs: list[str], env: Mapping[str, str], front: set[str]) -> str | None:
    """key 从哪来：她在前端填的优先（P2b），其次环境变量；都没有 = None。**只回来源，不回值。**"""
    if pid in front:
        return "front"
    if resolve_key(key_envs, env):
        return "env"
    return None


def providers_view(store: SettingsStore, env: Mapping[str, str] | None = None,
                   keys: Any = None) -> dict[str, Any]:
    env = os.environ if env is None else env
    front = keys.configured() if keys is not None else set()
    per_provider = Counter(m["provider_id"] for m in store.models())
    items = []
    for p in store.providers():
        items.append({
            "id": p["id"],
            "label": p["label"],
            "protocol": p["protocol"],
            "base_url": safe_url(p["base_url"]),
            "cache_style": p["cache_style"],
            "auth": p["auth"],
            "source": p["source"],                      # seed = 来自代码 + 环境变量；user = 前端改的（P2 起）
            "note": p["note"],
            "key_envs": list(p["key_envs"]),            # 环境变量**名字**
            "key_configured": _key_source(p["id"], list(p["key_envs"]), env, front) is not None,
            "key_source": _key_source(p["id"], list(p["key_envs"]), env, front),   # front | env | None
            "models": per_provider.get(p["id"], 0),
        })
        # P2c：真测结论 → 徽章（缺失 / 未测 / 正常 / 失败）
        items[-1].update(status_of(store, target_of(p["id"]), items[-1]["key_configured"]))
    return {"providers": items, "shadow": store.shadow_summary(7)}


def slots_view(store: SettingsStore, env: Mapping[str, str] | None = None,
               keys: Any = None) -> dict[str, Any]:
    env = os.environ if env is None else env
    front = keys.configured() if keys is not None else set()
    rows = {s["slot"]: s for s in store.slots()}
    order = [n for n in SLOT_ORDER if n in rows] + sorted(n for n in rows if n not in SLOT_ORDER)
    items = []
    for name in order:
        s = rows[name]
        items.append(_slot_item(store, s, env, front))
    return {"slots": items}


def _slot_item(store: SettingsStore, s: dict[str, Any], env: Mapping[str, str],
               front: set[str] = frozenset()) -> dict[str, Any]:
    """一个槽位。指不到东西时**如实报坏**（`ok: False`），不用空值顶替 ——「读不到」和「没有」要分开。"""
    name = s["slot"]
    targets = s.get("targets") or []
    if not targets:
        return {"slot": name, "ok": False, "error": "槽位里没有任何目标"}
    t = targets[0]
    m = store.model(t.get("model_id"))
    if m is None:
        return {"slot": name, "ok": False, "error": "槽位指向的型号在库里找不到"}
    p = store.provider(m["provider_id"])
    if p is None:
        return {"slot": name, "ok": False, "error": "型号所属的厂商在库里找不到"}
    params = t.get("params") or {}
    key_envs = list(params.get("key_envs", p["key_envs"]))
    return {
        "slot": name,
        "ok": True,
        "provider": {"id": p["id"], "label": p["label"], "cache_style": p["cache_style"],
                     "protocol": params.get("provider") or p["protocol"]},
        "model": {"name": m["model_name"], "label": m["label"], "short_name": m["short_name"],
                  "capabilities": m["capabilities"]},
        "max_tokens": params.get("max_tokens"),
        "key_configured": p["id"] in front or bool(resolve_key(key_envs, env)),
        "updated_by": s["updated_by"],
    }


def models_view(store: SettingsStore, env: Mapping[str, str] | None = None, keys: Any = None) -> dict[str, Any]:
    """所有型号 + 各自的真测徽章（P2c）。缓存打法给的是**实际生效**的那个（型号覆盖 > 厂商）。"""
    env = os.environ if env is None else env
    front = keys.configured() if keys is not None else set()
    provs = {p["id"]: p for p in store.providers()}
    items = []
    for m in store.models():
        p = provs.get(m["provider_id"])
        if p is None:
            continue
        has_key = _key_source(p["id"], list(p["key_envs"]), env, front) is not None
        items.append({
            "provider_id": p["id"], "provider_label": p["label"],
            "model_name": m["model_name"], "short_name": m["short_name"], "label": m["label"],
            "capabilities": m["capabilities"], "origin": m["origin"], "enabled": bool(m["enabled"]),
            "cache_style": m.get("cache_style") or p["cache_style"], "cache_ttl": m.get("cache_ttl"),
            **status_of(store, target_of(p["id"], m["model_name"]), has_key),
        })
    return {"models": items}
