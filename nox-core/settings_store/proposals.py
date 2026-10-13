"""他提议、她点头（配置层 P5，2026-10-13；设计稿第十一节、糖糖 10-05 拍板「她改，他提议」）。

他的工具 `propose_config_change` **只写一条提议** + 往聊天里发一张卡；什么配置都不改。
她在卡上点「同意」，由确认端点（`POST /api/nox/config/proposals/{id}/confirm`，**不经过他的工具循环**）执行。

三种提议：
  slot      把某个用途（Primary / Utility / Vision / Video / Image）换成某个型号
  add_model 在某一家名下加一个型号（她同意时才真测，通过才保存）
  need_key  请她给某一家填 key —— **值只在她点开的卡里输入**，不进对话、不进提议、不进日志

🔴 `apply` 只许被确认端点调用（tests/test_config_proposals.py 的哨兵扫源码）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from settings_store import slots
from settings_store.store import SettingsStore, _now

logger = logging.getLogger("settings_store.proposals")

KINDS = ("slot", "add_model", "need_key")
SLOT_NAMES = {"chat.primary": "聊天（Primary）", "chat.utility": "杂活（Utility）", "vision": "看图（Vision）",
              "video": "看视频（Video）", "image": "画图（Image）"}
#: 他随口说的位置名 → 正式名
SLOT_ALIASES = {"primary": "chat.primary", "chat": "chat.primary", "utility": "chat.utility", "vision": "vision",
                "video": "video", "image": "image", "chat.primary": "chat.primary", "chat.utility": "chat.utility"}

PROPOSED, APPLYING, APPLIED, REJECTED, FAILED = "proposed", "applying", "applied", "rejected", "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals(
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL,          -- JSON；need_key 里**没有** key
  summary     TEXT NOT NULL,          -- 卡上那句话
  reason      TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL,
  result      TEXT NOT NULL DEFAULT '',
  session_id  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  decided_at  TEXT
);
"""


class ProposalError(ValueError):
    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.status = status


def _ensure(store: SettingsStore) -> None:
    with store._lock:
        store._conn.executescript(SCHEMA)


# ------------------------------------------------------------------ 校验（他提的时候就把不成立的挡回去）


def _model_label(store: SettingsStore, provider_id: str, model_name: str) -> str:
    m = next((x for x in store.models() if x["provider_id"] == provider_id and x["model_name"] == model_name), None)
    return (m["label"] if m and m["label"] else model_name)


def _now_text(core: Any, slot: str) -> str:
    """这个位置现在是谁（卡上「从 X 换成 Y」的 X）。"""
    if slot == "chat.primary":
        key = core.chat_model_key() if hasattr(core, "chat_model_key") else None
        choice = (getattr(core.cfg, "models", {}) or {}).get(key or "")
        if choice:
            return choice.label or choice.model
        mine = core.user_model(key) if key and hasattr(core, "user_model") else None
        return mine["label"] if mine else core.cfg.primary.model
    c = slots.choice(core.settings, slot)
    if c:
        return c["model"]["label"] or c["model"]["model_name"]
    default = {"chat.utility": getattr(core.cfg, "utility", None), "vision": getattr(core.cfg, "vision", None)}.get(slot)
    return default.model if default is not None else "还未配置"


def _primary_key(core: Any, provider_id: str, model_name: str) -> str | None:
    """Primary 的选择存的是短名：代码清单里的，或她加的 u- 型号。"""
    for key, c in (getattr(core.cfg, "models", {}) or {}).items():
        if c.backend == provider_id and c.model == model_name:
            return key
    store = getattr(core, "settings", None)
    if store is not None:
        for m in store.models():
            if (m["provider_id"], m["model_name"]) == (provider_id, model_name) and (m["short_name"] or "").startswith("u-") \
                    and m["enabled"]:
                return m["short_name"]
    return None


