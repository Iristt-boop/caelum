"""前端填的 key（配置层 P2b，2026-10-11）。

🔴 规矩（设计稿第十二节）：
  · **不进 settings.db**：单独一个文件 `settings.keys`（同目录，0600）。库会被备份、被拷、被截图，不该因此带出密钥
  · **不进普通备份**：`scripts/caelum-backup.sh` 只按名字收 *.db，这个文件不在清单里；`doctor.sh` 解开最新备份包断言里面没有它
  · **只写不读**：对外只有「配没配」。值只在两处离开这个文件：建 adapter（`profile.apply`）和她主动导出的加密迁移包（`migrate.py`）
  · 日志、接口响应、错误信息里**一个字节都不出**

优先级：这里有就用这里的（她在前端填的），没有才用环境变量（设计稿原则 P2：前端 > 环境变量 > 代码默认）。

格式：JSON `{"<provider_id>": "<key>"}`。写入走「临时文件 + fsync + rename」，写一半断电也不会留下半截文件。
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
from pathlib import Path

logger = logging.getLogger("settings_store.keys")

#: 和 settings.db 同目录
FILENAME = "settings.keys"
_MAX_LEN = 4096


class KeyFileError(RuntimeError):
    """文件坏了。**不许当成空的**：当成空的再写一次，就把她填过的别家 key 全冲掉了。"""


def _check_value(key: str) -> str:
    k = (key or "").strip()
    if not k:
        raise ValueError("key 是空的")
    if len(k) > _MAX_LEN:
        raise ValueError("key 太长了，不像是一把 key")
    if any(ch.isspace() for ch in k):
        raise ValueError("key 中间有空格或换行，多半是粘贴时带进来的")
    return k


class KeyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 读（只给建 adapter / 导出用）

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        self._tighten()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as exc:
            # 不带文件内容（里面是 key）
            raise KeyFileError(f"{self.path.name} 读不出来（{type(exc).__name__}）") from None
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            raise KeyFileError(f"{self.path.name} 的形状不对")
        return {str(k): v for k, v in data.items() if v}

    def get(self, provider_id: str) -> str:
        """拿不到（文件坏了）就当没有 —— 回落到环境变量，同时留痕；不让一个坏文件让他说不了话。"""
        with self._lock:
            try:
                return self._load().get(provider_id, "")
            except KeyFileError:
                logger.warning("key 文件坏了，这次 %s 用环境变量里的 key", provider_id, exc_info=True)
                return ""

    def configured(self) -> set[str]:
        with self._lock:
            try:
                return set(self._load())
            except KeyFileError:
                logger.warning("key 文件坏了，前端看到的「配没配」只算环境变量", exc_info=True)
                return set()

    def export_all(self) -> dict[str, str]:
        """**只给加密迁移包用。** 文件坏了直接抛 —— 导出一个缺了 key 的包比报错更糟。"""
        with self._lock:
            return dict(self._load())

    # ------------------------------------------------------------ 写

    def set(self, provider_id: str, key: str) -> None:
        k = _check_value(key)
        with self._lock:
            data = self._load()
            data[provider_id] = k
            self._write(data)
        logger.info("key 已更新：%s（值不记）", provider_id)

    def set_many(self, keys: dict[str, str]) -> None:
        clean = {pid: _check_value(v) for pid, v in keys.items()}
        with self._lock:
            data = self._load()
            data.update(clean)
            self._write(data)
        logger.info("key 已更新：%s（值不记）", "、".join(sorted(clean)) or "无")

    def clear(self, provider_id: str) -> bool:
        with self._lock:
            data = self._load()
            if provider_id not in data:
                return False
            del data[provider_id]
            self._write(data)
        logger.info("key 已清掉：%s（回到环境变量）", provider_id)
        return True

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        self._tighten()

    def _tighten(self) -> None:
        """权限只给自己。被人 chmod 宽了（或者 umask 宽）就收回来，并留痕。Windows 上 chmod 只管只读位，跳过。"""
        if os.name != "posix":
            return
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:
                os.chmod(self.path, 0o600)
                logger.warning("%s 的权限是 %o，收回成 600", self.path.name, mode)
        except OSError:
            logger.warning("%s 的权限查不了", self.path.name, exc_info=True)
