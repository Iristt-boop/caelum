"""种子：把「今天写死在代码和环境变量里的配置」灌进 settings.db。

P0 的铁律（设计稿第十三节）：**行为零变化。** 种子 = 现有 `BACKENDS` + `config.models` +
`NOX_*` 环境变量解析出的 primary / utility / vision，一点不多、一点不少。
库在 P0 里**不被读取、不生效** —— 它只是被灌进去、再读出来与现状逐字段对比（shadow.py）。

## 刷新语义（P0 的限定，别误读）

每次启动，`source/origin/updated_by = 'seed'` 的行会**整体删掉重灌**（一个事务里），
`source='user'` 的行（P2 起前端改出来的）一行都不碰。所以 P0 的影子对比证明的是：
  ① 这套表结构能**无损表示**今天的配置；
  ② 经过 sqlite 写入再读出（JSON 往返、外键、事务）之后，重建出的配置与现状逐字段相同；
  ③ 库读写健康（integrity_check、能打开、能写）。
它**不**证明「库说了算」—— 那要到 P2 出现用户改过的行才成立。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from settings_store.store import SettingsStore, _now

logger = logging.getLogger(__name__)

#: 同 `api/server.py` 的 PROVIDER_LABELS（P1 起前端读库，那处就没有存在的必要了）。
PROVIDER_LABELS = {
    "deepseek": "DeepSeek", "openrouter": "OpenRouter", "zhipu": "智谱 GLM",
    "anthropic": "Anthropic", "dashscope": "阿里百炼",
}

#: 槽位名 → `Config` 上的属性名
SLOT_ATTRS = (("chat.primary", "primary"), ("chat.utility", "utility"), ("vision", "vision"))

#: `config.py` 里只有 utility 读这个环境变量（`key_override=_env("NOX_UTILITY_API_KEY")`）。
#: 这是**照抄现状**，不是设计：config.py 变了，影子对比会立刻报不一致。
_KEY_OVERRIDE_ENVS = ("NOX_UTILITY_API_KEY",)


def seed_cache_style(protocol: str, base_url: str) -> str:
    """今天的 adapter 实际怎么做，种子就怎么填。

    - anthropic 原生：显式断点（`cache_control` + 1h TTL）
    - openai_compat 且地址里有 openrouter：把断点透传给后端（`adapters.py` 里的 `_supports_cache`）
    - 其余 openai_compat（DeepSeek、智谱等）：不打断点，靠厂商自己的前缀缓存

    ⚠️ 这条嗅探**只在种子里最后用一次** —— 之后 adapter 读档案字段，不再嗅探
    （`tests/test_settings_seed.py` 用真 adapter 的 `_supports_cache` 对账）。
    """
    if protocol == "anthropic":
        return "explicit_breakpoint"
    if "openrouter" in (base_url or "").lower():
        return "passthrough"
    return "auto_prefix"


def resolve_key(names: list[str], env: Mapping[str, str]) -> str:
    """按顺序找第一个非空的环境变量。和 `config.Backend.api_key` 同一个口径（含 strip）。"""
    for n in names:
        v = (env.get(n) or "").strip()
        if v:
            return v
    return ""


@dataclass
class SeedReport:
    providers: int
    models: int
    slots: int
    digest: str
    changed: bool        # 与上一次启动的种子内容不同（代码或环境变量变了）；第一次灌也算 True


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def seed(store: SettingsStore, cfg: Any, env: Mapping[str, str] | None = None) -> SeedReport:
    """把 `cfg`（真正的 `config.Config`）灌进库。全程一个事务，要么全成要么回滚。"""
    from config import BACKENDS

    env = os.environ if env is None else env
    now = _now()
    price_table = getattr(cfg, "PRICING_CNY", None) or {}

    # ---- 先在内存里把要写的全部算出来（便于算摘要，也让事务里只剩写）----
    providers: dict[str, dict[str, Any]] = {}
    for name, b in BACKENDS.items():
        providers[name] = {
            "label": PROVIDER_LABELS.get(name, name),
            "protocol": b.provider, "base_url": b.base_url,
            "cache_style": seed_cache_style(b.provider, b.base_url),
            "key_envs": list(b.key_envs), "note": "",
        }

    models: list[dict[str, Any]] = []
    index: dict[tuple[str, str], int] = {}          # (provider_id, model_name) → models 下标

    def add_model(provider_id: str, model_name: str, short: str | None, label: str) -> int:
        k = (provider_id, model_name)
        if k in index:
            # 同一个型号被多个短名引用（理论上可能）：短名只挂在第一次出现的那行，其余另起一行会撞
            # UNIQUE(provider_id, model_name) —— 所以这里直接复用，并记一笔
            if short and models[index[k]]["short_name"] is None:
                models[index[k]]["short_name"] = short
            return index[k]
        caps = ["chat"]
        from agent.adapters import supports_vision
        if supports_vision(model_name):
            caps.append("vision")
        models.append({
            "provider_id": provider_id, "model_name": model_name, "short_name": short,
            "label": label or model_name, "capabilities": caps,
            "price": price_table.get(model_name),
        })
        index[k] = len(models) - 1
        return index[k]

    for short, c in (getattr(cfg, "models", None) or {}).items():
        if c.backend in providers:
            add_model(c.backend, c.model, short, c.label)

    slots: dict[str, dict[str, Any]] = {}
    for slot_name, attr in SLOT_ATTRS:
        llm = getattr(cfg, attr, None)
        if llm is None:
            continue
        url = (llm.base_url or "").rstrip("/")
        cands = [n for n, p in providers.items() if p["base_url"].rstrip("/") == url]
        pid = next((n for n in cands if providers[n]["protocol"] == llm.provider),
                   cands[0] if cands else None)
        if pid is None:
            pid = f"env-{attr}"
            providers[pid] = {
                "label": f"环境变量指定（{attr}）", "protocol": llm.provider, "base_url": llm.base_url,
                "cache_style": seed_cache_style(llm.provider, llm.base_url),
                "key_envs": [], "note": "地址由 NOX_*_BASE_URL 直接指定，不在 BACKENDS 里",
            }
        params: dict[str, Any] = {"max_tokens": llm.max_tokens}
        if llm.provider != providers[pid]["protocol"]:
            params["provider"] = llm.provider                       # NOX_*_PROVIDER 覆盖
        base_key = resolve_key(providers[pid]["key_envs"], env)
        if (llm.api_key or "") != base_key:
            # key 不是从该厂商默认的环境变量来的：反查是哪个环境变量（只存**名字**）
            names = [n for p in providers.values() for n in p["key_envs"]] + list(_KEY_OVERRIDE_ENVS)
            found = [n for n in dict.fromkeys(names) if (env.get(n) or "").strip() == llm.api_key]
            params["key_envs"] = found[:1]
        mi = add_model(pid, llm.model, None, llm.model)
        slots[slot_name] = {"model_index": mi, "params": params}

    digest = hashlib.sha256(_canon(
        {"providers": providers, "models": models, "slots": slots}).encode("utf-8")).hexdigest()[:16]

    # ---- 一个事务：删掉旧的种子行，重灌 ----
    with store.transaction() as c:
        c.execute("DELETE FROM slots WHERE updated_by='seed'")
        c.execute("DELETE FROM models WHERE origin='seed'")
        c.execute("DELETE FROM providers WHERE source='seed'"
                  " AND id NOT IN (SELECT provider_id FROM models)")
        for pid, p in providers.items():
            # 不用 `ON CONFLICT … DO UPDATE`（要 SQLite ≥ 3.24，线上的版本没核实过）：
            # 先 INSERT OR IGNORE 再 UPDATE，任何 SQLite 都认。UPDATE 只动 source='seed' 的行 ——
            # 被用户型号还引用着、因而没被删掉的种子厂商才会走到这里。
            c.execute(
                "INSERT OR IGNORE INTO providers(id,label,protocol,base_url,cache_style,auth,key_envs,"
                "source,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pid, p["label"], p["protocol"], p["base_url"], p["cache_style"], "api_key",
                 json.dumps(p["key_envs"]), "seed", p["note"], now, now))
            c.execute(
                "UPDATE providers SET label=?, protocol=?, base_url=?, cache_style=?, key_envs=?,"
                " note=?, updated_at=? WHERE id=? AND source='seed'",
                (p["label"], p["protocol"], p["base_url"], p["cache_style"],
                 json.dumps(p["key_envs"]), p["note"], now, pid))
        ids: list[int] = []
        for m in models:
            cur = c.execute(
                "INSERT INTO models(provider_id,model_name,short_name,label,capabilities,price_json,"
                "origin,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (m["provider_id"], m["model_name"], m["short_name"], m["label"],
                 json.dumps(m["capabilities"]),
                 json.dumps(m["price"]) if m["price"] else None, "seed", 1, now, now))
            ids.append(cur.lastrowid)
        for slot_name, s in slots.items():
            c.execute(
                "INSERT INTO slots(slot,targets,updated_at,updated_by) VALUES(?,?,?,?)",
                (slot_name, json.dumps([{"model_id": ids[s["model_index"]], "params": s["params"]}]),
                 now, "seed"))
        prev = c.execute("SELECT v FROM meta WHERE k='seed_digest'").fetchone()
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('seed_digest',?)", (digest,))
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('seeded_at',?)", (now,))
    return SeedReport(providers=len(providers), models=len(models), slots=len(slots),
                      digest=digest, changed=(prev is None or prev["v"] != digest))