def validate(core: Any, kind: str, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """把他给的参数整理成提议，并写好卡上那句话。不成立就抛 ProposalError（话是给他看的：告诉他能选什么）。"""
    store: SettingsStore = core.settings
    if kind not in KINDS:
        raise ProposalError(f"kind 只能是 {' / '.join(KINDS)}")
    pid = str(args.get("provider_id") or "").strip()
    name = str(args.get("model_name") or "").strip()

    if kind == "need_key":
        p = store.provider(pid)
        if p is None:
            raise ProposalError(f"没有 {pid or '（空）'} 这一家。有：{'、'.join(x['id'] for x in store.providers())}")
        return {"provider_id": pid}, f"给「{p['label']}」填一把 key"

    if kind == "add_model":
        p = store.provider(pid)
        if p is None:
            raise ProposalError(f"没有 {pid or '（空）'} 这一家。有：{'、'.join(x['id'] for x in store.providers())}")
        if not name:
            raise ProposalError("model_name 要写型号的原名（比如 google/gemini-3-flash）")
        if any(m["provider_id"] == pid and m["model_name"] == name for m in store.models()):
            raise ProposalError(f"{p['label']} 名下已经有 {name} 了，用 kind=slot 提议换过去就行")
        return ({"provider_id": pid, "model_name": name},
                f"在「{p['label']}」名下加型号 {name}（她同意时先真测一次，通过才保存）")

    # slot
    slot = SLOT_ALIASES.get(str(args.get("slot") or "").strip().lower())
    if slot is None:
        raise ProposalError("slot 只能是 primary / utility / vision / video / image")
    if slot == "chat.primary":
        key = _primary_key(core, pid, name)
        if key is None:
            have = [f"{c.backend}/{c.model}" for c in (getattr(core.cfg, "models", {}) or {}).values()]
            raise ProposalError(f"聊天能选的里没有 {pid}/{name}。能选：{'、'.join(have[:12])}")
        payload = {"slot": slot, "provider_id": pid, "model_name": name, "key": key}
    else:
        opts = slots.options(store, getattr(core, "config_keys", None), slot)
        hit = next((o for o in opts if o["provider_id"] == pid and o["model_name"] == name), None)
        if hit is None:
            have = [f"{o['provider_id']}/{o['model_name']}" for o in opts]
            raise ProposalError(f"{SLOT_NAMES[slot]}能选的里没有 {pid}/{name}。"
                                + (f"能选：{'、'.join(have[:12])}" if have else "现在一个能选的都没有（没有对应能力的型号）"))
        payload = {"slot": slot, "provider_id": pid, "model_name": name}
    summary = f"把{SLOT_NAMES[slot]}从 {_now_text(core, slot)} 换成 {_model_label(store, pid, name)}"
    return payload, summary


# ------------------------------------------------------------------ 存取


def create(store: SettingsStore, kind: str, payload: dict[str, Any], summary: str, reason: str,
           session_id: str) -> str:
    _ensure(store)
    pid = uuid.uuid4().hex[:12]
    with store.transaction() as c:
        c.execute("INSERT INTO proposals(id, kind, payload, summary, reason, status, session_id, created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (pid, kind, json.dumps(payload, ensure_ascii=False), summary, reason[:300], PROPOSED, session_id, _now()))
    logger.info("他提议改配置 %s：%s", pid, summary)
    return pid


def get(store: SettingsStore, pid: str) -> dict[str, Any] | None:
    _ensure(store)
    with store._lock:
        r = store._conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


def _claim(store: SettingsStore, pid: str, to: str) -> dict[str, Any]:
    """proposed → to。不是 proposed（已经同意过 / 拒过）就报 409 —— 点两下不会执行两次。"""
    _ensure(store)
    with store.transaction() as c:
        n = c.execute("UPDATE proposals SET status=?, decided_at=? WHERE id=? AND status=?",
                      (to, _now(), pid, PROPOSED)).rowcount
    p = get(store, pid)
    if p is None:
        raise ProposalError("没有这条提议", status=404)
    if not n:
        raise ProposalError(f"这条提议已经处理过了（{p['status']}）", status=409)
    return p


def _finish(store: SettingsStore, pid: str, status: str, result: str) -> None:
    with store.transaction() as c:
        c.execute("UPDATE proposals SET status=?, result=? WHERE id=?", (status, result[:500], pid))


def reject(store: SettingsStore, pid: str) -> dict[str, Any]:
    _claim(store, pid, REJECTED)
    logger.info("配置审计：她没同意提议 %s", pid)
    return get(store, pid)


def apply(core: Any, pid: str, key: str | None = None) -> dict[str, Any]:
    """**只许确认端点调**。她点了「同意」：按提议执行，结果写回提议。"""
    from settings_store import profile

    store: SettingsStore = core.settings
    p = get(store, pid)
    if p is None:
        raise ProposalError("没有这条提议", status=404)
    if p["kind"] == "need_key" and not (key or "").strip():
        raise ProposalError("要在卡上把 key 填进去")          # 先校验，不占掉这条提议
    p = _claim(store, pid, APPLYING)
    pl = p["payload"]
    try:
        if p["kind"] == "slot":
            if pl["slot"] == "chat.primary":
                if not core.set_chat_model(pl["key"]):
                    raise ProposalError("聊天模型没存上（状态库没起来？）", status=503)
            else:
                mid = next(m["id"] for m in store.models()
                           if m["provider_id"] == pl["provider_id"] and m["model_name"] == pl["model_name"])
                slots.set_choice(store, getattr(core, "config_keys", None), pl["slot"], mid)
                slots.apply(core)
            result = "换好了，下一次就用它。"
        elif p["kind"] == "add_model":
            from settings_store.edit import Editor

            Editor(store, core.config_keys).add_models(pl["provider_id"], [pl["model_name"]])
            profile.refresh(core)
            result = "加上了（真测通过）。"
        else:
            core.config_keys.set(pl["provider_id"], key or "")
            store.forget_tests(pl["provider_id"])
            profile.refresh(core)
            result = "key 填好了，下一句就用新的。"
    except Exception as exc:  # noqa: BLE001 —— 失败要写回提议、如实给她看（不含 key：异常文字过一遍抹 key）
        from settings_store.probe import scrub

        msg = scrub(str(exc), [key or ""])
        _finish(store, pid, FAILED, msg)
        logger.warning("配置审计：提议 %s 执行失败：%s", pid, msg)
        raise ProposalError(msg, status=getattr(exc, "status", 400)) from None
    _finish(store, pid, APPLIED, result)
    logger.info("配置审计：她同意了提议 %s（%s）", pid, p["summary"])
    return get(store, pid)


def public(p: dict[str, Any]) -> dict[str, Any]:
    """给前端看的（卡片轮询）。payload 里本来就没有 key。"""
    return {k: p[k] for k in ("id", "kind", "summary", "reason", "status", "result", "created_at", "decided_at")}
