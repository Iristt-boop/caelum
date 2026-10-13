"""槽位可换（配置层 P3，2026-10-13）：Utility / Vision / Video / Image 在 OS 的 Slots 里选，下一句就生效，能换回去。

Primary（聊天）不在这里 —— 它早就是服务器上一份（`Nox.chat_model_key`，`/api/nox/model`），Slots 里直接用那条。

## 她的选择存哪

单独一张 `slot_choices`，**不碰 `slots` 表**：`slots` 每次启动由种子刷新（删 seed 行再灌），
影子对账也拿它和环境变量逐字段比 —— 她的选择要是写进去，下次启动就和种子撞主键、对账也报不一致、档案被停用。
没选 = 用服务器默认（环境变量那份，和以前一样）。每次改都往 `slot_history` 记上一个，「换回去」一步一步往回退。

## 怎么热生效（不重启）

  · Utility：Router、Archiver、各路杂活（话题池、日卡、电话、Moments、关系、时间、评估、压缩…）拿的**都是同一个**
    `SlotAdapter` 代理 —— 换的时候只换代理里面那个 adapter，所有人同时换，不会「一半换了一半没换」。
    正在跑的那一次调用不受影响（它已经拿着里面那个了）。
  · Vision：看图的四处（聊天里替主线看、相册描述、电脑截屏、Stack-chan 拍照）都改成**每次调用现取**（`Nox.vision_cfg`）。
  · Video / Image：能选、能存，现在还没有功能读它们 —— 等用上的那天直接读 `resolve_llm`。
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from settings_store.probe import llm_for
from settings_store.store import SettingsStore, _now

logger = logging.getLogger("settings_store.slots")

#: 能在前端换的槽位 → 型号必须有的能力
EDITABLE = {"chat.utility": "chat", "vision": "vision", "video": "video", "image": "image"}
#: 看图走 `agent/vision.describe`（OpenAI 兼容的 SDK 调用），原生 Anthropic 协议的接不上
_PROTOCOL_ONLY = {"vision": "openai_compat"}


class SlotError(ValueError):
    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.status = status


class SlotAdapter:
    """可热换的 adapter 代理。所有拿杂活 adapter 的地方拿的都是它；`swap` 只换里面那个。

    除了 `swap` / `current` / `slot`，其余属性（complete、stream、cfg…）原样转给里面那个。
    """

    def __init__(self, slot: str, inner: Any) -> None:
        self.__dict__["_slot"] = slot
        self.__dict__["_current"] = inner

    @property
    def slot(self) -> str:
        return self.__dict__["_slot"]

    def current(self) -> Any:
        return self.__dict__["_current"]

    def swap(self, inner: Any) -> None:
        self.__dict__["_current"] = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_current"], name)

    def __setattr__(self, name: str, value: Any) -> None:
        # 往代理身上赋值（比如老代码 `a.cfg = …`）要落到里面那个上，不然代理身上多一份影子、里面没变
        setattr(self.__dict__["_current"], name, value)


# ------------------------------------------------------------------ 读


def _check_slot(slot: str) -> str:
    if slot not in EDITABLE:
        raise SlotError(f"这个位置不能在这里换：{slot}", status=404)
    return slot


def choice(store: SettingsStore, slot: str) -> dict[str, Any] | None:
    """她在这个位置选的型号（连同厂商）。没选 / 选的型号已经删了 → None（= 服务器默认）。"""
    with store._lock:
        r = store._conn.execute("SELECT provider_id, model_name, at FROM slot_choices WHERE slot=?",
                                (slot,)).fetchone()
    if r is None:
        return None
    m = _find(store, r["provider_id"], r["model_name"])
    if m is None or not m["enabled"]:
        logger.warning("她给 %s 选的 %s/%s 不在了 / 停用了，这个位置先用服务器默认", slot, r["provider_id"], r["model_name"])
        return None
    p = store.provider(m["provider_id"])
    if p is None:
        return None
    return {"model": m, "provider": p, "at": r["at"]}


def resolve_llm(store: SettingsStore | None, keys: Any, slot: str, default: Any) -> Any:
    """这个位置**现在**该用哪条线路（`LLMConfig`）。她选过就按她选的建（长度 / 超时 / 重试沿用默认那份），没选回默认。"""
    if store is None:
        return default
    try:
        c = choice(store, slot)
    except Exception:  # noqa: BLE001 —— 读不出她的选择也得能干活，但要留痕
        logger.warning("读不出 %s 的选择，用服务器默认", slot, exc_info=True)
        return default
    if c is None:
        return default
    llm = llm_for(store, keys, c["provider"]["id"], c["model"]["model_name"])
    if default is not None:
        llm = dataclasses.replace(llm, max_tokens=default.max_tokens, timeout=default.timeout,
                                  max_retries=default.max_retries)
    return llm


def options(store: SettingsStore, keys: Any, slot: str) -> list[dict[str, Any]]:
    """这个位置能选哪些：能力对得上、开着、这一家配了 key（看图还要求 OpenAI 兼容协议）。"""
    from settings_store.views import _key_source

    import os

    need = EDITABLE[_check_slot(slot)]
    front = keys.configured() if keys is not None else set()
    provs = {p["id"]: p for p in store.providers()}
    out = []
    for m in store.models():
        p = provs.get(m["provider_id"])
        if p is None or not m["enabled"] or need not in (m.get("capabilities") or []):
            continue
        if _PROTOCOL_ONLY.get(slot) and p["protocol"] != _PROTOCOL_ONLY[slot]:
            continue
        if _key_source(p["id"], list(p["key_envs"]), os.environ, front) is None:
            continue
        out.append({"model_id": m["id"], "model_name": m["model_name"], "label": m["label"] or m["model_name"],
                    "provider_id": p["id"], "provider_label": p["label"]})
    return out


def history(store: SettingsStore, slot: str) -> list[dict[str, Any]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT id, snapshot_json, at, reason FROM slot_history WHERE slot=? ORDER BY id DESC LIMIT 20",
            (slot,)).fetchall()
    return [{"id": r["id"], **json.loads(r["snapshot_json"]), "at": r["at"], "reason": r["reason"]} for r in rows]


def editable_view(store: SettingsStore, keys: Any, cfg: Any) -> list[dict[str, Any]]:
    """Slots 里四个能换的位置：现在是谁（她选的 / 服务器默认）、能选哪些、能不能换回去。**不含 key。**"""
    defaults = {"chat.utility": getattr(cfg, "utility", None), "vision": getattr(cfg, "vision", None)}
    out = []
    for slot in EDITABLE:
        c = choice(store, slot)
        d = defaults.get(slot)
        out.append({
            "slot": slot,
            "choice": ({"model_id": c["model"]["id"], "model_name": c["model"]["model_name"],
                        "label": c["model"]["label"] or c["model"]["model_name"],
                        "provider_label": c["provider"]["label"]} if c else None),
            "default": ({"model_name": d.model} if d is not None and getattr(d, "model", "") else None),
            "options": options(store, keys, slot),
            "can_revert": bool(history(store, slot)),
        })
    return out


# ------------------------------------------------------------------ 写


def _find(store: SettingsStore, provider_id: str, model_name: str) -> dict[str, Any] | None:
    return next((m for m in store.models() if m["provider_id"] == provider_id and m["model_name"] == model_name), None)


def _current(store: SettingsStore, slot: str) -> tuple[str, str] | None:
    """(厂商, 型号名)；没选 = None。"""
    with store._lock:
        r = store._conn.execute("SELECT provider_id, model_name FROM slot_choices WHERE slot=?", (slot,)).fetchone()
    return (r["provider_id"], r["model_name"]) if r else None


def _write(store: SettingsStore, slot: str, target: tuple[str, str] | None, reason: str, record: bool = True) -> None:
    prev = _current(store, slot)
    with store.transaction() as c:
        if record:
            c.execute("INSERT INTO slot_history(slot, snapshot_json, at, reason) VALUES(?,?,?,?)",
                      (slot, json.dumps({"target": list(prev) if prev else None}), _now(), reason))
        if target is None:
            c.execute("DELETE FROM slot_choices WHERE slot=?", (slot,))
        else:
            c.execute("INSERT OR REPLACE INTO slot_choices(slot, provider_id, model_name, at, by) VALUES(?,?,?,?,'she')",
                      (slot, target[0], target[1], _now()))


def set_choice(store: SettingsStore, keys: Any, slot: str, model_id: int | None) -> None:
    """她选了一个（`None` = 回到服务器默认）。只许选 `options` 里的 —— 没 key / 能力不对的选了也用不上。"""
    _check_slot(slot)
    target = None
    if model_id is not None:
        opt = next((o for o in options(store, keys, slot) if o["model_id"] == model_id), None)
        if opt is None:
            raise SlotError("这个型号放不进这个位置（能力不对、停用了，或者那一家没配 key）")
        target = (opt["provider_id"], opt["model_name"])
    if target == _current(store, slot):
        return
    _write(store, slot, target, reason="她选的")
    logger.info("配置审计：她把 %s 换成了 %s", slot, f"型号 {model_id}" if model_id is not None else "服务器默认")


def revert(store: SettingsStore, slot: str) -> str | None:
    """回到上一个。返回回到的「厂商/型号名」（None = 服务器默认）。没有上一个就报。"""
    _check_slot(slot)
    with store._lock:
        r = store._conn.execute("SELECT id, snapshot_json FROM slot_history WHERE slot=? ORDER BY id DESC LIMIT 1",
                                (slot,)).fetchone()
    if r is None:
        raise SlotError("没有上一个可以回了")
    snap = json.loads(r["snapshot_json"]).get("target")
    target = tuple(snap) if snap else None
    _write(store, slot, target, reason="回到上一个", record=False)
    with store.transaction() as c:
        c.execute("DELETE FROM slot_history WHERE id=?", (r["id"],))
    label = "/".join(target) if target else None
    logger.info("配置审计：她把 %s 换回了上一个（%s）", slot, label or "服务器默认")
    return label


# ------------------------------------------------------------------ 生效


def apply(core: Any) -> None:
    """让她的选择生效：Utility 换代理里面那个 adapter；Vision 不用做什么（每次现取）。启动时和每次改完都调。"""
    from agent.adapters import make_adapter

    store = getattr(core, "profiles", None)
    router = getattr(core, "router", None)
    proxy = getattr(router, "light_adapter", None)
    if store is None or not isinstance(proxy, SlotAdapter):
        return
    default_cfg = getattr(getattr(core, "cfg", None), "utility", None)
    llm = resolve_llm(store, getattr(core, "config_keys", None), "chat.utility", default_cfg)
    cur = proxy.current()
    if getattr(cur, "cfg", None) is not None and _same_line(cur, llm):
        return
    try:
        proxy.swap(make_adapter(llm))
    except Exception:  # noqa: BLE001 —— 建不出来就别换（留着原来那个，杂活照常），但要留痕
        logger.warning("杂活换成 %s 失败，留着原来那个", getattr(llm, "model", "?"), exc_info=True)
        return
    logger.info("杂活现在是 %s（%s）", llm.model, llm.base_url or "默认地址")


def _same_line(adapter: Any, llm: Any) -> bool:
    from agent.adapters import profiled

    a, b = adapter.cfg, profiled(llm)
    return (a.model, a.base_url, a.provider, a.api_key) == (b.model, b.base_url, b.provider, b.api_key)
