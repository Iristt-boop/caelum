"""启动钩子：`api/server.py` 在建 orders / tasks 库的旁边调一行。

🔴 **永远不许让启动因为它失败。** P0 是观察，不是功能：任何异常都只留一条 warning（带堆栈）
就返回，Core 照常起来（`docs/LOGGING.md`：每个 catch 必须留痕，禁止静默失败）。

关掉它：`NOX_CONFIG_SHADOW=off`（也认 0 / false）—— 连库带档案一起关。
只关档案（库照灌、影子照对、P1 视图照看，adapter 回到名字嗅探）：`NOX_CONFIG_STORE=off`。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("settings_store.startup")

_OFF = ("off", "0", "false", "no")


def start_shadow(core: Any) -> None:
    from agent import adapters
    adapters.use_profiles(None)          # 先回老路：下面哪一步没走到，都不该留着上一次的档案
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
        res = shadow.run(store, cfg, report)
        #: P1 的只读视图从这儿取
        core.settings = store
        #: P2b：她在前端填的 key（0600 文件，不在库里）。档案没装时也挂上 —— 接口照样能写、能导出
        from settings_store.keys import FILENAME, KeyStore
        keys = KeyStore(Path(cfg.db_path).parent / FILENAME)
        core.config_keys = keys

        # P2a：adapter 改读档案（缓存怎么打、能不能看图）。对账不过就不让库说话
        from settings_store import profile
        if not profile.enabled():
            logger.info("NOX_CONFIG_STORE=off，adapter 照旧按名字判断")
        elif not res.ok:
            logger.warning("配置库和现状对不上（%d 处），这次 adapter 不读档案、照旧按名字判断", len(res.diffs))
        else:
            n = profile.attach(core, store, keys)
            logger.info("adapter 改读档案：已套 %d 个，之后新建的在工厂里套", n)
    except Exception:  # noqa: BLE001 —— 观察不许拖垮启动，但必须留痕
        logger.warning("配置影子失败（不影响启动）", exc_info=True)
