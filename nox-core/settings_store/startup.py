"""启动钩子：`api/server.py` 在建 orders / tasks 库的旁边调一行。

🔴 **永远不许让启动因为它失败。** P0 是观察，不是功能：任何异常都只留一条 warning（带堆栈）
就返回，Core 照常起来（`docs/LOGGING.md`：每个 catch 必须留痕，禁止静默失败）。

关掉它：`NOX_CONFIG_SHADOW=off`（也认 0 / false）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("settings_store.startup")

_OFF = ("off", "0", "false", "no")


def start_shadow(core: Any) -> None:
    try:
        if (os.environ.get("NOX_CONFIG_SHADOW") or "").strip().lower() in _OFF:
            logger.info("NOX_CONFIG_SHADOW=off，跳过配置影子")
            return

        from config import Config

        cfg = getattr(core, "cfg", None)
        if not isinstance(cfg, Config):
            # 单测里的假 cfg 没有这些字段；真线上 cfg 一定是 Config
            logger.info("cfg 不是真正的 Config，跳过配置影子")
            return

        from settings_store import shadow
        from settings_store.seed import seed
        from settings_store.store import SettingsStore

        store = SettingsStore(Path(cfg.db_path).parent / "settings.db")
        report = seed(store, cfg)
        shadow.run(store, cfg, report)
        #: P1 的只读视图从这儿取；P0 里没有任何东西读它
        core.settings = store
    except Exception:  # noqa: BLE001 —— 观察不许拖垮启动，但必须留痕
        logger.warning("配置影子失败（不影响启动）", exc_info=True)
