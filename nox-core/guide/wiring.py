"""从 `Nox` 实例装配地图的数据来源。

放在这里而不是 `nox.py` 里：`nox.py` 只负责「把工具登记进去」，
读哪些现成的东西、怎么取，是地图自己的事。

全部是**只读取值**，而且是**取值函数**不是快照 —— 订单 / 任务仓库在
`api/server.py` 才建（同 luckin 的 `store_ref`），所以这里每次调用才去拿。
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from guide.world_map import MapDeps

logger = logging.getLogger(__name__)

_ROLES = (
    ("primary", "主线（和她聊天）"),
    ("utility", "杂活（意图、总结、记忆整理）"),
    ("vision", "识图（替主线看图）"),
)


def _backend_name(llm) -> str:
    """按 base_url 认是哪家（`LLMConfig` 里没有 backend 字段，PROJECT.md 42.1 同款处理）。"""
    from config import BACKENDS

    if getattr(llm, "provider", "") == "anthropic":
        return "anthropic"
    url = (getattr(llm, "base_url", "") or "").rstrip("/")
    for name, b in BACKENDS.items():
        if b.base_url and b.base_url.rstrip("/") == url:
            return name
    return urlparse(url).netloc or "未知"


def make_deps(nox) -> MapDeps:
    cfg = nox.cfg

    def models_from_store(store) -> list[dict]:
        """P1：模型段改读配置库（和 `/api/nox/config/slots` 同一个视图），不再直接读 cfg。

        这样 P2 切到「库说了算」时，地图自然跟着变；P1 里它与 cfg 一致（P0 的影子账本盯着）。
        """
        from settings_store.seed import SLOT_ATTRS
        from settings_store.views import slots_view

        role_of = {slot: role for (slot, attr), (_, role) in zip(SLOT_ATTRS, _ROLES)}
        rows = []
        for item in slots_view(store)["slots"]:
            if not item["ok"]:
                # 槽位坏了：如实说，别当它不存在
                rows.append({"role": role_of.get(item["slot"], item["slot"]), "backend": "（读不到）",
                             "model": item["error"], "key_configured": False})
                continue
            pid = item["provider"]["id"]
            backend = pid
            if pid.startswith("env-"):                       # 环境变量直接指定的地址：显示主机名
                p = store.provider(pid)
                backend = urlparse((p or {}).get("base_url", "")).netloc or pid
            rows.append({"role": role_of.get(item["slot"], item["slot"]), "backend": backend,
                         "model": item["model"]["name"], "key_configured": item["key_configured"]})
        return rows

    def models() -> list[dict]:
        store = getattr(nox, "settings", None)
        if store is not None:
            try:
                return models_from_store(store)
            except Exception:  # noqa: BLE001 —— 库读不出来就退回 cfg，但要留痕
                logger.warning("地图读配置库失败，退回读 cfg", exc_info=True)
        rows = []
        for attr, role in _ROLES:
            llm = getattr(cfg, attr, None)
            if llm is None:
                continue
            rows.append({
                "role": role,
                "backend": _backend_name(llm),
                "model": llm.model,
                "key_configured": bool(llm.api_key),   # 只说配没配，key 本身不出这里
            })
        return rows

    def pending() -> dict[str, int]:
        orders = getattr(nox, "orders", None)
        tasks = getattr(nox, "tasks", None)
        if orders is None or tasks is None:
            # 仓库在 api/server.py 才建；还没建好就老实说取不到，不要报 0 骗人
            raise LookupError("订单 / 任务仓库还没建好")
        return {"orders": orders.count_pending(), "tasks": tasks.count_waiting_for_her()}

    def health() -> dict:
        from obs import heartbeat

        # `ok` 在这里的含义是「Core 在应答」——这个工具正在被调用，本身就是证据。
        # 和 `/health` 返回的字段同源（主模型名 + 后台停摆的活），但不 import api/server。
        return {
            "ok": True,
            "model": cfg.primary.model,
            "background_stale": heartbeat.stale_jobs(),
        }

    return MapDeps(
        loop=nox.loop,
        context_registry=getattr(nox, "context", None),
        models=models,
        pending=pending,
        health=health,
        # 桌面端页面表在 nox-app（独立仓库，VPS 上没有）。要接得走 bridge，另开一件事。
        os_pages=None,
    )
