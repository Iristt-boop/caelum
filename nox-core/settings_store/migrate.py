"""加密迁移包 `.caelumcfg`（配置层 P2b，设计稿 12.2，糖糖 2026-10-05 拍板）。

key 不进普通备份，那换机 / 恢复 / 搬家时 key 怎么过去？—— **只走她主动点的加密迁移包。**

## 格式

    MAGIC(10) | 头长度(2, 大端) | 头(JSON) | 密文 + GCM tag

  · 头：`{"v":1,"kdf":"scrypt","n":…,"r":…,"p":…,"salt":b64,"nonce":b64}` —— KDF 参数写进文件，以后升级参数旧包照样能开
  · 密钥：`hashlib.scrypt(口令, salt)` → 32 字节 → AES-256-GCM
  · **MAGIC + 头整个当 AAD**：改头里任何一个字节（比如把 n 调小想省力破解）tag 就对不上
  · 明文 JSON：providers / models / slots / keys / exported_at / source_host / choices
  · `choices`（10-29 加）：她自己的选择 —— 槽位（slot_choices）、声音链、聊天主模型、思考开关。
    旧包没有这一栏，照样能导（只是不带选择）；新包给旧代码，多出来的这栏被忽略

## 不变量（每条都有测试，交付前故意改坏过）

  · 口令不对 / 文件被改：**同一句话**，对外不区分（不给猜的人提示）
  · 包里扫不到任何明文 key；预览里扫不到任何 key（只有「有没有带 key」的布尔）
  · 导入只写**她自己加的**厂商 / 型号（`source='user'` / `origin!='seed'`）和 key；代码自带的种子行由代码管，不覆盖
  · `slots`（种子槽位表）仍然不导：它每次启动由代码重灌。她的槽位选择在 `choices.slots`（slot_choices 表，不和种子撞），
    10-29 起导入：型号按「厂商 + 型号名」对上、这边能用才收，对不上的一条条列在预览里，不猜
  · 这个模块**不许被工具表引用**（他只能提议，不能导出 / 导入；`tests/test_config_keys_migrate.py` 的哨兵）
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
from datetime import datetime, timezone
from typing import Any

import logging

from settings_store.keys import KeyStore
from settings_store.store import SettingsStore, _now

_log = logging.getLogger("settings_store.migrate")

MAGIC = b"CAELUMCFG\x00"
VERSION = 1
MIN_PASSPHRASE = 8
#: scrypt：n=2^15, r=8 → 约 32MB 内存、N150 上约 0.1 秒。参数随文件走，以后可以调大
KDF = {"kdf": "scrypt", "n": 2 ** 15, "r": 8, "p": 1}
_MAXMEM = 128 * 1024 * 1024

#: 对外的报错。口令错和文件被改**不区分**
BAD_PACKAGE = "口令不对，或者文件被改过"
NOT_A_PACKAGE = "这不是 Caelum 的迁移包"


class MigrationError(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _derive(passphrase: str, salt: bytes, kdf: dict[str, Any]) -> bytes:
    if kdf.get("kdf") != "scrypt":
        raise MigrationError(BAD_PACKAGE)              # 版本号对得上、算法却不认识 = 头被改过
    n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    # 头被改成离谱的参数：别真去算（会卡死 / 吃光内存），按「被改过」处理
    if not (2 ** 14 <= n <= 2 ** 20 and 1 <= r <= 16 and 1 <= p <= 4):
        raise MigrationError(BAD_PACKAGE)
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=_MAXMEM, dklen=32)


def check_passphrase(p1: str, p2: str) -> str:
    if (p1 or "") != (p2 or ""):
        raise MigrationError("两次口令不一样")
    if len(p1 or "") < MIN_PASSPHRASE:
        raise MigrationError(f"口令至少 {MIN_PASSPHRASE} 个字")
    return p1


# ------------------------------------------------------------------ 导出


#: 预览里给她看的名字
SLOT_LABELS = {"chat.utility": "杂活", "vision": "看图", "video": "视频", "image": "画图"}


def _model_by_short(store: SettingsStore, short: str | None) -> dict[str, Any] | None:
    if not short:
        return None
    return next((m for m in store.models() if m["short_name"] == short), None)


def collect_choices(store: SettingsStore, prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    """她自己的选择（R-4 补，10-29）。`prefs` 由 server 递进来：{"chat_model": 短名 | None, "thinking": bool | None}
    —— 那两样存在 attention.db，这个模块不碰别的库。"""
    from settings_store import voice

    with store._lock:
        rows = store._conn.execute("SELECT slot, provider_id, model_name FROM slot_choices ORDER BY slot").fetchall()
    saved = {n: voice._saved(store, n) for n in voice.DEFAULTS}
    out: dict[str, Any] = {
        "slots": [{"slot": r["slot"], "provider_id": r["provider_id"], "model_name": r["model_name"]} for r in rows],
        "voice": {n: e for n, e in saved.items() if e is not None},
        "chat_model": None,
        "thinking": None,
    }
    prefs = prefs or {}
    key = prefs.get("chat_model")
    if key:
        m = _model_by_short(store, key)
        # 型号名跟着走：短名在新机器上可能让给别人了（导入时短名被占会清空）
        out["chat_model"] = {"key": key, "provider_id": m["provider_id"] if m else None,
                             "model_name": m["model_name"] if m else None}
    if prefs.get("thinking") is not None:
        out["thinking"] = bool(prefs["thinking"])
    return out


def collect(store: SettingsStore, keys: KeyStore, prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    slots = []
    for s in store.slots():
        resolved = []
        for t in s["targets"]:
            m = store.model(t.get("model_id"))
            if m:
                resolved.append({"provider_id": m["provider_id"], "model_name": m["model_name"],
                                 "params": t.get("params") or {}})
        slots.append({"slot": s["slot"], "targets": resolved, "updated_by": s["updated_by"]})
    return {
        "format": "caelumcfg", "v": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_host": socket.gethostname(),
        "providers": [{k: p[k] for k in ("id", "label", "protocol", "base_url", "cache_style", "auth",
                                         "key_envs", "source", "note")} for p in store.providers()],
        "models": [{k: m.get(k) for k in ("provider_id", "model_name", "short_name", "label", "capabilities",
                                          "price", "cache_style", "cache_ttl", "origin", "enabled")}
                   for m in store.models()],
        "slots": slots,
        "keys": keys.export_all(),
        "choices": collect_choices(store, prefs),
    }


def seal(payload: dict[str, Any], passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    header = json.dumps({"v": VERSION, **KDF, "salt": _b64(salt), "nonce": _b64(nonce)},
                        separators=(",", ":"), sort_keys=True).encode("utf-8")
    key = _derive(passphrase, salt, KDF)
    plain = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, plain, MAGIC + header)
    return MAGIC + struct.pack(">H", len(header)) + header + ct


def export_package(store: SettingsStore, keys: KeyStore, passphrase: str,
                   prefs: dict[str, Any] | None = None) -> bytes:
    return seal(collect(store, keys, prefs), passphrase)


# ------------------------------------------------------------------ 打开 / 预览 / 导入


def open_package(blob: bytes, passphrase: str) -> dict[str, Any]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 2:
        raise MigrationError(NOT_A_PACKAGE)
    (hlen,) = struct.unpack(">H", blob[len(MAGIC):len(MAGIC) + 2])
    hstart = len(MAGIC) + 2
    header = blob[hstart:hstart + hlen]
    ct = blob[hstart + hlen:]
    try:
        h = json.loads(header.decode("utf-8"))
        salt, nonce = base64.b64decode(h["salt"]), base64.b64decode(h["nonce"])
        if h.get("v") != VERSION:
            raise MigrationError("这个迁移包的版本太新，先升级 Caelum")
    except MigrationError:
        raise
    except Exception:  # noqa: BLE001 —— 头坏了 = 被改过
        raise MigrationError(BAD_PACKAGE) from None
    key = _derive(passphrase or "", salt, h)
    try:
        plain = AESGCM(key).decrypt(nonce, ct, MAGIC + header)
    except (InvalidTag, ValueError):
        raise MigrationError(BAD_PACKAGE) from None
    data = json.loads(plain.decode("utf-8"))
    if data.get("format") != "caelumcfg":
        raise MigrationError(NOT_A_PACKAGE)
    return data


def _is_user_provider(p: dict[str, Any]) -> bool:
    return p.get("source") == "user"


def _is_user_model(m: dict[str, Any]) -> bool:
    return m.get("origin") not in (None, "seed")


def preview(store: SettingsStore, data: dict[str, Any]) -> dict[str, Any]:
    """导入前给她看的。**不含任何 key**：每家只说「包里有没有带 key」。"""
    here = {p["id"]: p for p in store.providers()}
    keys = data.get("keys") or {}
    providers = []
    for p in data.get("providers") or []:
        pid = p["id"]
        if _is_user_provider(p):
            action = "覆盖" if pid in here else "新增"
        elif pid in here:
            action = "代码自带，只导 key" if pid in keys else "代码自带，不动"
        else:
            action = "跳过（这边的代码里没有这一家）"
        providers.append({"id": pid, "label": p.get("label") or pid, "action": action,
                          "has_key": pid in keys, "status": "未测"})
    user_models = [m for m in data.get("models") or [] if _is_user_model(m)]
    return {
        "exported_at": data.get("exported_at"), "source_host": data.get("source_host"),
        "providers": providers,
        "models_added": len(user_models),
        "keys": len([pid for pid in keys if pid in here or any(
            p["id"] == pid and _is_user_provider(p) for p in data.get("providers") or [])]),
        "slots": {"in_package": len(data.get("slots") or []), "importable": False,
                  "note": "" if "choices" in data else "这个包是旧版导出的，没带你的选择（槽位 / 声音 / 聊天模型 / 思考）"},
        "choices": [{"what": c["what"], "value": c["value"], "action": c["action"], "ok": c["ok"]}
                    for c in plan_choices(store, data)],
    }


def _model_after_import(store: SettingsStore, data: dict[str, Any], provider_id: str | None,
                        model_name: str | None) -> bool:
    """这个型号导完以后这边有没有：这边本来就有，或者包里带着、而且会被导进来（她加的、厂商这边有 / 跟着导）。"""
    if not provider_id or not model_name:
        return False
    if any(m["provider_id"] == provider_id and m["model_name"] == model_name and m["enabled"]
           for m in store.models()):
        return True
    here = {p["id"] for p in store.providers()}
    incoming_p = {p["id"] for p in data.get("providers") or [] if _is_user_provider(p)}
    return any(m["provider_id"] == provider_id and m["model_name"] == model_name and _is_user_model(m)
               and (provider_id in here or provider_id in incoming_p) for m in data.get("models") or [])


def plan_choices(store: SettingsStore, data: dict[str, Any]) -> list[dict[str, Any]]:
    """包里她的选择，逐条说会不会收。**预览和导入用同一份判断**，免得预览说收、导入没收。"""
    from settings_store import voice

    ch = data.get("choices") or {}
    out: list[dict[str, Any]] = []
    for s in ch.get("slots") or []:
        slot, pid, name = s.get("slot"), s.get("provider_id"), s.get("model_name")
        what = SLOT_LABELS.get(slot)
        if what is None:
            out.append({"kind": "slot", "key": slot, "what": str(slot), "value": f"{pid} / {name}",
                        "ok": False, "action": "跳过（这边没有这个位置）"})
            continue
        ok = _model_after_import(store, data, pid, name)
        out.append({"kind": "slot", "key": slot, "what": what, "value": f"{pid} / {name}", "ok": ok,
                    "target": (pid, name),
                    "action": "会换成这个" if ok else "跳过（这边没有这个型号，留服务器默认）"})
    for name, engines in sorted((ch.get("voice") or {}).items()):
        label = voice.LABELS.get(name)
        engines = [str(e) for e in engines or []]
        known = [e for e in engines if e in voice.ENGINE_IDS]
        if label is None:
            out.append({"kind": "voice", "key": name, "what": str(name), "value": " → ".join(engines),
                        "ok": False, "action": "跳过（这边没有这条声音链）"})
        elif not known:
            out.append({"kind": "voice", "key": name, "what": label, "value": " → ".join(engines),
                        "ok": False, "action": "跳过（这几家这边都没接）"})
        else:
            dropped = [e for e in engines if e not in known]
            out.append({"kind": "voice", "key": name, "what": label, "value": " → ".join(known), "ok": True,
                        "engines": known,
                        "action": "会换成这个" + (f"（{'、'.join(dropped)} 这边没接，去掉）" if dropped else "")})
    cm = ch.get("chat_model")
    if cm:
        pid, name, key = cm.get("provider_id"), cm.get("model_name"), cm.get("key")
        ok = _model_after_import(store, data, pid, name)
        out.append({"kind": "chat_model", "key": "chat_model", "what": "聊天模型",
                    "value": f"{pid} / {name}" if pid else str(key), "ok": ok, "target": (pid, name),
                    "action": "会换成这个" if ok else "跳过（这边没有这个型号，用默认主模型）"})
    if ch.get("thinking") is not None:
        out.append({"kind": "thinking", "key": "thinking", "what": "思考", "value": "开" if ch["thinking"] else "关",
                    "ok": True, "on": bool(ch["thinking"]), "action": "会设成这样"})
    return out


def apply_package(store: SettingsStore, keys: KeyStore, data: dict[str, Any], prefs: Any = None) -> dict[str, Any]:
    """写入。种子行不碰；她加的厂商 / 型号覆盖或新增；key 只给这边存在的厂商。"""
    now = _now()
    user_providers = [p for p in data.get("providers") or [] if _is_user_provider(p)]
    with store.transaction() as c:
        for p in user_providers:
            seed_row = c.execute("SELECT source FROM providers WHERE id=?", (p["id"],)).fetchone()
            if seed_row and seed_row["source"] == "seed":
                continue                                # 同名的种子由代码管，不让包覆盖
            c.execute(
                "INSERT OR REPLACE INTO providers(id,label,protocol,base_url,cache_style,auth,key_envs,"
                "source,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'user',?,?,?)",
                (p["id"], p["label"], p["protocol"], p["base_url"], p["cache_style"], p.get("auth") or "api_key",
                 json.dumps(list(p.get("key_envs") or [])), p.get("note") or "", now, now))
        known = {r["id"] for r in c.execute("SELECT id FROM providers")}
        n_models = 0
        for m in data.get("models") or []:
            if not _is_user_model(m) or m["provider_id"] not in known:
                continue
            c.execute("DELETE FROM models WHERE provider_id=? AND model_name=? AND origin!='seed'",
                      (m["provider_id"], m["model_name"]))
            if c.execute("SELECT 1 FROM models WHERE provider_id=? AND model_name=?",
                         (m["provider_id"], m["model_name"])).fetchone():
                continue                                # 这边的代码已经自带这个型号
            short = m.get("short_name")
            if short and c.execute("SELECT 1 FROM models WHERE short_name=?", (short,)).fetchone():
                short = None                            # 短名被占了：型号照导，短名让出来
            c.execute(
                "INSERT INTO models(provider_id,model_name,short_name,label,capabilities,price_json,cache_style,"
                "cache_ttl,origin,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (m["provider_id"], m["model_name"], short, m.get("label") or m["model_name"],
                 json.dumps(m.get("capabilities") or ["chat"]),
                 json.dumps(m["price"]) if m.get("price") else None, m.get("cache_style"), m.get("cache_ttl"),
                 m.get("origin") or "manual", 1 if m.get("enabled", True) else 0, now, now))
            n_models += 1
    incoming = {pid: k for pid, k in (data.get("keys") or {}).items() if pid in known and k}
    if incoming:
        keys.set_many(incoming)
    applied, skipped = apply_choices(store, data, prefs, keys)
    return {"providers": len(user_providers), "models": n_models, "keys": len(incoming), "slots": 0,
            "choices": applied, "skipped": skipped}


def apply_choices(store: SettingsStore, data: dict[str, Any], prefs: Any = None,
                  keys: Any = None) -> tuple[int, list[str]]:
    """厂商 / 型号 / key 写完之后再收她的选择（型号得先在）。`prefs` 是 core：有 set_chat_model / set_thinking。
    返回（收了几条，跳过的「哪一项」）。每条都走各自原来的写入口，「换回去」照样能用。"""
    from settings_store import slots, voice

    applied, skipped = 0, []
    for c in plan_choices(store, data):
        if not c["ok"]:
            skipped.append(c["what"])
            continue
        try:
            if c["kind"] == "slot":
                # 和她在 Slots 里点选同一道门：能力对、没停用、那一家有 key（key 上面刚导进来）
                pid, name = c["target"]
                if not any(o["provider_id"] == pid and o["model_name"] == name
                           for o in slots.options(store, keys, c["key"])):
                    skipped.append(c["what"])
                    continue
                slots._write(store, c["key"], (pid, name), reason="迁移包导入")
            elif c["kind"] == "voice":
                voice.set_chain(store, c["key"], c["engines"])
            elif c["kind"] == "chat_model":
                m = next((m for m in store.models()
                          if (m["provider_id"], m["model_name"]) == tuple(c["target"])), None)
                if prefs is None or m is None or not m["short_name"] or not prefs.set_chat_model(m["short_name"]):
                    skipped.append(c["what"])
                    continue
            elif c["kind"] == "thinking":
                if prefs is None or not prefs.set_thinking(c["on"]):
                    skipped.append(c["what"])
                    continue
        except Exception:  # noqa: BLE001 —— 一条收不进不该让整包回滚（厂商 / key 已经写了），但要说出来
            _log.warning("迁移包里的选择 %s 没收进来", c["what"], exc_info=True)
            skipped.append(c["what"])
            continue
        applied += 1
    return applied, skipped
